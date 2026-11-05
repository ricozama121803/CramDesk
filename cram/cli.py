"""cram: the command line."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

import typer
from rich.console import Group
from rich.live import Live
from rich.prompt import Confirm, IntPrompt
from rich.syntax import Syntax
from rich.text import Text

from . import config as config_mod
from . import conflict, report, ui
from .paths import cache_dir, config_file, data_file
from .store import Session, Store
from .timeutil import (
    DAY_ABBR, ParseError, fmt_hm, fmt_md, fmt_range,
    fmt_time, now, parse_date, parse_time, parse_week, parse_when, week_start,
)

app = typer.Typer(
    name="cram",
    help="⏱  [bold]cram[/]: track your study hours.\n\n"
         "[b]cram start[/] statics · [b]cram stop[/] · [b]cram add[/] details/note/block · "
         "[b]cram create class[/] · [b]cram ls-class[/] · [b]cram ls-note[/]\n\n"
         "Run [b]cram[/] on its own for the app, which does everything else.",
    rich_markup_mode="rich",
    no_args_is_help=False,
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

WEEK_HELP = "Which week: [b]this[/], [b]last[/], [b]next[/], [b]-2[/], or any date in it (10/14, 2026-10-14)."


# ── tab completion ───────────────────────────────────────────────────────────
# Each function gets what's been typed so far and returns suggestions for the word under the cursor.
# They must never print or fail: anything going wrong just means no suggestions.

def _quiet_store() -> Store | None:
    try:
        return Store.load()
    except Exception:  # noqa: BLE001
        return None


def _class_word_completer(param: str):
    """Complete a class name one word at a time: cram in lin<tab> -> linear, then al<tab> -> algebra.
    `param` is the argument the words go into; what's already typed is in ctx.params."""
    def complete(ctx: typer.Context, incomplete: str):
        store = _quiet_store()
        if not store:
            return []
        typed = [str(w).lower() for w in (ctx.params.get(param) or [])]
        seen, out = set(), []
        for c in store.classes:
            cw = c.lower().split()
            n = len(typed)
            if cw[:n] == typed and len(cw) > n and cw[n].startswith(incomplete.lower()) and cw[n] not in seen:
                seen.add(cw[n])
                out.append((cw[n], c))
        return out
    return complete


def _complete_class_option(incomplete: str):
    """-c / --class: one word that cram will recognize as the class."""
    store = _quiet_store()
    if not store:
        return []
    out = [("none", "no class")] if "none".startswith(incomplete.lower()) else []
    for c in store.classes:
        low = c.lower()
        first = low.split()[0]
        word = low if " " not in low else first
        if store.match_class(word) != (c, "") and " " in low:
            word = "".join(w[0] for w in low.split())       # initials, when the first word is shared
        if store.match_class(word) == (c, "") and word.startswith(incomplete.lower()):
            out.append((word, c))
    return out


def _todo_completer(which: str):
    def complete(incomplete: str):
        store = _quiet_store()
        if not store:
            return []
        mine, older = store.todos_for(week_start(date.today()))
        pool = {"open": [t for t in mine + older if not t.done],
                "done": [t for t in store.todos if t.done],
                "all": mine + older}[which]
        return [(str(t.id), (f"{t.course}: " if t.course else "") + t.text)
                for t in pool if str(t.id).startswith(incomplete)]
    return complete


def _complete_week(incomplete: str):
    words = [("this", "this week"), ("last", "last week"), ("next", "next week"), ("-2", "two weeks ago")]
    return [(w, h) for w, h in words if w.startswith(incomplete.lower())]


def _complete_day(incomplete: str):
    words = [("today", ""), ("yesterday", "")] + [(d, "") for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
    return [(w, h) for w, h in words if w.startswith(incomplete.lower())]


def _complete_session_id(incomplete: str):
    store = _quiet_store()
    if not store:
        return []
    recent = sorted(store.sessions, key=lambda s: s.start, reverse=True)[:15]
    return [(str(s.id), f"{s.course or 'no class'} · {ui.block_label(s)}") for s in recent
            if str(s.id).startswith(incomplete)]


# ── helpers ──────────────────────────────────────────────────────────────────

def _load() -> tuple[Store, dict]:
    cfg = config_mod.load()
    ui.set_theme(cfg["ui"].get("theme", "catppuccin"))
    return Store.load(), cfg


def _fail(msg: str, code: int = 1):
    ui.err(msg)
    raise typer.Exit(code)


def _check_overlap(store: Store, start: datetime, end: datetime | None, exclude: int | None = None) -> None:
    o = store.overlap(start, end, exclude)
    if o:
        _fail(f"That overlaps the session on {ui.block_label(o)}.")


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


def _ask_notes(prompt: str = "What did you cover?") -> list[str]:
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
    """Let the user choose a session from today or another day this week."""
    today = date.today()
    blocks = store.in_week(week_start(today))
    todays = [s for s in blocks if s.day == today]
    others = [s for s in blocks if s.day != today]
    off = "You're not studying right now. " if ask_first else ""
    if not blocks:
        _fail(f"{off}Nothing logged this week. [b]cram start[/] to begin.")
    if not _tty():
        _fail(f"{off}Run this in a terminal to pick a session.")
    if ask_first:
        ui.info("You're not studying right now.")
        if not Confirm.ask(Text("  Add it to an earlier session from today or this week?", style=f"bold {ui.T.text}"),
                           default=True):
            ui.info("Nothing added.")
            raise typer.Exit()
    if todays and others:
        pool = [todays, others][_choose("Which?", ["From today", "From another day this week"])]
    else:
        pool = todays or others
        ui.info("Sessions from today:" if todays else "Nothing from today yet. Sessions from earlier this week:")
    return pool[_choose("Which session?", [_block_line(s) for s in pool])]


def _ambiguous(store: Store, text: str) -> list[str]:
    first = (text.split() or [""])[0].lower()
    return [c for c in store.classes if len(first) >= 3 and c.lower().startswith(first)]


def _class_arg(store: Store, value: str) -> str | None:
    """An explicit --class value: must name one of your classes ('' or 'none' clears it)."""
    if value.strip().lower() in ("", "none"):
        return None
    course, rest = store.match_class(value)
    if course and not rest:
        return course
    hits = _ambiguous(store, value)
    if len(hits) > 1:
        _fail(f"'{value}' could be {' or '.join(hits)}. Type more of the name.")
    _fail(f"'{value}' isn't one of your classes. Create it with [b]cram create class {value}[/].")


def _split_class(store: Store, text: str) -> tuple[str | None, str]:
    """What you typed after 'in' -> (class, note). Offers to add a new class when nothing matches."""
    course, rest = store.match_class(text)
    if course or not text:
        return course, rest
    hits = _ambiguous(store, text)
    if len(hits) > 1:
        _fail(f"'{text.split()[0]}' could be {' or '.join(hits)}. Type more of the name.")
    if _tty():
        if Confirm.ask(Text(f"  '{text}' isn't one of your classes. Add it as a class?", style=f"bold {ui.T.text}"),
                       default=True):
            return store.add_class(text), ""
        return None, text
    return None, text


# ── clocking ─────────────────────────────────────────────────────────────────

@app.command("in", hidden=True)
def clock_in(
    note: Optional[list[str]] = typer.Argument(None, help="The class, then anything else about it (optional).",
                                                autocompletion=_class_word_completer("note")),
    at: Optional[str] = typer.Option(None, "--at", help="Start time if you forgot: 9:30am, 'yesterday 9pm'."),
    course: Optional[str] = typer.Option(None, "--class", "-c", help="The class, if you'd rather name it here.",
                                          autocompletion=_complete_class_option),
):
    """Start studying. [dim]e.g. cram in linear algebra[/] or [dim]cram in la ch 5 review[/]"""
    store, _ = _load()
    run = store.running()
    if run:
        _fail(f"Already studying since {fmt_time(run.start)}. [b]cram stop[/] first.")
    start = parse_when(at) if at else now()
    if start > now() + timedelta(minutes=1):
        _fail("Can't start in the future.")
    _check_overlap(store, start, None)
    text = " ".join(note or [])
    if course is not None:
        cls = _class_arg(store, course)
    else:
        cls, text = _split_class(store, text)
    s = store.add(start, None, [text] if text else [], cls)
    store.save()
    ui.console.print(ui.session_card(s, "studying", border=ui.T.ok))


@app.command("out", hidden=True)
def clock_out(
    note: Optional[list[str]] = typer.Argument(None, help="What you covered (otherwise you'll be asked)."),
    at: Optional[str] = typer.Option(None, "--at", help="End time if you forgot: 5:15pm, 'today 5pm'."),
):
    """Stop studying and note what you covered."""
    store, cfg = _load()
    run = store.running()
    if not run:
        _fail("You're not studying right now.")
    end = parse_when(at, default_date=run.start.date()) if at else now()
    if at and end <= run.start and len(at.split()) == 1:
        end += timedelta(days=1)  # worked past midnight
    if end <= run.start:
        _fail(f"End time has to be after the start ({fmt_time(run.start)}).")
    if end > now() + timedelta(minutes=1):
        _fail("Can't stop in the future.")
    if note:
        run.notes.append(" ".join(note))
    run.end = end
    if not note and sys.stdin.isatty():
        # show the block, ask for anything missing, then confirm it
        ui.console.print(ui.session_card(run, "stop session", border=ui.T.accent2))
        run.notes += _ask_notes("Did you cover anything else?" if run.notes else "What did you cover?")
        day = f"{DAY_ABBR[run.day.weekday()]} {fmt_md(run.day)}"
        if not Confirm.ask(Text(f"  Save {day} {fmt_range(run.start, run.end)} ({fmt_hm(run.minutes())})?",
                                style=f"bold {ui.T.text}"), default=True):
            ui.info("Not saved. You're still studying.")
            raise typer.Exit()
    store.save()
    ui.console.print(ui.session_card(run, "session saved", border=ui.T.accent))
    r = report.build(store, cfg, run.week)
    ui.console.print(Text("  ") + ui.totals_line(r))


@app.command(hidden=True)
def edit(
    id: Optional[int] = typer.Argument(None, hidden=True, autocompletion=_complete_session_id),
    day: Optional[str] = typer.Option(None, "--date", "-d", help="Move to another date.", autocompletion=_complete_day),
    start: Optional[str] = typer.Option(None, "--start", "-s", help="New start time."),
    end: Optional[str] = typer.Option(None, "--end", "-e", help="New end time."),
    notes: Optional[list[str]] = typer.Option(None, "--note", "-n", help="Replace notes (repeat for several lines)."),
    add_note: Optional[list[str]] = typer.Option(None, "--add-note", "-a", help="Append a note line."),
    course: Optional[str] = typer.Option(None, "--class", "-c", help="Change the class ('none' clears it).",
                                          autocompletion=_complete_class_option),
):
    """Edit a session: its date, times and what you covered. Asks which session from today or this week."""
    store, _ = _load()
    if id is None:
        s = _pick_block(store, ask_first=False)
    else:
        s = store.get(id)
        if not s:
            _fail(f"No session #{id}.")

    interactive = not any([day, start, end, notes, add_note]) and course is None and _tty()
    new_class = s.course if course is None else _class_arg(store, course)

    def ask_times(d0: str, st0: str, en0: str | None) -> tuple[str | None, str | None, str | None]:
        ui.info("Edit in place and press enter.")
        # a block from today stays on today; only older blocks can move to another date
        d = None if s.day == date.today() else (_prefilled("date", d0) or None)
        st = _prefilled("start", st0) or None
        en = (_prefilled("end", en0) or None) if en0 is not None else None
        return d, st, en

    if interactive:
        ui.console.print(ui.session_card(s, "editing", notes_label="what I covered"))
        parts = ["time", "what I covered"]
        pick = _choose("What do you want to edit?", ["Time", "What I covered", "Class"])
        if pick == 2:
            s.course = _pick_class(store, s.course)
            store.save()
            ui.console.print(ui.session_card(s, "updated", border=ui.T.ok, notes_label="what I covered"))
            return
        first = parts[pick]
        todo = [first]
        while todo:
            part = todo.pop()
            if part == "time":
                day, start, end = ask_times(s.day.isoformat(), fmt_time(s.start),
                                            None if s.running else fmt_time(s.end))
            else:
                ui.info("Edit in place and press enter. Clear a line to remove it.")
                if s.notes:
                    ui.console.print(Text("  what I covered", style=f"bold {ui.T.accent}"))
                notes = [n for n in (_prefilled("", old) for old in s.notes) if n]
                notes += _ask_notes("What else do you want to add to this session?")
            if part == first:
                other = parts[1 - parts.index(first)]
                if Confirm.ask(Text(f"  Edit {'the time' if other == 'time' else 'what you covered'} too?",
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
            _fail("That session is still running. Use [b]cram stop --at[/] to end it.")
        if not (interactive and new_end and store.overlap(new_start, new_end, s.id)):
            break
        how = _resolve_overlap(store, new_start, new_end, s.id)
        if how == "back":
            day, start, end = ask_times(d.isoformat(), fmt_time(new_start), fmt_time(new_end))
            continue
        rid = conflict.apply(store, how, new_start, new_end, new_notes, s.id)
        store.get(rid).course = new_class
        store.save()
        ui.console.print(ui.session_card(store.get(rid), "updated", border=ui.T.ok, notes_label="what I covered"))
        return

    _check_overlap(store, new_start, new_end, exclude=s.id)
    s.start, s.end, s.notes, s.course = new_start, new_end, new_notes, new_class
    store.save()
    ui.console.print(ui.session_card(s, "updated", border=ui.T.ok, notes_label="what I covered"))


def _pick_class(store: Store, current: str | None) -> str | None:
    if not store.classes:
        _fail("You don't have any classes yet. Create one with [b]cram create class …[/].")
    labels = [c + ("   (current)" if c == current else "") for c in store.classes] + ["No class"]
    i = _choose("Which class?", labels)
    return store.classes[i] if i < len(store.classes) else None


_FIXES = {"merge": "Merge them", "trim": "Fit this one into the gap", "shorten": "Shorten the other session",
          "replace": "Replace the other session"}


def _resolve_overlap(store: Store, st: datetime, en: datetime, sid: int) -> str:
    """The new times run into another session: offer ways to fix it. Returns a conflict fix or 'back'."""
    ui.warn(f"{fmt_range(st, en)} overlaps:")
    for o in conflict.overlapping(store, st, en, sid):
        ui.console.print(Text.assemble((f"    {ui.block_label(o)}", f"bold {ui.T.warn}"),
                                       (f"  {' · '.join(o.notes)}" if o.notes else "", ui.T.muted)))
    opts = conflict.options(store, st, en, sid)
    keys = list(opts) + ["back", "cancel"]
    labels = [f"{_FIXES[k]}  {opts[k]}" for k in opts] + ["Change the times", "Cancel (keep the session as it was)"]
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
        ui.warn(f"You've been studying for {fmt_hm(run.minutes())}. Forgot to stop? "
                "[b]cram stop --at 5pm[/]")


@app.command(hidden=True)
def status(
    watch: bool = typer.Option(False, "--watch", "-W", help="Live-updating dashboard (ctrl-c to quit)."),
    week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP, autocompletion=_complete_week),
):
    """Studying or not, hours today, and the week."""
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

@app.command("v-today", hidden=True)
def view_today(day: Optional[str] = typer.Argument(None, help="Another day instead: yesterday, tue, 10/14.",
                                                        autocompletion=_complete_day)):
    """View today's sessions: a timeline plus what you covered in each."""
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
                         ("   ", ""), (f"{len(blocks)} session{'s' if len(blocks) != 1 else ''}", ui.T.dim))
    ui.console.print(ui.panel(Group(ui.timeline_bar(blocks, d, width, lo, hi), ui.timeline_axis(width, lo, hi),
                                    Text(""), ui.class_bars(blocks), Text(""), ui.blocks_table(blocks), Text(""), foot), title))


@app.command("v-week", hidden=True)
def view_week(week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP, autocompletion=_complete_week)):
    """View a week: hours per day, every session and the totals."""
    store, cfg = _load()
    monday = parse_week(week)
    r = report.build(store, cfg, monday)
    parts = [ui.week_bars(store, monday)]
    if r.days:
        parts += [Text(""), Text("by class", style=f"bold {ui.T.accent}"), ui.class_bars(store.in_week(monday)),
                  Text(""), ui.sessions_table(store, monday)]
    parts += [Text(""), ui.totals_line(r)]
    mine, older = store.todos_for(monday)
    if mine or older:
        parts += [Text(""), Text("notes", style=f"bold {ui.T.accent}"), ui.todo_list(store, monday)]
    ui.console.print(ui.panel(Group(*parts), f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)}"))


@app.command("v-block", hidden=True)
def view_block(id: Optional[int] = typer.Option(None, "--id", hidden=True)):
    """View the session you're studying in, or your most recent one."""
    store, _ = _load()
    if id:
        s = store.get(id)
        if not s:
            _fail(f"No session #{id}.")
        title, border = "session", None
    elif (s := store.running()):
        title, border = "studying", ui.T.ok
    elif (s := store.last()):
        title, border = "last session (not studying right now)", None
    else:
        _fail("Nothing logged yet. [b]cram start[/] to begin.")
    ui.console.print(ui.session_card(s, title, border=border, notes_label="what I covered"))
    if not s.notes:
        ui.console.print(Text('  no details yet, add some with  cram add details "…"', style=ui.T.dim))


@app.command(hidden=True)
def weeks(n: int = typer.Option(8, "--last", "-n", help="How many weeks back.")):
    """Hours studied in each of your recent weeks."""
    from rich import box
    from rich.table import Table

    store, cfg = _load()
    t = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ui.T.accent}", show_edge=False, pad_edge=False)
    for col, j in [("week", "left"), ("hours", "right"), ("days", "right")]:
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
            Text(str(len(r.days)) if not empty else "", style=ui.T.dim),
        )
    ui.console.print(ui.panel(t, "weeks"))


# ── classes ──────────────────────────────────────────────────────────────────

class_app = typer.Typer(help="Your classes: [b]cram class add Linear Algebra[/], rm, rename, or just [b]cram class[/] to list them.",
                        rich_markup_mode="rich", invoke_without_command=True, no_args_is_help=False)
app.add_typer(class_app, name="class", hidden=True)


@class_app.callback()
def _class_root(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        _list_classes()


@app.command("classes", hidden=True)
def list_classes():
    """List your classes with hours this week and in total."""
    _list_classes()


def _list_classes() -> None:
    store, _ = _load()
    if not store.classes and not store.sessions:
        ui.info("No classes yet. Create one with [b]cram create class Statics[/] "
                "(several at once: [b]cram create class Statics, Calc III[/]).")
        return
    ui.console.print(ui.panel(ui.classes_table(store), "classes"))


@class_app.command("list")
@class_app.command("ls", hidden=True)
def class_list():
    """List your classes (same as plain [b]cram class[/])."""
    _list_classes()


@class_app.command("add")
def class_add(name: list[str] = typer.Argument(..., help="The class name. Separate several with commas.")):
    """Add a class. [dim]e.g. cram class add Linear Algebra, Chemistry[/]"""
    store, _ = _load()
    added, had = [], []
    for part in " ".join(name).split(","):
        if part.strip():
            (had if store.find_class(part) else added).append(store.add_class(part))
    if not added and not had:
        _fail("Give the class a name: [b]cram create class Statics[/]")
    store.save()
    for c in added:
        ui.ok(f"Created [b]{c}[/]. Start with [b]cram start {c.lower()}[/]")
    for c in had:
        ui.info(f"[b]{c}[/] is already one of your classes.")


@class_app.command("rm")
@class_app.command("remove", hidden=True)
@class_app.command("delete", hidden=True)
def class_rm(name: list[str] = typer.Argument(..., help="The class to remove.",
                                                   autocompletion=_class_word_completer("name"))):
    """Remove a class from your list. Past sessions keep it, so no hours are lost."""
    store, _ = _load()
    c = _class_arg(store, " ".join(name))
    if c is None:
        _fail("Which class? [b]cram class rm Chemistry[/]")
    store.remove_class(c)
    store.save()
    n = sum(1 for s in store.sessions if s.course == c)
    ui.ok(f"Removed [b]{c}[/]." + (f" Its {n} past session{'s' if n != 1 else ''} keep the class." if n else ""))


@class_app.command("rename")
def class_rename(old: str = typer.Argument(..., help="Current name (quote it if it has spaces)."),
                 new: str = typer.Argument(..., help="New name (quote it if it has spaces).")):
    """Rename a class, including on past sessions. [dim]e.g. cram class rename "Lin Alg" "Linear Algebra"[/]"""
    store, _ = _load()
    try:
        name = store.rename_class(_class_arg(store, old) or old, new)
    except ValueError as e:
        _fail(str(e).capitalize() + ".")
    store.save()
    ui.ok(f"Renamed to [b]{name}[/].")


# ── to-dos ───────────────────────────────────────────────────────────────────

todo_app = typer.Typer(help="Notes to self for the week (no reminders). [b]cram todo add finish problem set 4[/], "
                            "done, uncheck, rm, or just [b]cram todo[/] to see the list.",
                       rich_markup_mode="rich", invoke_without_command=True, no_args_is_help=False)
app.add_typer(todo_app, name="todo", hidden=True)


@todo_app.callback()
def _todo_root(ctx: typer.Context, week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP, autocompletion=_complete_week)):
    if ctx.invoked_subcommand is None:
        _show_todos(parse_week(week))


def _show_todos(monday: date) -> None:
    store, _ = _load()
    title = f"notes · week {fmt_md(monday)} – {fmt_md(monday + timedelta(days=6))}"
    ui.console.print(ui.panel(ui.todo_list(store, monday), title, subtitle=ui.todo_summary(store, monday) or None))


def _todo_ids(store: Store, ids: list[int]):
    found = []
    for i in ids:
        t = store.get_todo(i)
        if not t:
            _fail(f"No note #{i}. [b]cram ls-note[/] shows the numbers.")
        found.append(t)
    return found


@todo_app.command("list")
@todo_app.command("ls", hidden=True)
def todo_list(week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP, autocompletion=_complete_week)):
    """List the week's to-dos (same as plain [b]cram todo[/])."""
    _show_todos(parse_week(week))


@todo_app.command("add")
def todo_add(text: list[str] = typer.Argument(..., help="What to do. Start with a class to tag it: la problem set 4.",
                                            autocompletion=_class_word_completer("text")),
             week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP, autocompletion=_complete_week)):
    """Add a to-do for this week (or another with -w). [dim]e.g. cram todo add chem read ch 7[/]"""
    store, _ = _load()
    monday = parse_week(week)
    try:
        t = store.add_todo(" ".join(text), monday)
    except ValueError:
        _fail('Give it some text: [b]cram todo add "finish problem set 4"[/]')
    store.save()
    ui.console.print(Text("  ") + ui.todo_line(t))


@todo_app.command("done")
def todo_done(ids: list[int] = typer.Argument(..., help="The to-do number(s), from cram todo.",
                                               autocompletion=_todo_completer("open"))):
    """Check off to-dos. [dim]e.g. cram todo done 2 3[/]"""
    store, _ = _load()
    for t in _todo_ids(store, ids):
        t.done = True
        ui.console.print(Text("  ") + ui.todo_line(t))
    store.save()


@todo_app.command("uncheck")
@todo_app.command("undo", hidden=True)          # the old name
def todo_uncheck(ids: list[int] = typer.Argument(..., help="The to-do number(s) to un-check.",
                                               autocompletion=_todo_completer("done"))):
    """Un-check to-dos you checked off by mistake. [dim](cram undo is different: it undoes your last change)[/]"""
    store, _ = _load()
    for t in _todo_ids(store, ids):
        t.done = False
        ui.console.print(Text("  ") + ui.todo_line(t))
    store.save()


@todo_app.command("rm")
@todo_app.command("remove", hidden=True)
@todo_app.command("delete", hidden=True)
def todo_rm(ids: list[int] = typer.Argument(..., help="The to-do number(s) to delete.",
                                             autocompletion=_todo_completer("all"))):
    """Delete to-dos."""
    store, _ = _load()
    for t in _todo_ids(store, ids):
        store.remove_todo(t.id)
        ui.ok(f"Removed “{t.text}”")
    store.save()


@todo_app.command("clear")
def todo_clear(week: Optional[str] = typer.Option(None, "--week", "-w", help=WEEK_HELP, autocompletion=_complete_week)):
    """Delete the checked-off to-dos for a week."""
    store, _ = _load()
    monday = parse_week(week)
    gone = [t for t in store.todos if t.week == monday and t.done]
    for t in gone:
        store.remove_todo(t.id)
    store.save()
    ui.ok(f"Cleared {len(gone)} checked-off to-do{'s' if len(gone) != 1 else ''}.")


# ── the commands you see ─────────────────────────────────────────────────────
# cram start · stop · add details/note/block · create class · ls-class · ls-note.
# Everything else lives in the app (plain `cram`). Older names still work but are hidden.

def _ask_class(store: Store) -> str:
    """Ask which class this is for; offers to create one. Only called in a terminal."""
    if store.classes:
        labels = list(store.classes) + ["A new class…"]
        i = _choose("Which class?", labels)
        if i < len(store.classes):
            return store.classes[i]
    else:
        ui.info("You don't have any classes yet.")
    from rich.prompt import Prompt
    name = Prompt.ask(Text("  Class name", style=f"bold {ui.T.text}")).strip()
    if not name:
        _fail("No class given. Nothing started.")
    return store.add_class(name)


def _class_from_words(store: Store, words: list[str] | None) -> tuple[str, str]:
    """`cram start statics review slides` -> ('Statics', 'review slides'). Asks when nothing is given."""
    text = " ".join(words or [])
    if not text:
        if not _tty():
            names = ", ".join(store.classes) or "none yet"
            _fail(f"Which class? [b]cram start <class>[/]  (your classes: {names})")
        return _ask_class(store), ""
    course, rest = store.match_class(text)
    if course:
        return course, rest
    hits = _ambiguous(store, text)
    if len(hits) > 1:
        _fail(f"'{text.split()[0]}' could be {' or '.join(hits)}. Type more of the name.")
    if not _tty():
        _fail(f"'{text}' isn't one of your classes. Create it with [b]cram create class {text}[/].")
    if Confirm.ask(Text(f"  '{text}' isn't one of your classes. Create it?", style=f"bold {ui.T.text}"), default=True):
        return store.add_class(text), ""
    return _ask_class(store), ""


@app.command("start")
def start_cmd(
    words: Optional[list[str]] = typer.Argument(None, help="The class, optionally followed by details.",
                                                 autocompletion=_class_word_completer("words")),
    at: Optional[str] = typer.Option(None, "--at", help="Start time if you forgot: 9:30am, 'yesterday 9pm'."),
):
    """Start a study block. [dim]e.g. cram start statics[/] · no class: it asks which one"""
    store, _ = _load()
    run = store.running()
    if run:
        _fail(f"Already studying {run.course or ''} since {fmt_time(run.start)}. [b]cram stop[/] first.")
    when = parse_when(at) if at else now()
    if when > now() + timedelta(minutes=1):
        _fail("Can't start in the future.")
    _check_overlap(store, when, None)
    course, details = _class_from_words(store, words)
    s = store.add(when, None, [details] if details else [], course)
    store.save()
    ui.console.print(ui.session_card(s, "studying", border=ui.T.ok))


@app.command("stop")
def stop_cmd(at: Optional[str] = typer.Option(None, "--at", help="End time if you forgot: 5:15pm, 'today 5pm'.")):
    """Stop the current study block."""
    store, cfg = _load()
    run = store.running()
    if not run:
        _fail("You're not studying right now. [b]cram start[/] to begin.")
    end = parse_when(at, default_date=run.start.date()) if at else now()
    if at and end <= run.start and len(at.split()) == 1:
        end += timedelta(days=1)  # studied past midnight
    if end <= run.start:
        _fail(f"End time has to be after the start ({fmt_time(run.start)}).")
    if end > now() + timedelta(minutes=1):
        _fail("Can't stop in the future.")
    run.end = end
    store.save()
    ui.console.print(ui.session_card(run, "stopped", border=ui.T.accent, notes_label="details"))
    ui.console.print(Text("  ") + ui.totals_line(report.build(store, cfg, run.week)))


add_app = typer.Typer(help="Add [b]details[/] to the current block, a [b]note[/] to self, or a past [b]block[/].",
                      rich_markup_mode="rich", no_args_is_help=True)
app.add_typer(add_app, name="add")


@add_app.command("details")
def add_details(text: list[str] = typer.Argument(..., help="What you're covering.")):
    """Add details to the block you're in. [dim]e.g. cram add details "ch 5 problems"[/]"""
    store, _ = _load()
    run = store.running()
    if not run:
        _fail("You're not studying right now. [b]cram start[/] first.")
    run.notes.append(" ".join(text))
    store.save()
    ui.ok(f"Added to your {run.course or ''} block: {' '.join(text)}")


@add_app.command("note")
@add_app.command("todo", hidden=True)
def add_note(text: list[str] = typer.Argument(..., help="The note.")):
    """Add a note to self for this week (no reminders). [dim]e.g. cram add note "read ch 7"[/]"""
    store, _ = _load()
    try:
        t = store.add_todo(" ".join(text), week_start(date.today()))
    except ValueError:
        _fail('Give the note some text: [b]cram add note "read ch 7"[/]')
    store.save()
    ui.ok(f"Noted: {t.text}   [dim](check it off in the app: [b]cram[/])[/]")


@add_app.command("block")
def add_block():
    """Log a past block you forgot to start. Asks the class, day, start and end."""
    if not _tty():
        _fail("Run this in a terminal: it asks the class, day and times.")
    from rich.prompt import Prompt
    store, _ = _load()
    course = _ask_class(store)
    while True:
        try:
            d = parse_date(Prompt.ask(Text("  Day", style=f"bold {ui.T.text}"), default="today"))
            st = datetime.combine(d, parse_time(Prompt.ask(Text("  Start", style=f"bold {ui.T.text}"))))
            en = datetime.combine(d, parse_time(Prompt.ask(Text("  End", style=f"bold {ui.T.text}"))))
        except ParseError as e:
            ui.err(str(e))
            continue
        if en <= st:
            en += timedelta(days=1)           # past midnight
        if en > now() + timedelta(minutes=1):
            ui.err("That ends in the future.")
            continue
        o = store.overlap(st, en)
        if o:
            ui.err(f"That overlaps your block on {ui.block_label(o)}.")
            continue
        break
    details = Prompt.ask(Text("  Details (optional)", style=f"bold {ui.T.text}"), default="").strip()
    s = store.add(st, en, [details] if details else [], course)
    store.save()
    ui.console.print(ui.session_card(s, "block added", border=ui.T.ok, notes_label="details"))


@add_app.command("class", hidden=True)
def add_class_alias(name: list[str] = typer.Argument(...)):
    create_class(name)


create_app = typer.Typer(help="Create a [b]class[/].", rich_markup_mode="rich", no_args_is_help=True)
app.add_typer(create_app, name="create")


@create_app.command("class")
def create_class(name: list[str] = typer.Argument(..., help="The class name. Separate several with commas.")):
    """Create a class. [dim]e.g. cram create class Statics, Calc III[/]"""
    class_add(name)


@app.command("ls-class")
def ls_class():
    """List your classes with hours this week and in total."""
    _list_classes()


@app.command("ls-note")
def ls_note():
    """List this week's notes to self."""
    _show_todos(week_start(date.today()))


# ── misc ─────────────────────────────────────────────────────────────────────

@app.command(hidden=True)
def undo(steps: int = typer.Argument(1, help="How many changes to undo.")):
    """Undo your last change (start/stop, notes, edits…)."""
    _load()
    from . import history
    for _ in range(max(1, steps)):
        label = history.undo()
        if label is None:
            ui.info("Nothing left to undo.")
            break
        ui.ok(f"Undid: {label}")


@app.command(hidden=True)
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


@app.command("cycle-theme", hidden=True)
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


@app.command(hidden=True)
def config(
    edit_: bool = typer.Option(False, "--edit", "-e", help="Open the config in $EDITOR."),
    path: bool = typer.Option(False, "--path", help="Just print where the files live."),
):
    """Show or edit your settings (theme, banner, mascot)."""
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
