"""crm: the command line."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Group
from rich.live import Live
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.syntax import Syntax
from rich.text import Text

from . import config as config_mod
from . import conflict, pdf, report, termimg, ui
from .paths import cache_dir, config_file, data_file
from .store import Session, Store
from .timeutil import (
    DAY_ABBR, ParseError, add_business_days, fmt_hm, fmt_hours_num, fmt_hrmin, fmt_long, fmt_md, fmt_money, fmt_range,
    fmt_time, now, parse_date, parse_hours, parse_time, parse_week, parse_when, week_start,
)

app = typer.Typer(
    name="crm",
    help="⏱  [bold]crm[/]: clock in/out, log what you did, and ship a weekly log + invoice PDF.\n\nRun [b]crm[/] on its own for the full-screen app.",
    rich_markup_mode="rich",
    no_args_is_help=False,
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

WEEK_HELP = "Which week: [b]this[/], [b]last[/], [b]-2[/], or any date in it (10/14, 2026-10-14)."


# ── helpers ──────────────────────────────────────────────────────────────────

def _load() -> tuple[Store, dict]:
    cfg = config_mod.load()
    ui.set_theme(cfg["ui"].get("theme", "catppuccin"))
    return Store.load(), cfg


def _fail(msg: str, code: int = 1):
    ui.err(msg)
    raise typer.Exit(code)


def _ensure_unlocked(store: Store, monday: date) -> None:
    if store.is_approved(monday):
        _fail(
            f"The week of {fmt_md(monday)} is approved and locked. "
            f"Run [b]crm unapprove -w {monday.isoformat()}[/] to change it."
        )


def _check_overlap(store: Store, start: datetime, end: datetime | None, exclude: int | None = None) -> None:
    o = store.overlap(start, end, exclude)
    if o:
        _fail(f"That overlaps the block on {ui.block_label(o)}.")


def _pick_week(store: Store, week: str | None) -> date:
    """Explicit week wins. Otherwise this week, or last week if this one is still empty."""
    if week is not None:
        return parse_week(week)
    cur = week_start(date.today())
    if not store.in_week(cur) and store.in_week(cur - timedelta(weeks=1)):
        return cur - timedelta(weeks=1)
    return cur


def _tty() -> bool:
    return sys.stdin.isatty()


def _ask_notes(prompt: str = "What did you work on?") -> list[str]:
    if not _tty():
        return []
    ui.console.print(Text(f"  {prompt}", style=f"bold {ui.T.accent}"),
                     Text("(one per line, empty line to finish)", style=ui.T.dim))
    notes = []
    while True:
        try:
            line = ui.console.input(Text("  › ", style=ui.T.accent)).strip()
        except EOFError:
            break
        if not line:
            break
        notes.append(line)
    return notes


def _prefilled(label: str, value: str) -> str:
    """Ask for a value with the current one already typed in, ready to edit."""
    try:
        import readline
    except ImportError:  # no readline (Windows): fall back to a plain prompt
        readline = None
    if readline:
        readline.set_startup_hook(lambda: readline.insert_text(value))
    try:
        return ui.console.input(Text.assemble((f"  {label:>8} ", ui.T.muted), ("› ", ui.T.accent))).strip()
    except EOFError:
        return value
    finally:
        if readline:
            readline.set_startup_hook()


def _choose(prompt: str, labels: list[str]) -> int:
    """Numbered menu; returns the 0-based index picked."""
    for n, label in enumerate(labels, 1):
        ui.console.print(Text.assemble((f"  {n}  ", f"bold {ui.T.accent}"), (label, ui.T.text)))
    pick = IntPrompt.ask(Text(f"  {prompt}", style=f"bold {ui.T.text}"),
                         choices=[str(n) for n in range(1, len(labels) + 1)], show_choices=False)
    return pick - 1


def _block_line(s: Session) -> str:
    what = f"   › {s.notes[0]}" + (f" (+{len(s.notes) - 1})" if len(s.notes) > 1 else "") if s.notes else ""
    return f"{ui.block_label(s)}  {fmt_hm(s.minutes())}{what}"


def _pick_block(store: Store, ask_first: bool) -> Session:
    """Let the user choose a block from today or another day this week."""
    today = date.today()
    blocks = store.in_week(week_start(today))
    todays = [s for s in blocks if s.day == today]
    others = [s for s in blocks if s.day != today]
    off = "You're off the clock. " if ask_first else ""
    if not blocks:
        _fail(f"{off}Nothing logged this week. [b]crm in[/] to start a block.")
    if not _tty():
        _fail(f"{off}Run this in a terminal to pick a block.")
    if ask_first:
        ui.info("You're off the clock.")
        if not Confirm.ask(Text("  Add it to an earlier block from today or this week?", style=f"bold {ui.T.text}"),
                           default=True):
            ui.info("Nothing added.")
            raise typer.Exit()
    if todays and others:
        pool = [todays, others][_choose("Which?", ["From today", "From another day this week"])]
    else:
        pool = todays or others
        ui.info("Blocks from today:" if todays else "Nothing from today yet. Blocks from earlier this week:")
    return pool[_choose("Which block?", [_block_line(s) for s in pool])]


def _export_path(cfg: dict, r: report.Report) -> Path:
    return report.export_path(cfg, r)


# ── clocking ─────────────────────────────────────────────────────────────────

@app.command("in")
def clock_in(
    note: Optional[list[str]] = typer.Argument(None, help="What you're starting on (optional)."),
    at: Optional[str] = typer.Option(None, "--at", help="Start time if you forgot: 9:30am, 'yesterday 9pm'."),
):
    """Clock in. [dim]e.g. crm in fixing the scraper[/]"""
    store, _ = _load()
    run = store.running()
    if run:
        _fail(f"Already on the clock since {fmt_time(run.start)}. [b]crm out[/] first.")
    start = parse_when(at) if at else now()
    if start > now() + timedelta(minutes=1):
        _fail("Can't clock in in the future.")
    _ensure_unlocked(store, week_start(start.date()))
    _check_overlap(store, start, None)
    s = store.add(start, None, [" ".join(note)] if note else [])
    store.save()
    ui.console.print(ui.session_card(s, "clocked in", border=ui.T.ok))


@app.command("out")
def clock_out(
    note: Optional[list[str]] = typer.Argument(None, help="What you did (otherwise you'll be asked)."),
    at: Optional[str] = typer.Option(None, "--at", help="End time if you forgot: 5:15pm, 'today 5pm'."),
):
    """Clock out and describe what you did."""
    store, cfg = _load()
    run = store.running()
    if not run:
        _fail("You're not clocked in.")
    end = parse_when(at, default_date=run.start.date()) if at else now()
    if at and end <= run.start and len(at.split()) == 1:
        end += timedelta(days=1)  # worked past midnight
    if end <= run.start:
        _fail(f"End time has to be after the start ({fmt_time(run.start)}).")
    if end > now() + timedelta(minutes=1):
        _fail("Can't clock out in the future.")
    if note:
        run.notes.append(" ".join(note))
    run.end = end
    if not note and sys.stdin.isatty():
        # show the block, ask for anything missing, then confirm it
        ui.console.print(ui.session_card(run, "punch out", border=ui.T.accent2))
        run.notes += _ask_notes("Did you do anything else not listed?" if run.notes else "What did you work on?")
        day = f"{DAY_ABBR[run.day.weekday()]} {fmt_md(run.day)}"
        if not Confirm.ask(Text(f"  Save {day} {fmt_range(run.start, run.end)} ({fmt_hm(run.minutes())})?",
                                style=f"bold {ui.T.text}"), default=True):
            ui.info("Not saved. You're still clocked in.")
            raise typer.Exit()
    store.save()
    ui.console.print(ui.session_card(run, "clocked out", border=ui.T.accent))
    r = report.build(store, cfg, run.week)
    ui.console.print(Text("  ") + ui.totals_line(r))


@app.command()
def add(
    text: Optional[list[str]] = typer.Argument(None, help="The description. Leave it out to type several lines."),
    id: Optional[int] = typer.Option(None, "--id", hidden=True),
):
    """Describe what you're doing in the current block. [dim]e.g. crm add "checked the VPN prefix list"[/]

    Off the clock, it asks which block from today or this week to add to."""
    store, _ = _load()
    if id:
        s = store.get(id)
        if not s:
            _fail(f"No session #{id}.")
    else:
        s = store.running() or _pick_block(store, ask_first=True)
    _ensure_unlocked(store, s.week)
    if text:
        lines = [" ".join(text)]
    else:
        if not _tty():
            _fail("Give a description: [b]crm add \"…\"[/]")
        ui.console.print(ui.session_card(s, "add to block", border=ui.T.accent2, notes_label="what I did"))
        lines = _ask_notes("What else do you want to add to this block?")
        if not lines:
            ui.info("Nothing added.")
            raise typer.Exit()
    s.notes += lines
    store.save()
    what = "Description" if len(lines) == 1 else f"{len(lines)} descriptions"
    ui.ok(f"{what} added to {ui.block_label(s)}")


@app.command()
def edit(
    id: Optional[int] = typer.Argument(None, hidden=True),
    day: Optional[str] = typer.Option(None, "--date", "-d", help="Move to another date."),
    start: Optional[str] = typer.Option(None, "--start", "-s", help="New start time."),
    end: Optional[str] = typer.Option(None, "--end", "-e", help="New end time."),
    notes: Optional[list[str]] = typer.Option(None, "--note", "-n", help="Replace notes (repeat for several lines)."),
    add_note: Optional[list[str]] = typer.Option(None, "--add-note", "-a", help="Append a note line."),
):
    """Edit a block: its date, times and what you did. Asks which block from today or this week."""
    store, _ = _load()
    if id is None:
        s = _pick_block(store, ask_first=False)
    else:
        s = store.get(id)
        if not s:
            _fail(f"No session #{id}.")
    _ensure_unlocked(store, s.week)

    interactive = not any([day, start, end, notes, add_note]) and _tty()

    def ask_times(d0: str, st0: str, en0: str | None) -> tuple[str | None, str | None, str | None]:
        ui.info("Edit in place and press enter.")
        # a block from today stays on today; only older blocks can move to another date
        d = None if s.day == date.today() else (_prefilled("date", d0) or None)
        st = _prefilled("start", st0) or None
        en = (_prefilled("end", en0) or None) if en0 is not None else None
        return d, st, en

    if interactive:
        ui.console.print(ui.session_card(s, "editing", notes_label="what I did"))
        parts = ["time", "what I did"]
        first = parts[_choose("What do you want to edit?", ["Time", "What I did"])]
        todo = [first]
        while todo:
            part = todo.pop()
            if part == "time":
                day, start, end = ask_times(s.day.isoformat(), fmt_time(s.start),
                                            None if s.running else fmt_time(s.end))
            else:
                ui.info("Edit in place and press enter. Clear a line to remove it.")
                if s.notes:
                    ui.console.print(Text("  what I did", style=f"bold {ui.T.accent}"))
                notes = [n for n in (_prefilled("", old) for old in s.notes) if n]
                notes += _ask_notes("What else do you want to add to this block?")
            if part == first:
                other = parts[1 - parts.index(first)]
                if Confirm.ask(Text(f"  Edit {'the time' if other == 'time' else 'what you did'} too?",
                                    style=f"bold {ui.T.text}"), default=False):
                    todo.append(other)

    new_notes = list(notes) if notes is not None else list(s.notes)
    new_notes += list(add_note or [])

    while True:
        d = parse_date(day) if day else s.day
        new_start = datetime.combine(d, parse_time(start) if start else s.start.time())
        new_end = s.end
        if s.end is not None:
            new_end = datetime.combine(d, parse_time(end) if end else s.end.time())
            if new_end <= new_start:
                new_end += timedelta(days=1)
        elif end:
            _fail("That session is still running. Use [b]crm out --at[/] to end it.")
        _ensure_unlocked(store, week_start(d))
        if not (interactive and new_end and store.overlap(new_start, new_end, s.id)):
            break
        how = _resolve_overlap(store, new_start, new_end, s.id)
        if how == "back":
            day, start, end = ask_times(d.isoformat(), fmt_time(new_start), fmt_time(new_end))
            continue
        rid = conflict.apply(store, how, new_start, new_end, new_notes, s.id)
        ui.console.print(ui.session_card(store.get(rid), "updated", border=ui.T.ok, notes_label="what I did"))
        return

    _check_overlap(store, new_start, new_end, exclude=s.id)
    s.start, s.end, s.notes = new_start, new_end, new_notes
    store.save()
    ui.console.print(ui.session_card(s, "updated", border=ui.T.ok, notes_label="what I did"))


_FIXES = {"merge": "Merge them", "trim": "Fit this one into the gap", "shorten": "Shorten the other block",
          "replace": "Replace the other block"}


def _resolve_overlap(store: Store, st: datetime, en: datetime, sid: int) -> str:
    """The new times run into another block: offer ways to fix it. Returns a conflict fix or 'back'."""
    ui.warn(f"{fmt_range(st, en)} overlaps:")
    for o in conflict.overlapping(store, st, en, sid):
        ui.console.print(Text.assemble((f"    {ui.block_label(o)}", f"bold {ui.T.warn}"),
                                       (f"  {' · '.join(o.notes)}" if o.notes else "", ui.T.muted)))
    opts = conflict.options(store, st, en, sid)
    keys = list(opts) + ["back", "cancel"]
    labels = [f"{_FIXES[k]}  {opts[k]}" for k in opts] + ["Change the times", "Cancel (keep the block as it was)"]
    how = keys[_choose("How do you want to fix it?", labels)]
    if how == "cancel":
        ui.info("Nothing changed.")
        raise typer.Exit()
    return how


# ── viewing ──────────────────────────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context):
    """No command: open the full-screen app (or just print the dashboard when not in a terminal)."""
    if ctx.invoked_subcommand is not None:
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _show_dashboard()
        return
    from .tui import run_tui
    run_tui()




def _show_dashboard(week: str | None = None) -> None:
    store, cfg = _load()
    ui.console.print(ui.dashboard(store, report.build(store, cfg, parse_week(week)), cfg))
    run = store.running()
    if run and run.minutes() > 12 * 60:
        ui.warn(f"You've been clocked in for {fmt_hm(run.minutes())}. Forgot to clock out? "
                "[b]crm out --at 5pm[/]")


@app.command()
def status(
    watch: bool = typer.Option(False, "--watch", "-W", help="Live-updating dashboard (ctrl-c to quit)."),
    week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP),
):
    """On or off the clock, hours today, and the week."""
    store, cfg = _load()
    monday = parse_week(week)
    if not watch:
        _show_dashboard(week=week)
        return
    try:
        with Live(console=ui.console, screen=True, auto_refresh=False) as live:
            while True:
                store = Store.load()
                live.update(Group(ui.dashboard(store, report.build(store, cfg, monday), cfg, live=True),
                                  Text("  ctrl-c to quit", style=ui.T.faint)), refresh=True)
                _time.sleep(1)
    except KeyboardInterrupt:
        pass


# ── quick views ──────────────────────────────────────────────────────────────

@app.command("v-today")
def view_today(day: Optional[str] = typer.Argument(None, help="Another day instead: yesterday, tue, 10/14.")):
    """View today's blocks: a timeline plus what you did in each."""
    store, cfg = _load()
    d = parse_date(day) if day else date.today()
    blocks = store.on_day(d)
    title = f"{'today' if d == date.today() else DAY_ABBR[d.weekday()]} · {fmt_md(d)}"
    if not blocks:
        ui.console.print(ui.panel(Text("nothing logged", style=ui.T.dim), title))
        return
    width = min(60, max(20, ui.console.width - 12))
    lo, hi = ui.day_range(blocks)
    total = sum(s.minutes() for s in blocks)
    foot = Text.assemble(("total ", ui.T.dim), (fmt_hm(total), f"bold {ui.T.text}"),
                         ("   ", ""), (f"{len(blocks)} block{'s' if len(blocks) != 1 else ''}", ui.T.dim))
    ui.console.print(ui.panel(Group(ui.timeline_bar(blocks, d, width, lo, hi), ui.timeline_axis(width, lo, hi),
                                    Text(""), ui.blocks_table(blocks), Text(""), foot), title))


@app.command("v-week")
def view_week(week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP)):
    """View a week: hours per day, every block and the totals."""
    store, cfg = _load()
    monday = parse_week(week)
    r = report.build(store, cfg, monday)
    parts = [ui.week_bars(store, monday)]
    if r.days:
        parts += [Text(""), ui.sessions_table(store, monday)]
    parts += [Text(""), ui.totals_line(r)]
    ui.console.print(ui.panel(Group(*parts), f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)}",
                              subtitle=ui.status_chip(store, monday)))


@app.command("v-block")
def view_block(id: Optional[int] = typer.Option(None, "--id", hidden=True)):
    """View the block you're clocked into, or your most recent one."""
    store, _ = _load()
    if id:
        s = store.get(id)
        if not s:
            _fail(f"No session #{id}.")
        title, border = "block", None
    elif (s := store.running()):
        title, border = "on the clock", ui.T.ok
    elif (s := store.last()):
        title, border = "last block (off the clock currently)", None
    else:
        _fail("Nothing logged yet. [b]crm in[/] to start a block.")
    ui.console.print(ui.session_card(s, title, border=border, notes_label="what I did"))
    if not s.notes:
        ui.console.print(Text('  no descriptions yet, add one with  crm add "…"', style=ui.T.dim))


@app.command()
def weeks(n: int = typer.Option(8, "--last", "-n", help="How many weeks back.")):
    """Overview of recent weeks and whether they're approved/exported."""
    from rich import box
    from rich.table import Table

    store, cfg = _load()
    t = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ui.T.accent}", show_edge=False, pad_edge=False)
    for col, j in [("week", "left"), ("hours", "right"), ("billed", "right"), ("amount", "right"),
                   ("invoice", "right"), ("status", "left")]:
        t.add_column(col, justify=j)
    cur = week_start(date.today())
    keys = {cur - timedelta(weeks=i) for i in range(n)} | {s.week for s in store.sessions if s.week >= cur - timedelta(weeks=n)}
    for monday in sorted(keys, reverse=True)[:max(n, 1)]:
        r = report.build(store, cfg, monday)
        empty = not r.days
        style = ui.T.faint if empty else ui.T.text
        t.add_row(
            Text(f"{fmt_md(monday)} – {fmt_md(r.sunday)}", style=style),
            Text(fmt_hm(r.total_minutes) if not empty else "·", style=style),
            Text(fmt_hm(r.billed_minutes) if not empty else "", style=style),
            Text(fmt_money(r.amount) if not empty else "", style=ui.T.ok if not empty else style),
            Text(f"#{r.invoice_number}" if r.approved else "", style=ui.T.dim),
            ui.status_chip(store, monday) if not empty or r.approved else Text(""),
        )
    ui.console.print(ui.panel(t, "weeks"))


# ── preview / approve / export ───────────────────────────────────────────────

@app.command()
def preview(
    week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP),
    page: Optional[int] = typer.Option(None, "--page", "-p", help="Show just page N (bigger)."),
    open_: bool = typer.Option(False, "--open", "-o", help="Open in your PDF viewer instead of the terminal."),
):
    """See what the PDF will look like (watermarked until the week is approved)."""
    store, cfg = _load()
    monday = _pick_week(store, week)
    r = report.build(store, cfg, monday)
    if not r.days:
        _fail(f"Nothing logged for the week of {fmt_md(monday)}.")
    path = pdf.render(r, cfg, cache_dir() / f"preview-{monday.isoformat()}.pdf", preview=not r.approved)
    if not open_ and termimg.supports_inline_images():
        termimg.show_inline(path, [page] if page else None)
    elif sys.stdout.isatty() and not termimg.open_external(path):
        ui.warn("Couldn't find a PDF viewer.")
    ui.info(f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)} · {fmt_hrmin(r.total_minutes)} · "
            f"billed {fmt_hrmin(r.billed_minutes)} · {fmt_money(r.amount)}"
            + ("" if r.approved else " · [i]not approved yet[/]"))
    ui.info(f"[dim]{path}[/]")


@app.command()
def approve(week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP)):
    """Review a week, confirm billed hours + invoice details, and lock it in."""
    store, cfg = _load()
    monday = _pick_week(store, week)
    if store.is_approved(monday):
        r = report.build(store, cfg, monday)
        ui.ok(f"The week of {fmt_md(monday)} is already approved (invoice #{r.invoice_number}). "
              "[b]crm export[/] makes the PDF.")
        return
    run = store.running()
    if run and run.week == monday:
        _fail("You're still clocked in this week. [b]crm out[/] first.")
    r = report.build(store, cfg, monday)
    if not r.days:
        _fail(f"Nothing logged for the week of {fmt_md(monday)}.")

    ui.console.print(ui.panel(Group(ui.sessions_table(store, monday), Text(""), ui.totals_line(r)),
                              f"review · week {fmt_md(r.monday)} – {fmt_md(r.sunday)}", subtitle=ui.status_chip(store, monday)))
    ui.info(f"Worked [b]{fmt_hrmin(r.total_minutes)}[/]. Billing rule '{cfg['invoice']['billing']}' "
            f"gives [b]{fmt_hrmin(r.billed_minutes)}[/].")

    billed = parse_hours(Prompt.ask("  Billed hours", default=fmt_hours_num(r.billed_minutes)))
    role = cfg["invoice"]["role"]
    summary = Prompt.ask(f"  Invoice line [dim](after '{role} -')[/]", default=r.summary or None) or ""
    number = IntPrompt.ask("  Invoice #", default=r.invoice_number)
    issued = parse_date(Prompt.ask("  Issued", default="today"))
    due = add_business_days(issued, int(cfg["invoice"]["due_business_days"]))
    rate = float(cfg["invoice"]["rate"])
    amount = round(rate * billed / 60, 2)

    ui.console.print(ui.panel(ui.kv(
        ("billed", fmt_hrmin(billed)),
        ("rate", f"{fmt_money(rate, cents=False)}/hr"),
        ("amount", Text(fmt_money(amount), style=f"bold {ui.T.ok}")),
        ("invoice", f"#{number}"),
        ("item", f"{role} -\n{summary}" if summary else role),
        ("issued", fmt_long(issued)),
        ("due", fmt_long(due)),
    ), "about to approve", border=ui.T.accent2, padding=(0, 2)))
    if Confirm.ask("  Look at the PDF first?", default=False):
        tmp = report.Report(**{**r.__dict__, "billed_minutes": billed, "summary": summary,
                               "invoice_number": number, "issued": issued, "due": due, "rate": rate})
        path = pdf.render(tmp, cfg, cache_dir() / f"preview-{monday.isoformat()}.pdf", preview=True)
        if termimg.supports_inline_images():
            termimg.show_inline(path)
        else:
            termimg.open_external(path)
    if not Confirm.ask(Text(f"  Approve and lock the week of {fmt_md(monday)}?", style=f"bold {ui.T.text}"), default=True):
        ui.info("Not approved. Nothing changed.")
        raise typer.Exit()

    store.weeks[monday.isoformat()] = {
        "approved_at": datetime.now().isoformat(timespec="seconds"),
        "total_minutes": r.total_minutes,
        "billed_minutes": billed,
        "rate": rate,
        "invoice_number": number,
        "summary": summary,
        "issued": issued.isoformat(),
        "due": due.isoformat(),
        "exports": [],
    }
    store.save()
    ui.ok(f"Approved and locked. Invoice #{number} · {fmt_money(amount)}")
    if Confirm.ask("  Export the PDF now?", default=True):
        export(week=monday.isoformat(), out=None, open_=False)


@app.command()
def unapprove(week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP)):
    """Unlock an approved week so you can edit it again."""
    store, _ = _load()
    monday = _pick_week(store, week)
    if not store.is_approved(monday):
        _fail(f"The week of {fmt_md(monday)} isn't approved.")
    if not Confirm.ask(f"  Unlock the week of {fmt_md(monday)}? (you'll need to approve it again)", default=False):
        raise typer.Exit()
    del store.weeks[monday.isoformat()]
    store.save()
    ui.ok("Unlocked.")


@app.command()
def export(
    week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP),
    out: Optional[Path] = typer.Option(None, "--out", help="Write the PDF here instead of the export dir."),
    open_: bool = typer.Option(False, "--open", "-o", help="Open it afterwards."),
):
    """Write the final PDF for an approved week."""
    store, cfg = _load()
    monday = _pick_week(store, week)
    if not store.is_approved(monday):
        _fail(f"The week of {fmt_md(monday)} isn't approved yet. "
              "Check it with [b]crm preview[/], then run [b]crm approve[/].")
    r = report.build(store, cfg, monday)
    path = (out.expanduser() if out else _export_path(cfg, r)).resolve()
    pdf.render(r, cfg, path, preview=False)
    store.weeks[monday.isoformat()].setdefault("exports", []).append(
        {"path": str(path), "at": datetime.now().isoformat(timespec="seconds")})
    store.save()
    ui.ok(f"Saved [b]{path}[/]")
    if open_:
        termimg.open_external(path)


# ── misc ─────────────────────────────────────────────────────────────────────

@app.command()
def undo(steps: int = typer.Argument(1, help="How many changes to undo.")):
    """Undo your last change (punch in/out, items, edits, approvals…)."""
    _load()
    from . import history
    for _ in range(max(1, steps)):
        label = history.undo()
        if label is None:
            ui.info("Nothing left to undo.")
            break
        ui.ok(f"Undid: {label}")


@app.command()
def redo(steps: int = typer.Argument(1, help="How many changes to redo.")):
    """Redo a change you just undid."""
    _load()
    from . import history
    for _ in range(max(1, steps)):
        label = history.redo()
        if label is None:
            ui.info("Nothing to redo.")
            break
        ui.ok(f"Redid: {label}")


@app.command("cycle-theme")
def cycle_theme():
    """Switch to the next color theme."""
    _, cfg = _load()
    names = list(ui.THEMES)
    cur = cfg["ui"].get("theme", "catppuccin")
    nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
    config_mod.set_value("ui", "theme", nxt)
    ui.set_theme(nxt)
    swatch = Text.assemble(*[("██", c) for c in ui.ramp(6)])
    ui.console.print(Text.assemble(("  theme ", ui.T.dim), (f"{nxt:<12}", f"bold {ui.T.accent}"), swatch,
                                   (f"   ({names.index(nxt) + 1}/{len(names)})", ui.T.dim)))


@app.command()
def config(
    edit_: bool = typer.Option(False, "--edit", "-e", help="Open the config in $EDITOR."),
    path: bool = typer.Option(False, "--path", help="Just print where the files live."),
):
    """Show or edit your details (name, client, rate, theme…)."""
    _load()  # creates the file on first run
    f = config_file()
    if path:
        ui.console.print(f"config  {f}\ndata    {data_file()}\ncache   {cache_dir()}")
        return
    if edit_:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("nano" if shutil.which("nano") else "vi")
        subprocess.call([editor, str(f)])
        try:
            config_mod.load()
            ui.ok("Config saved.")
        except Exception as e:  # noqa: BLE001
            ui.err(f"Config has a problem: {e}")
        return
    ui.console.print(ui.panel(Syntax(f.read_text(), "toml", theme="ansi_dark", background_color="default"),
                              "config", subtitle=Text(str(f), style=ui.T.dim), padding=(0, 1)))


def run():
    try:
        app()
    except ParseError as e:
        ui.err(str(e))
        sys.exit(2)
