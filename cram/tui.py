"""Full-screen, keyboard-driven cram (what you get from plain `cram`)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import sys
from datetime import date, datetime, timedelta

from rich import box
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import config as config_mod
from . import conflict, history, mascot, report, ui
from .paths import data_dir
from .store import Session, Store
from .timeutil import (
    DAY_ABBR, ParseError, fmt_hm, fmt_md, fmt_mdy, fmt_range, fmt_time, now, parse_date,
    parse_time, parse_week, week_start,
)

TEXTUAL_THEMES = {
    "catppuccin": "catppuccin-mocha", "nord": "nord", "gruvbox": "gruvbox",
    "tokyonight": "tokyo-night", "rosepine": "rose-pine", "leonida": "leonida",
}

# Textual has no built-in theme for this one, so the widgets get a matching palette here.
LEONIDA = Theme(
    name="leonida", dark=True,
    primary="#c04dff", secondary="#2ee6d6", accent="#ff4fa3",
    warning="#ffb347", error="#ff5470", success="#4ef0b3",
    foreground="#fdeaff", background="#1a0f2e", surface="#241540", panel="#2e1b4f",
)

# every shortcut, for the F1 sheet (the footer only shows the first few)
SHORTCUTS = [
    ("prompt bar", "(bottom)"),
    ("  in [item]", "punch in, optionally with your first item"),
    ("  out", "punch out: review, add anything missing, save"),
    ("  undo / redo", "take back your last change (or put it back)"),
    ("  /vday [day]", "view a day           /vday yesterday"),
    ("  /vweek [week]", "view the week's times  /vweek last"),
    ("  /previewLogs [week]", "preview the log + invoice PDF"),
    ("  /exportLogs [week]", "save the PDF to your export folder"),
    ("  /weeks", "calendar of weeks: ↑↓ pick · p preview · e export · a approve"),
    ("  /config", "rate, theme, export folder, file names"),
    ("", ""),
    ("tab", "switch between the prompt bar and the top section"),
    ("arrows (top section)", "move between items, working on, today and week"),
    ("  in the item list", "f2 rename · delete remove · esc back"),
    ("enter (on a panel)", "open today / the week"),
    ("ctrl+s", "punch in / punch out"),
    ("ctrl+z · ctrl+y", "undo · redo"),
    ("ctrl+n", "jump to the working-on box"),
    ("ctrl+p", "command palette: approve, export, everything else"),
    ("ctrl+t", "fix the last block's times"),
    ("ctrl+l · f5", "week log · PDF preview"),
    ("ctrl+b", "toggle the side panels"),
    ("ctrl+q", "quit"),
    ("", ""),
    ("in the week log", "enter edit · ctrl+n new block · delete remove · [ ] switch weeks · esc back"),
    ("punching out", "enter adds a line · empty enter or ctrl+s saves · esc keeps you clocked in"),
]


def _today_view(store: Store) -> Table:
    g = Table.grid(padding=(0, 2))
    g.add_column(no_wrap=True)
    g.add_column(justify="right", no_wrap=True)
    g.add_column()
    blocks = store.on_day(date.today())
    if not blocks:
        g.add_row(Text("nothing yet", style=ui.T.dim), "", "")
    for s in blocks:
        rng = Text(fmt_range(s.start, s.end), style=f"bold {ui.T.ok}" if s.running else ui.T.text)
        first = s.notes[0] if s.notes else "—"
        more = f"  +{len(s.notes) - 1}" if len(s.notes) > 1 else ""
        g.add_row(rng, Text(fmt_hm(s.minutes()), style=ui.T.accent2),
                  Text(first, style=ui.T.muted, overflow="ellipsis", no_wrap=True) + Text(more, style=ui.T.dim))
    total = sum(s.minutes() for s in blocks)
    if blocks:
        g.add_row(Text("total", style=ui.T.dim), Text(fmt_hm(total), style=f"bold {ui.T.text}"), "")
    return g


# ── dialogs ──────────────────────────────────────────────────────────────────

class ItemList(OptionList):
    """A list of work items. delete removes, f2 renames (handled by the owner)."""

    BINDINGS = [
        Binding("delete,backspace", "remove", "remove item", show=False),
        Binding("f2", "rename", "rename item", show=False),
        Binding("escape", "back", "back", show=False),
        Binding("down", "down", show=False),
        Binding("right", "nav('right')", show=False),
    ]

    def action_down(self) -> None:
        """Past the last item, drop into the box below."""
        if self.highlighted is not None and self.highlighted >= self.option_count - 1:
            self.action_back()
        else:
            self.action_cursor_down()

    def action_nav(self, direction: str) -> None:
        nav = getattr(self.screen, "nav", None)
        if nav:
            nav(self.id, direction)

    def action_back(self) -> None:
        self.screen.query_one("#new" if self.screen.query("#new") else "#extra", Input).focus()

    class Removed(Message):
        def __init__(self, index: int):
            super().__init__()
            self.index = index

    class Renamed(Message):
        def __init__(self, index: int):
            super().__init__()
            self.index = index

    def action_remove(self) -> None:
        if self.highlighted is not None:
            self.post_message(ItemList.Removed(self.highlighted))

    def action_rename(self) -> None:
        if self.highlighted is not None:
            self.post_message(ItemList.Renamed(self.highlighted))


def _item_text(i: int, s: str) -> Text:
    return Text.assemble(("› ", ui.T.accent), (s, ui.T.text))


class BlockEditor(ModalScreen[bool]):
    """Confirm / edit / create a time block. mode: out | edit | add"""

    BINDINGS = [
        Binding("ctrl+s", "save", "save"),
        Binding("escape", "cancel", "cancel"),
    ]

    def __init__(self, session: Session | None, mode: str, day: date | None = None,
                 end: datetime | None = None):
        super().__init__()
        self.mode = mode
        self.sid = session.id if session else None
        self.items: list[str] = list(session.notes) if session else []
        if session:
            start = session.start
            end = session.end or end or now()
        else:
            d = day or date.today()
            start = end = None
        self.v_day = fmt_mdy(start.date() if start else d)
        self.v_start = fmt_time(start) if start else ""
        self.v_end = fmt_time(end) if end else ""
        self.renaming: int | None = None
        # a block that's still running: only its descriptions can change here
        self.live = bool(session and session.running and mode == "edit")
        self.live_start = session.start if self.live else None
        self.changed = False  # something was written to disk while the editor was open

    def compose(self) -> ComposeResult:
        titles = {"out": "punch out", "edit": "edit block", "add": "add a past block"}
        if self.live:
            titles["edit"] = "edit descriptions"
        if self.mode == "out":
            q = "Did you do anything else not listed?" if self.items else "What did you work on?"
        else:
            q = "Add an item"
        with Vertical(id="dialog") as v:
            v.border_title = titles[self.mode]
            with Horizontal(id="times"):
                yield Label("day")
                yield Input(self.v_day, id="day", placeholder="11/1/26")
                yield Label("from")
                yield Input(self.v_start, id="start", placeholder="9:00am")
                yield Label("to")
                yield Input(self.v_end, id="end", placeholder="5:00pm")
            yield Static(id="span")
            yield Label("what you did", classes="section")
            yield ItemList(id="items")
            yield Label(q, id="question")
            yield ItemInput(id="extra", placeholder="type + enter, saved right away")
            yield Static(id="error")
            items_hint = (" · ", ("↑", "bold"), " items: ", ("f2", "bold"), " rename ", ("del", "bold"), " remove")
            if self.mode == "out":
                hint = Text.assemble(("enter", "bold"), " adds a line (saved) · ", ("empty enter", "bold"), "/",
                                     ("ctrl+s", "bold"), " punches out · ", ("esc", "bold"), " keep working", *items_hint)
            elif self.mode == "add":
                hint = Text.assemble("fill in the times, then ", ("enter", "bold"), " saves each line · ",
                                     ("esc", "bold"), " close", *items_hint)
            else:
                hint = Text.assemble(("enter", "bold"), " saves each change · ", ("esc", "bold"), " close",
                                     *items_hint)
            yield Static(hint, id="hint")

    def on_mount(self) -> None:
        self._refresh_items()
        if self.live:
            self.query_one("#times").display = False
        self._update_span()
        self.query_one("#start" if self.mode == "add" else "#extra", Input).focus()

    def _refresh_items(self) -> None:
        ol = self.query_one("#items", ItemList)
        ol.clear_options()
        ol.add_options([_item_text(i, s) for i, s in enumerate(self.items)])
        ol.display = bool(self.items)
        self.query_one(".section").display = bool(self.items)

    def _parse(self) -> tuple[datetime, datetime]:
        d = parse_date(self.query_one("#day", Input).value)
        st = datetime.combine(d, parse_time(self.query_one("#start", Input).value))
        en = datetime.combine(d, parse_time(self.query_one("#end", Input).value))
        if en < st:
            en += timedelta(days=1)
        return st, en

    def _update_span(self) -> None:
        span = self.query_one("#span", Static)
        if self.live:
            st = self.live_start
            span.update(Text.assemble(
                (f"{DAY_ABBR[st.weekday()]} {fmt_md(st.date())}", f"bold {ui.T.text}"), ("  ·  ", ui.T.dim),
                (f"{fmt_time(st)}-now", f"bold {ui.T.ok}"), ("  ·  still running, times are set when you punch out", ui.T.dim)))
            return
        try:
            st, en = self._parse()
        except (ParseError, ValueError):
            span.update(Text(" ", style=ui.T.dim))
            return
        span.update(Text.assemble(
            (f"{DAY_ABBR[st.weekday()]} {fmt_md(st.date())}", f"bold {ui.T.text}"), ("  ·  ", ui.T.dim),
            (fmt_range(st, en), ui.T.text), ("  ·  ", ui.T.dim), (fmt_hm(int((en - st).total_seconds() // 60)), f"bold {ui.T.accent2}"),
        ))

    @on(Input.Changed, "#day, #start, #end")
    def _times_changed(self) -> None:
        self._update_span()
        self.query_one("#error", Static).update("")

    @on(Input.Submitted, "#day, #start, #end")
    def _next_field(self, event: Input.Submitted) -> None:
        # editing a finished block: times save as soon as they're all valid
        order = ["day", "start", "end", "extra"]
        nxt = self.query_one(f"#{order[order.index(event.input.id) + 1]}", Input)
        if self.mode == "edit" and not self.live and self.sid is not None:
            times = self._valid_times()
            if times is None:
                return
            s = Store.load().get(self.sid)
            if s and (s.start, s.end) != times:
                self._commit(*times, then=lambda: (self._saved("times saved"), nxt.focus()))
                return
        nxt.focus()

    @on(Input.Submitted, "#extra")
    def _add_item(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            self.action_save()
            return
        if self.renaming is not None:
            self.items.insert(self.renaming, text)
            self.renaming = None
        else:
            self.items.append(text)
        event.input.value = ""
        self._refresh_items()
        self._persist_items()

    @on(ItemList.Removed)
    def _remove(self, ev: ItemList.Removed) -> None:
        del self.items[ev.index]
        self._refresh_items()
        self._persist_items("removed")

    @on(ItemList.Renamed)
    def _rename(self, ev: ItemList.Renamed) -> None:
        inp = self.query_one("#extra", Input)
        inp.value = self.items.pop(ev.index)
        self.renaming = ev.index
        self._refresh_items()
        inp.focus()

    def _error(self, msg: str) -> None:
        self.query_one("#error", Static).update(Text(f"✗ {msg}", style=f"bold {ui.T.err}"))

    def _saved(self, msg: str = "saved") -> None:
        self.changed = True
        self.query_one("#error", Static).update(Text(f"✓ {msg}", style=f"bold {ui.T.ok}"))

    def _valid_times(self) -> tuple[datetime, datetime] | None:
        """Parse + check the times; shows the problem and returns None if they don't work."""
        try:
            st, en = self._parse()
        except (ParseError, ValueError) as e:
            self._error(str(e))
            return None
        if en > now() + timedelta(minutes=1):
            self._error("that ends in the future")
            return None
        store = Store.load()
        s = store.get(self.sid) if self.sid else None
        for monday in {week_start(st.date())} | ({s.week} if s else set()):
            if store.is_approved(monday):
                self._error(f"the week of {fmt_md(monday)} is approved and locked")
                return None
        return st, en

    def _commit(self, st: datetime, en: datetime, then) -> None:
        """Save this block's times + items, sorting out any overlap first."""
        store = Store.load()
        if conflict.overlapping(store, st, en, self.sid):
            def chosen(how: str | None) -> None:
                if how in (None, "back"):
                    self._error("times overlap another block; change them or pick a fix")
                    return
                self.sid = conflict.apply(Store.load(), how, st, en, list(self.items), self.sid)
                s = Store.load().get(self.sid)
                self.items = list(s.notes)
                self._refresh_items()
                self.query_one("#day", Input).value = fmt_mdy(s.start.date())
                self.query_one("#start", Input).value = fmt_time(s.start)
                if s.end:
                    self.query_one("#end", Input).value = fmt_time(s.end)
                self.changed = True
                then()
            self.app.push_screen(ConflictScreen(st, en, self.sid), chosen)
            return
        s = store.get(self.sid) if self.sid else None
        if s:
            s.start, s.end, s.notes = st, en, list(self.items)
        else:
            self.sid = store.add(st, en, list(self.items)).id
        store.save()
        self.changed = True
        then()

    def _persist_items(self, what: str = "saved") -> None:
        """Write the item list to disk right away."""
        if self.sid is None:
            # a brand-new past block: create it as soon as its times are valid
            times = self._valid_times()
            if times is None:
                self.query_one("#error", Static).update(
                    Text("fill in valid times above and this block will save", style=ui.T.warn))
                return
            self._commit(*times, then=lambda: self._saved("block saved"))
            return
        store = Store.load()
        s = store.get(self.sid)
        if s is None:
            return
        if store.is_approved(s.week):
            self._error(f"the week of {fmt_md(s.week)} is approved and locked")
            return
        s.notes = list(self.items)
        store.save()
        self._saved(what)

    def action_save(self) -> None:
        pending = self.query_one("#extra", Input).value.strip()
        if pending:
            if self.renaming is not None:
                self.items.insert(self.renaming, pending)
                self.renaming = None
            else:
                self.items.append(pending)
            self.query_one("#extra", Input).value = ""
            self._refresh_items()
        if self.live:
            self._persist_items()
            self.dismiss(True)
            return
        times = self._valid_times()
        if times is None:
            return
        self._commit(*times, then=lambda: self.dismiss(True))

    def action_cancel(self) -> None:
        # anything already saved stays saved; a half-typed line is kept too
        pending = self.query_one("#extra", Input).value.strip()
        if pending and self.sid is not None:
            self.items.insert(self.renaming, pending) if self.renaming is not None else self.items.append(pending)
            self._persist_items()
        self.dismiss(self.changed and self.mode != "out")


class ConflictScreen(ModalScreen[str]):
    """Two blocks claim the same time: pick how to fix it."""

    KEYS = {"merge": "m", "trim": "t", "shorten": "s", "replace": "r"}
    LABELS = {"merge": "merge them", "trim": "fit into the gap", "shorten": "shorten the other",
              "replace": "replace it"}
    BINDINGS = [
        Binding("escape", "pick('back')", "change the times"),
        Binding("m", "pick('merge')", show=False),
        Binding("t", "pick('trim')", show=False),
        Binding("s", "pick('shorten')", show=False),
        Binding("r", "pick('replace')", show=False),
    ]

    def __init__(self, st: datetime, en: datetime, sid: int | None):
        super().__init__()
        store = Store.load()
        self.st, self.en = st, en
        self.others = conflict.overlapping(store, st, en, sid)
        self.opts = conflict.options(store, st, en, sid)

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm", classes="wide") as v:
            v.border_title = "times overlap"
            head = Text.assemble(("This block ", ui.T.text),
                                 (f"{fmt_md(self.st.date())} {fmt_range(self.st, self.en)}", f"bold {ui.T.accent2}"),
                                 (" overlaps:", ui.T.text))
            for o in self.others:
                head.append(f"\n  {fmt_range(o.start, o.end)}", style=f"bold {ui.T.warn}")
                if o.notes:
                    head.append(f"  {' · '.join(o.notes)}", style=ui.T.muted)
            yield Static(head)
            yield OptionList(*[
                Option(Text.assemble((f"{self.KEYS[k]:<4}", f"bold {ui.T.accent}"), (f"{self.LABELS[k]:<20}", f"bold {ui.T.text}"),
                                     (desc, ui.T.muted)), id=k)
                for k, desc in self.opts.items()
            ] + [Option(Text.assemble(("esc ", f"bold {ui.T.accent}"), (f"{'change the times':<20}", f"bold {ui.T.text}"),
                                      ("go back and edit them", ui.T.muted)), id="back")], id="quitopts")

    def on_mount(self) -> None:
        self.query_one("#quitopts").focus()

    @on(OptionList.OptionSelected, "#quitopts")
    def _chosen(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    def action_pick(self, what: str) -> None:
        if what == "back" or what in self.opts:
            self.dismiss(what)


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("y,enter", "yes", "yes"), Binding("n,escape", "no", "no")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm") as v:
            v.border_title = "confirm"
            yield Static(Text(self.question, style=f"bold {ui.T.text}"))
            yield Static(Text.assemble(("y", "bold"), " yes   ", ("n", "bold"), " no", style=ui.T.dim))

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class KeysSheet(ModalScreen[None]):
    BINDINGS = [Binding("escape,f1,q", "close", "close")]

    def compose(self) -> ComposeResult:
        t = Table.grid(padding=(0, 3))
        t.add_column(style=f"bold {ui.T.accent}", no_wrap=True)
        t.add_column(style=ui.T.text)
        for k, v in SHORTCUTS:
            t.add_row(k, v)
        with Vertical(id="keys") as v:
            v.border_title = "shortcuts"
            yield Static(t)
            yield Static(Text("esc to close", style=ui.T.dim))

    def action_close(self) -> None:
        self.dismiss(None)


# ── week log ─────────────────────────────────────────────────────────────────

class BlockTable(DataTable):
    """A table of blocks; enter (shown in the footer as "edit") opens the selected one."""

    BINDINGS = [Binding("enter", "select_cursor", "edit")]


class LogScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("f2", "edit", "edit", show=False),
        Binding("ctrl+n", "new", "new block"),
        Binding("delete", "delete", "delete"),
        Binding("left_square_bracket", "week(-1)", "prev week", show=False),
        Binding("right_square_bracket", "week(1)", "next week", show=False),
    ]

    def __init__(self, monday: date | None = None):
        super().__init__()
        self.monday = monday or week_start(date.today())

    def compose(self) -> ComposeResult:
        with Vertical(id="logbox") as v:
            v.border_title = "week log"
            yield BlockTable(id="log", cursor_type="row", zebra_stripes=False)
            yield Static(id="weektl")
            yield Static(id="logtotal")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("day", "time", "dur", "what")
        self.reload()

    def reload(self) -> None:
        cfg = config_mod.load()
        store = Store.load()
        r = report.build(store, cfg, self.monday)
        box = self.query_one("#logbox")
        box.border_title = f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)}   [ ] to switch weeks"
        live = any(s.running for s in store.in_week(self.monday))
        box.border_subtitle = "● block in progress" if live else ("approved 🔒" if r.approved else "pending approval")
        box.set_class(live, "unsaved")
        t = self.query_one(DataTable)
        t.clear()
        last = None
        for s in store.in_week(self.monday):
            day = f"{DAY_ABBR[s.day.weekday()]} {fmt_md(s.day)}" if s.day != last else ""
            last = s.day
            rng = Text(fmt_range(s.start, s.end))
            if s.running:
                rng.stylize(f"bold {ui.T.ok}")
                rng.append("  ● running", style=f"bold {ui.T.ok}")
            t.add_row(day, rng, fmt_hm(s.minutes()),
                      Text(" · ".join(s.notes) or "—", overflow="ellipsis", no_wrap=True), key=str(s.id))
        self.query_one("#logtotal", Static).update(ui.totals_line(r))
        self._timeline(store)

    def _timeline(self, store: Store) -> None:
        from .timeutil import week_days
        days = week_days(self.monday)
        per_day = [store.on_day(d) for d in days]
        lo, hi = 8, 18
        for blocks in per_day:
            a, b = ui.day_range(blocks)
            lo, hi = min(lo, a), max(hi, b)
        label_w, tail_w = 11, 9
        w = max(10, (self.query_one("#logbox").content_size.width or 80) - label_w - tail_w - 2)
        rows = []
        for d, blocks in zip(days, per_day):
            today = d == date.today()
            row = Text(f"{DAY_ABBR[d.weekday()]:<6}{fmt_md(d):<5}", style=f"bold {ui.T.text}" if today else ui.T.muted)
            row.append_text(ui.timeline_bar(blocks, d, w, lo, hi))
            mins = sum(s.minutes() for s in blocks)
            row.append(f"{fmt_hm(mins):>{tail_w}}" if mins else "", style=ui.T.accent2)
            rows.append(row)
        rows.append(Text(" " * label_w) + ui.timeline_axis(w, lo, hi))
        self.query_one("#weektl", Static).update(Group(Text(""), *rows))

    def on_resize(self) -> None:
        self.call_after_refresh(self.reload)

    def _selected(self) -> Session | None:
        t = self.query_one(DataTable)
        if not t.row_count:
            return None
        key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        return Store.load().get(int(key))

    def action_week(self, delta: int) -> None:
        self.monday += timedelta(weeks=delta)
        self.reload()

    @on(DataTable.RowSelected)
    def _row_enter(self) -> None:
        self.action_edit()

    def action_edit(self) -> None:
        s = self._selected()
        if s:
            self.app.push_screen(BlockEditor(s, "edit"), lambda ok: self.reload())

    def action_new(self) -> None:
        d = min(date.today(), self.monday + timedelta(days=6))
        self.app.push_screen(BlockEditor(None, "add", day=d), lambda ok: self.reload())

    def action_delete(self) -> None:
        s = self._selected()
        if not s:
            return

        def done(yes: bool | None) -> None:
            if not yes:
                return
            store = Store.load()
            if store.is_approved(s.week):
                self.app.notify("That week is approved and locked.", severity="error")
                return
            store.remove(s.id)
            store.save()
            self.reload()

        self.app.push_screen(Confirm(f"Delete block #{s.id} ({fmt_md(s.day)} {fmt_range(s.start, s.end)})?"), done)


# ── crash safety ─────────────────────────────────────────────────────────────
# Every change is written to data.json the moment it happens. On top of that the
# app keeps a small heartbeat file: when it was last alive, whether it exited
# cleanly, and any half-typed item. That lets the next launch notice a crash.

def _hb_path():
    return data_dir() / "session.json"


def write_heartbeat(clean: bool = False, draft: str = "") -> None:
    try:
        tmp = _hb_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                   "clean": clean, "draft": draft, "pid": os.getpid()}))
        os.replace(tmp, _hb_path())
    except OSError:
        pass


def read_heartbeat() -> dict | None:
    try:
        return json.loads(_hb_path().read_text())
    except (OSError, ValueError):
        return None


# ── prompt bar ───────────────────────────────────────────────────────────────

SLASH = [
    ("/vday", "view a day's blocks", "/vday yesterday"),
    ("/vweek", "view the week's times", "/vweek last"),
    ("/previewLogs", "preview the week's log + invoice PDF", "/previewLogs last"),
    ("/exportLogs", "save the PDF to your export folder", "/exportLogs last"),
    ("/weeks", "pick any week on a calendar: preview, export, approve", ""),
    ("/config", "rate, theme, export folder, file names", ""),
    ("/help", "commands and shortcuts", ""),
]
WORDS = [("in", "punch in", "in fixing the scraper"), ("out", "punch out", "")]


class CommandInput(Input):
    """The prompt bar. Up/down/tab drive the slash menu while it's open."""

    BINDINGS = [
        Binding("up", "menu(-1)", show=False),
        Binding("down", "menu(1)", show=False),
        Binding("tab", "complete", "switch window"),
        Binding("escape", "close_menu", show=False),
    ]

    def _menu(self) -> OptionList:
        return self.screen.query_one("#slash", OptionList)

    def action_menu(self, step: int) -> None:
        m = self._menu()
        if m.display and m.option_count:
            m.highlighted = ((m.highlighted or 0) + step) % m.option_count

    def action_complete(self) -> None:
        m = self._menu()
        if m.display and m.highlighted is not None:
            self.value = m.get_option_at_index(m.highlighted).id + " "
            self.cursor_position = len(self.value)
            m.display = False
        else:
            self.screen.action_toggle_area()

    def action_close_menu(self) -> None:
        self._menu().display = False


class ItemInput(Input):
    """Where you type what you're working on. Up jumps into the item list;
    right at the end of the text moves over to the side panels."""

    BINDINGS = [
        Binding("up", "to_list", show=False),
        Binding("right", "right", show=False),
    ]

    def action_right(self) -> None:
        nav = getattr(self.screen, "nav", None)
        if nav and self.cursor_position >= len(self.value):
            nav(self.id, "right")
        else:
            self.action_cursor_right()

    def action_to_list(self) -> None:
        items = self.screen.query_one("#items", ItemList)
        if items.display and items.option_count:
            items.focus()
            items.highlighted = items.option_count - 1


class FocusPanel(Static, can_focus=True):
    """A side panel you can arrow to; enter opens its full view."""

    BINDINGS = [
        Binding("enter", "open", "open", show=False),
        Binding("up", "nav('up')", show=False),
        Binding("down", "nav('down')", show=False),
        Binding("left", "nav('left')", show=False),
        Binding("right", "nav('right')", show=False),
    ]

    def action_nav(self, direction: str) -> None:
        nav = getattr(self.screen, "nav", None)
        if nav:
            nav(self.id, direction)

    class Opened(Message):
        def __init__(self, panel_id: str):
            super().__init__()
            self.panel_id = panel_id

    def action_open(self) -> None:
        self.post_message(FocusPanel.Opened(self.id))


# ── view screens ─────────────────────────────────────────────────────────────

class WeekPicker(Screen):
    """A calendar of weeks: ↑↓ to pick one, then preview / export / approve it."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("q", "app.pop_screen", "back", show=False),
        Binding("p", "act('preview')", "preview"),
        Binding("e", "act('export')", "export"),
        Binding("a", "act('approve')", "approve"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="logbox") as v:
            v.border_title = "pick a week"
            yield Static(id="weekhead")
            yield OptionList(id="weeklist")
            yield Static(id="weekinfo")
        yield Footer()

    def on_mount(self) -> None:
        self.reload(select_last=True)
        self.query_one("#weeklist").focus()

    def on_screen_resume(self) -> None:
        self.reload()

    def _weeks(self, store: Store) -> list[date]:
        cur = week_start(date.today())
        first = min([s.week for s in store.sessions] + [cur - timedelta(weeks=11)])
        n = (cur - first).days // 7
        return [first + timedelta(weeks=i) for i in range(n + 1)]

    def reload(self, select_last: bool = False) -> None:
        store = Store.load()
        cfg = self.app.cfg
        self.mondays = self._weeks(store)
        ol = self.query_one("#weeklist", OptionList)
        keep = ol.highlighted
        today = date.today()
        hl = ui.ramp(5, [ui.T.faint, ui.T.accent2, ui.T.accent])
        opts = []
        for monday in self.mondays:
            r = report.build(store, cfg, monday)
            sunday = monday + timedelta(days=6)
            month = ""                      # label the row where a month begins
            if monday == self.mondays[0] or monday.day == 1:
                month = f"{monday:%b}"
            elif sunday.month != monday.month:
                month = f"{sunday:%b}"
            row = Text(f"{month:<5}", style=f"bold {ui.T.accent}")
            for i in range(7):
                d = monday + timedelta(days=i)
                mins = sum(x.minutes() for x in store.on_day(d))
                if d > today:
                    style = ui.T.faint
                elif mins:
                    style = f"bold {hl[min(4, 1 + mins // 120)]}"
                else:
                    style = ui.T.dim
                if d == today:
                    style += " underline"
                row.append(f"{d.day:>3}", style=style)
            row.append(f"   {fmt_hm(r.total_minutes) if r.total_minutes else '·':>8}", style=ui.T.text)
            if r.approved:
                rec = store.approval(monday) or {}
                status = ("✓ exported" if rec.get("exports") else "✓ approved") + f"  #{r.invoice_number}"
                row.append(f"   {status}", style=ui.T.ok)
            elif r.days:
                row.append("   ◌ pending", style=ui.T.warn)
            opts.append(Option(row, id=monday.isoformat()))
        ol.clear_options()
        ol.add_options(opts)
        ol.highlighted = len(opts) - 1 if select_last or keep is None else min(keep, len(opts) - 1)
        head = Text(f"{'':<5}", style=ui.T.dim)
        for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"):
            head.append(f"{name:>3}", style=f"bold {ui.T.muted}")
        head.append(f"   {'hours':>8}   status", style=f"bold {ui.T.muted}")
        self.query_one("#weekhead", Static).update(head)
        self._info()

    @on(OptionList.OptionHighlighted, "#weeklist")
    def _info(self) -> None:
        m = self.selected()
        if m is None:
            return
        r = report.build(Store.load(), self.app.cfg, m)
        msg = Text.assemble((f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)}", f"bold {ui.T.text}"), ("   ", ""))
        if r.days:
            msg.append_text(ui.totals_line(r))
        else:
            msg.append("nothing logged", style=ui.T.dim)
        self.query_one("#weekinfo", Static).update(msg)

    def selected(self) -> date | None:
        ol = self.query_one("#weeklist", OptionList)
        if ol.highlighted is None:
            return None
        return date.fromisoformat(ol.get_option_at_index(ol.highlighted).id)

    @on(OptionList.OptionSelected, "#weeklist")
    def _open(self) -> None:
        m = self.selected()
        if m:
            self.app.push_screen(LogScreen(m))

    def action_act(self, what: str) -> None:
        m = self.selected()
        if m is None:
            return
        if not Store.load().in_week(m):
            self.app.notify(f"Nothing logged for the week of {fmt_md(m)}.", severity="warning")
            return
        if what == "preview":
            self.app.run_cli("preview", "-w", m.isoformat())
        elif what == "export":
            self.app.main.export_logs(m.isoformat())
        elif what == "approve":
            self.app.run_cli("approve", "-w", m.isoformat())
        self.reload()


class DayScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("q", "app.pop_screen", "back", show=False),
        Binding("f2", "edit", "edit", show=False),
        Binding("ctrl+n", "new", "new block"),
        Binding("delete", "delete", "delete block"),
        Binding("left_square_bracket", "day(-1)", "prev day"),
        Binding("right_square_bracket", "day(1)", "next day"),
    ]

    def __init__(self, day: date):
        super().__init__()
        self.day = day
        self._shown: tuple | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="logbox"):
            yield BlockTable(id="daylog", cursor_type="row", zebra_stripes=False)
            yield Static(id="dayempty")
            yield Static(id="daytl")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_column("time", key="time")
        t.add_column("dur", key="dur")
        t.add_column("what you did", key="what")
        self.reload()
        self.set_interval(5, self.reload)
        t.focus()

    def action_day(self, delta: int) -> None:
        self.day += timedelta(days=delta)
        self._shown = None
        self.reload()

    def reload(self) -> None:
        store = Store.load()
        blocks = store.on_day(self.day)
        total = sum(s.minutes() for s in blocks)
        live = any(s.running for s in blocks)
        box_ = self.query_one("#logbox")
        n = len(blocks)
        box_.border_title = (f"{DAY_ABBR[self.day.weekday()]} {fmt_mdy(self.day)}  ·  {fmt_hm(total)}"
                             f"  ·  {n} block{'s' if n != 1 else ''}")
        if store.is_approved(week_start(self.day)):
            box_.border_subtitle = "approved 🔒 (read only)"
        else:
            box_.border_subtitle = "● block in progress" if live else "enter to edit a block"
        box_.set_class(live, "unsaved")

        t = self.query_one(DataTable)
        snapshot = tuple((s.id, s.start, s.end, tuple(s.notes), s.minutes()) for s in blocks)
        if snapshot != self._shown:
            self._shown = snapshot
            keep = t.cursor_row
            t.clear()
            for s in blocks:
                rng = Text(fmt_range(s.start, s.end), style=f"bold {ui.T.ok}" if s.running else ui.T.text)
                if s.running:
                    rng.append("\n● running", style=f"bold {ui.T.ok}")
                what = ui.notes_text(s.notes) if s.notes else Text("— no description yet, enter to add one", style=ui.T.dim)
                t.add_row(rng, Text(fmt_hm(s.minutes()), style=ui.T.accent2), what, key=str(s.id), height=None)
            # last row: always there, enter on it adds a block for this day
            t.add_row(Text("+", style=f"bold {ui.T.accent}"), "",
                      Text("add a block" + ("" if blocks else " (nothing logged this day yet)"), style=ui.T.muted),
                      key="add")
            t.move_cursor(row=min(keep, len(blocks)))
        self.query_one("#dayempty", Static).update("")
        self._timeline(blocks)

    def _timeline(self, blocks) -> None:
        w = (self.query_one("#logbox").content_size.width or 80) - 2
        lo, hi = ui.day_range(blocks)
        self.query_one("#daytl", Static).update(
            Group(ui.timeline_bar(blocks, self.day, w, lo, hi), ui.timeline_axis(w, lo, hi)))

    def on_resize(self) -> None:
        self.call_after_refresh(self.reload)

    def _cursor_key(self) -> str | None:
        t = self.query_one(DataTable)
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value

    def _selected(self) -> Session | None:
        key = self._cursor_key()
        return Store.load().get(int(key)) if key and key != "add" else None

    @on(DataTable.RowSelected)
    def _row_enter(self) -> None:
        if self._cursor_key() == "add":
            self.action_new()
        else:
            self.action_edit()

    def action_edit(self) -> None:
        s = self._selected()
        if s:
            self.app.push_screen(BlockEditor(s, "edit"), self._edited)

    def _edited(self, saved: bool | None) -> None:
        if saved:
            self.app.notify("Block updated.", title="✓ saved")
        self._shown = None
        self.reload()

    def action_new(self) -> None:
        self.app.push_screen(BlockEditor(None, "add", day=self.day), self._edited)

    def action_delete(self) -> None:
        s = self._selected()
        if not s:
            return
        if s.running:
            self.app.notify("That block is still running. Punch out first.", severity="warning")
            return

        def done(yes: bool | None) -> None:
            if not yes:
                return
            store = Store.load()
            if store.is_approved(s.week):
                self.app.notify("That week is approved and locked.", severity="error")
                return
            store.remove(s.id)
            store.save()
            self._edited(False)

        self.app.push_screen(Confirm(f"Delete block {fmt_range(s.start, s.end)} on {fmt_md(s.day)}?"), done)


class ConfigScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("ctrl+s", "save", "save"),
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+o", "full", "open full config file"),
    ]

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.theme0 = cfg["ui"].get("theme", "catppuccin")

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as v:
            v.border_title = "config"
            yield Label("hourly rate ($)", classes="field")
            yield Input(f"{float(self.cfg['invoice']['rate']):g}", id="rate", placeholder="25")
            yield Label("color theme  (↑↓ previews)", classes="field")
            yield OptionList(*[
                Option(Text.assemble((f"{name:<12}", f"bold {ui.THEMES[name]['text']}"),
                                     *[("██", c) for c in ui.ramp(6, ui.THEMES[name]["grad"])]), id=name)
                for name in ui.THEMES
            ], id="themes")
            yield Label("export folder", classes="field")
            yield Input(self.cfg["export"]["dir"], id="folder", placeholder="~/Documents/cram")
            yield Label("file names  (example for this week)", classes="field")
            yield OptionList(*self._naming_options(), id="naming")
            yield Static(id="error")
            yield Static(Text.assemble(("enter", "bold"), " next · ", ("ctrl+s", "bold"), " save · ",
                                       ("esc", "bold"), " cancel · ", ("ctrl+o", "bold"),
                                       " full config file (name, client, address…)"), id="hint")

    def _naming_options(self) -> list[Option]:
        r = report.build(Store.load(), self.cfg, week_start(date.today()))
        current = self.cfg["export"]["filename"]
        patterns = list(report.NAMING_PRESETS)
        if current not in patterns:
            patterns.insert(0, current)
        self.patterns = patterns
        return [Option(Text.assemble((report.export_filename(self.cfg, r, pat), f"bold {ui.T.text}"),
                                     ("   custom" if pat not in report.NAMING_PRESETS else "", ui.T.dim)), id=str(i))
                for i, pat in enumerate(patterns)]

    def on_mount(self) -> None:
        ol = self.query_one("#themes", OptionList)
        names = list(ui.THEMES)
        ol.highlighted = names.index(self.theme0) if self.theme0 in names else 0
        self.query_one("#naming", OptionList).highlighted = self.patterns.index(self.cfg["export"]["filename"])
        self.query_one("#rate", Input).focus()

    @on(OptionList.OptionHighlighted, "#themes")
    def _preview(self, ev: OptionList.OptionHighlighted) -> None:
        self.app.apply_theme(ev.option.id)

    @on(Input.Submitted, "#rate")
    def _rate_done(self) -> None:
        self.query_one("#themes").focus()

    @on(OptionList.OptionSelected, "#themes")
    def _theme_done(self) -> None:
        self.query_one("#folder").focus()

    @on(Input.Submitted, "#folder")
    def _folder_done(self) -> None:
        self.query_one("#naming").focus()

    @on(OptionList.OptionSelected, "#naming")
    def _picked(self) -> None:
        self.action_save()

    def action_save(self) -> None:
        try:
            rate = float(self.query_one("#rate", Input).value.strip().lstrip("$"))
            if rate <= 0:
                raise ValueError
        except ValueError:
            self.query_one("#error", Static).update(Text("✗ the rate should be a number, like 25", style=f"bold {ui.T.err}"))
            return
        ol = self.query_one("#themes", OptionList)
        theme = ol.get_option_at_index(ol.highlighted).id
        folder = self.query_one("#folder", Input).value.strip() or "~/Documents/cram"
        try:
            Path(folder).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.query_one("#error", Static).update(Text(f"✗ can't use that folder: {e.strerror}", style=f"bold {ui.T.err}"))
            return
        naming = self.query_one("#naming", OptionList)
        config_mod.set_value("invoice", "rate", rate)
        config_mod.set_value("ui", "theme", theme)
        config_mod.set_value("export", "dir", folder)
        config_mod.set_value("export", "filename", self.patterns[naming.highlighted or 0])
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.app.apply_theme(self.theme0)
        self.dismiss(False)

    def action_full(self) -> None:
        self.dismiss(False)
        self.app.run_cli("config", "--edit", wait=False)


class ObPage(Static, can_focus=True):
    """A setup page with nothing to type: enter moves on."""

    BINDINGS = [Binding("enter", "screen.next", show=False)]


class OnboardingScreen(ModalScreen[bool]):
    """First-run setup: who you are, who you bill, your rate and theme, then a quick tour.
    Every step is saved as you go; esc skips the rest (it's all in /config later)."""

    AUTO_FOCUS = None
    BINDINGS = [
        Binding("escape", "skip", "skip setup"),
        Binding("ctrl+s", "next", "next", show=False),
    ]
    # step id, title, the inputs on it: (config section, key, label, placeholder)
    STEPS = [
        ("welcome", "welcome", []),
        ("you", "about you · goes on your log and invoice", [
            ("you", "name", "your name", "Alex Rivera"),
            ("you", "title", "your title  (log header reads Name-Title)", "Contractor"),
            ("you", "email", "your email", "you@example.com"),
            ("you", "paypal", "paypal handle  (optional, leave blank to hide it)", "@yourhandle"),
        ]),
        ("client", "who you bill", [
            ("client", "name", "client name", "Client Co"),
            ("client", "email", "client billing email", "billing@example.com"),
            ("invoice", "role", "what you do for them  (first line of the invoice)", "Software Contractor"),
        ]),
        ("rate", "rate and look", [
            ("invoice", "rate", "hourly rate ($)", "25"),
        ]),
        ("tour", "you're set", []),
    ]

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.step = 0
        self.theme0 = cfg["ui"].get("theme", "catppuccin")

    def _field_id(self, sec: str, key: str) -> str:
        return f"ob-{sec}-{key}"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="onboard") as v:
            v.border_title = "set up cram"
            with Vertical(id="ob-welcome", classes="ob-step"):
                yield Static(ui.banner(), id="ob-banner")
                yield ObPage(Text.assemble(
                    ("Welcome! ", f"bold {ui.T.accent}"),
                    ("cram is a time clock for your terminal. Punch in, jot down what you're working on, "
                     "punch out, and every week turns into a work log + invoice PDF.\n\n", ui.T.text),
                    ("Let's fill in what goes on that invoice. It takes about a minute, "
                     "and you can change all of it later with ", ui.T.muted), ("/config", f"bold {ui.T.text}"),
                    (".", ui.T.muted)))
            for sid, _title, fields in self.STEPS[1:-1]:
                with Vertical(id=f"ob-{sid}", classes="ob-step"):
                    for sec, key, label, ph in fields:
                        cur = self.cfg[sec].get(key, "")
                        default = config_mod.defaults()[sec].get(key)
                        value = "" if cur == default or cur is None else str(cur)
                        if key == "rate":
                            value = f"{float(cur):g}"
                        yield Label(label, classes="field")
                        yield Input(value, id=self._field_id(sec, key), placeholder=ph, classes="ob-input")
                    if sid == "rate":
                        yield Label("color theme  (↑↓ previews)", classes="field")
                        yield OptionList(*[
                            Option(Text.assemble((f"{name:<12}", f"bold {ui.THEMES[name]['text']}"),
                                                 *[("██", c) for c in ui.ramp(6, ui.THEMES[name]["grad"])]), id=name)
                            for name in ui.THEMES
                        ], id="themes")
            with Vertical(id="ob-tour", classes="ob-step"):
                yield ObPage()
            yield Static(id="error")
            yield Static(id="ob-steps")
            yield Static(id="hint")

    def _tour(self) -> Table:
        t = Table.grid(padding=(0, 3))
        t.add_column(style=f"bold {ui.T.accent}", no_wrap=True)
        t.add_column(style=ui.T.text)
        for k, v in [
            ("in", "type it in the bar at the bottom to punch in"),
            ("working on", "list what you're doing as you go; it becomes the log"),
            ("out", "punch out: review the block and save it"),
            ("tab", "switch between the bottom bar and the top section"),
            ("arrows", "move between your items, today and the week"),
            ("/", "views: a day, the week, preview or export the PDF"),
            ("ctrl+p", "command palette: approve a week and everything else"),
            ("f1", "every shortcut"),
        ]:
            t.add_row(k, v)
        return Group(Text("The basics:\n", style=f"bold {ui.T.text}"), t)

    def on_mount(self) -> None:
        names = list(ui.THEMES)
        self.query_one("#themes", OptionList).highlighted = names.index(self.theme0) if self.theme0 in names else 0
        self._show()

    def _show(self) -> None:
        sid, title, fields = self.STEPS[self.step]
        for i, (other, _t, _f) in enumerate(self.STEPS):
            self.query_one(f"#ob-{other}").display = i == self.step
        self.query_one("#dialog").border_title = f"set up cram · {title}"
        self.query_one("#error", Static).update("")
        dots = Text()
        for i in range(len(self.STEPS)):
            dots.append("● " if i == self.step else "○ ", style=ui.T.accent if i <= self.step else ui.T.dim)
        self.query_one("#ob-steps", Static).update(dots)
        last = self.step == len(self.STEPS) - 1
        self.query_one("#hint", Static).update(Text.assemble(
            ("enter", "bold"), " start using cram" if last else (" let's go" if self.step == 0 else " next"),
            *([] if last else [" · ", ("esc", "bold"), " skip setup"]), style=ui.T.muted))
        if sid == "tour":  # built now so it picks up the theme you just chose
            self.query_one("#ob-tour ObPage", Static).update(self._tour())
        if fields:
            self.query_one(f"#{self._field_id(*fields[0][:2])}", Input).focus()
        else:
            self.query_one(f"#ob-{sid} ObPage").focus()

    @on(Input.Submitted, ".ob-input")
    def _field_done(self, ev: Input.Submitted) -> None:
        ids = [self._field_id(sec, key) for sec, key, *_ in self.STEPS[self.step][2]]
        i = ids.index(ev.input.id)
        if i + 1 < len(ids):
            self.query_one(f"#{ids[i + 1]}", Input).focus()
        elif self.STEPS[self.step][0] == "rate":
            if self._save_step():
                self.query_one("#error", Static).update("")
                self.query_one("#themes").focus()
        else:
            self.action_next()

    @on(OptionList.OptionHighlighted, "#themes")
    def _preview(self, ev: OptionList.OptionHighlighted) -> None:
        self.app.apply_theme(ev.option.id)

    @on(OptionList.OptionSelected, "#themes")
    def _theme_done(self) -> None:
        self.action_next()

    def _save_step(self) -> bool:
        sid, _title, fields = self.STEPS[self.step]
        for sec, key, *_ in fields:
            value = self.query_one(f"#{self._field_id(sec, key)}", Input).value.strip()
            if key == "rate":
                try:
                    rate = float(value.lstrip("$") or 0)
                    if rate <= 0:
                        raise ValueError
                except ValueError:
                    self.query_one("#error", Static).update(
                        Text("✗ the rate should be a number, like 25", style=f"bold {ui.T.err}"))
                    return False
                config_mod.set_value(sec, key, rate)
            elif value or key == "paypal":
                config_mod.set_value(sec, key, value)
        if sid == "rate":
            ol = self.query_one("#themes", OptionList)
            config_mod.set_value("ui", "theme", ol.get_option_at_index(ol.highlighted or 0).id)
        if sid == "client":
            # The PDF's title and file name read better with the client's name in them.
            name = self.query_one(f"#{self._field_id('client', 'name')}", Input).value.strip()
            if name and self.cfg["invoice"]["doc_title"] == "Work Logs + Invoice":
                config_mod.set_value("invoice", "doc_title", f"{name} Logs + Invoice")
        return True

    def action_next(self) -> None:
        if not self._save_step():
            return
        if self.step == len(self.STEPS) - 1:
            self._finish()
            return
        self.step += 1
        self._show()

    def action_skip(self) -> None:
        if self.step == len(self.STEPS) - 1:
            self._finish()
            return
        self.app.notify("Setup skipped. /config or ctrl+p → \"Edit full config file\" any time.")
        self._finish()

    def _finish(self) -> None:
        config_mod.set_value("ui", "onboarded", True)
        self.dismiss(True)


class RecoverScreen(ModalScreen[str]):
    """Shown when cram died while you were on the clock."""

    BINDINGS = [
        Binding("k,escape", "pick('keep')", "still working"),
        Binding("e", "pick('end')", "end it then"),
    ]

    def __init__(self, run: Session, last_seen: datetime):
        super().__init__()
        self.run, self.last_seen = run, last_seen

    def compose(self) -> ComposeResult:
        gap = datetime.now() - self.last_seen
        with Vertical(id="confirm") as v:
            v.border_title = "cram closed unexpectedly"
            yield Static(Text.assemble(
                ("Nothing was lost. ", f"bold {ui.T.ok}"),
                (f"You're still clocked in since {fmt_time(self.run.start)}", ui.T.text),
                (f" ({DAY_ABBR[self.run.day.weekday()]} {fmt_md(self.run.day)}).\n", ui.T.text),
                (f"cram last saw you at {fmt_time(self.last_seen)}, {fmt_hm(int(gap.total_seconds() // 60))} ago.", ui.T.muted),
            ))
            yield Static("")
            yield Static(Text.assemble(("k", f"bold {ui.T.accent}"), "  keep the clock running (I'm still working)\n",
                                       ("e", f"bold {ui.T.accent}"), f"  end the block at {fmt_time(self.last_seen)} (you can adjust it)"))

    def action_pick(self, what: str) -> None:
        self.dismiss(what)


class QuitScreen(ModalScreen[str]):
    """Are you sure? / punch out or stay clocked in."""

    BINDINGS = [
        Binding("escape", "pick('cancel')", "cancel"),
        Binding("o", "pick('out')", "punch out", show=False),
        Binding("k", "pick('stay')", "stay clocked in", show=False),
        Binding("s,y", "pick('save')", "save & quit", show=False),
        Binding("d", "pick('discard')", "don't save", show=False),
    ]

    def __init__(self, run: Session | None, draft: str):
        super().__init__()
        self.run, self.draft = run, draft

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm") as v:
            if self.run:
                v.border_title = "quit cram?"
                yield Static(Text.assemble(
                    ("You're on the clock", f"bold {ui.T.ok}"),
                    (f" since {fmt_time(self.run.start)} ({fmt_hm(self.run.minutes())}).\n", ui.T.text),
                    ("Punch out, or stay clocked in while cram is closed?", ui.T.text),
                ))
                opts = [("out", "o", "punch out", "save the block, then quit"),
                        ("stay", "k", "stay clocked in", "the clock keeps running"),
                        ("discard", "d", "don't save", "throw away this block" + (" and typed item" if self.draft else "")),
                        ("cancel", "esc", "cancel", "back to cram")]
            else:
                v.border_title = "quit cram?"
                yield Static(Text("Are you sure? Everything will be saved.", style=f"bold {ui.T.text}"))
                opts = [("save", "s", "save and quit", "everything is kept"),
                        ("discard", "d", "don't save", "throw away the typed item" if self.draft else "quit"),
                        ("cancel", "esc", "cancel", "back to cram")]
            if self.draft:
                where = "added to your block" if self.run else "kept for next time"
                yield Static(Text(f"Your typed item “{self.draft}” will be {where} unless you don't save.",
                                  style=ui.T.muted))
            yield OptionList(*[
                Option(Text.assemble((f"{key:<5}", f"bold {ui.T.accent}"), (f"{label:<17}", f"bold {ui.T.text}"),
                                     (hint, ui.T.muted)), id=oid)
                for oid, key, label, hint in opts
            ], id="quitopts")

    def on_mount(self) -> None:
        self.query_one("#quitopts").focus()

    @on(OptionList.OptionSelected, "#quitopts")
    def _chosen(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    def action_pick(self, what: str) -> None:
        if what in ("out", "stay") and not self.run:
            return
        if what == "save" and self.run:
            return
        self.dismiss(what)


# ── main screen ──────────────────────────────────────────────────────────────

class MainScreen(Screen):
    BINDINGS = [
        Binding("ctrl+n", "new_item", "add item", show=False),
        Binding("ctrl+s", "punch_in", "punch in", show=False),
        Binding("ctrl+s", "punch_out", "punch out", show=False),
        Binding("tab", "toggle_area", "switch window"),
        Binding("ctrl+tab", "toggle_area", "switch window", show=False),
        Binding("ctrl+shift+tab", "toggle_area", "switch window", show=False),
        Binding("shift+tab", "toggle_area", "switch window", show=False),
        Binding("ctrl+p", "app.command_palette", "commands", show=False),
        Binding("f1", "keys", "shortcuts", show=False),
        Binding("ctrl+t", "edit_block", "edit times", show=False),
        Binding("ctrl+l", "log", "week log", show=False),
        Binding("f5", "preview", "preview", show=False),
        Binding("ctrl+b", "toggle_side", "side panel", show=False),
    ]
    # Arrow moves inside the top section: (from, direction) -> to.
    NAV = {
        ("new", "right"): "today",
        ("items", "right"): "today",
        ("today", "down"): "week",
        ("today", "left"): "new",
        ("week", "up"): "today",
        ("week", "left"): "new",
    }

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(id="banner")
            yield Static(id="mascot")
        with Horizontal(id="main"):
            with Vertical(id="now"):
                yield Static(id="clock")
                yield Label("working on", id="items-label", classes="section")
                yield ItemList(id="items")
                yield ItemInput(id="new")
            with Vertical(id="side"):
                yield FocusPanel(id="today")
                yield FocusPanel(id="week")
        yield OptionList(id="slash")
        with Horizontal(id="prompt"):
            yield Static("❯", id="caret")
            yield CommandInput(id="cmd", placeholder="in · out · undo · / for views and config")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.cfg = config_mod.load()
        self.running: Session | None = None
        self._notes_shown: tuple | None = None
        self.renaming: int | None = None
        self._ticks = 0
        self._side_pref: bool | None = None
        self.query_one("#banner", Static).update(ui.banner())
        self._frame = 0
        self.query_one("#mascot", Static).update(mascot.frame(0))
        self.set_interval(0.12, self._mascot_tick)
        self.query_one("#slash", OptionList).can_focus = False
        self.query_one("#today").border_title = "today"
        self.reload()
        self.set_interval(1, self._tick)
        self.query_one("#cmd", Input).focus()
        if self.cfg["ui"].get("onboarded") is False:
            self.call_after_refresh(self._onboard)
        else:
            self._check_last_session()

    def _onboard(self) -> None:
        def done(_ok: bool | None) -> None:
            self.app.reload_config()
            self.query_one("#cmd", Input).focus()
            self.app.notify("Type [b]in[/b] below to punch in.", title="all set")
        self.app.push_screen(OnboardingScreen(self.cfg), done)

    def on_resize(self) -> None:
        self.query_one("#top").display = self.cfg["ui"].get("banner", True) and self.size.height >= 36
        self.query_one("#mascot").display = self.cfg["ui"].get("mascot", True) and self.size.width >= 104
        self._layout_side()
        self.call_after_refresh(self.reload)

    def _layout_side(self) -> None:
        """Side panels hide themselves in narrow windows (ctrl+b overrides)."""
        pref = getattr(self, "_side_pref", None)
        self.query_one("#side").display = (self.size.width >= 100) if pref is None else pref

    # ── crash recovery ──
    def _check_last_session(self) -> None:
        hb = read_heartbeat()
        write_heartbeat()
        if not hb:
            return
        if hb.get("draft"):
            self.query_one("#new", Input).value = hb["draft"]
        if hb.get("clean"):
            return
        run = self.running
        last = datetime.fromisoformat(hb["at"])
        if run and run.start < last and datetime.now() - last > timedelta(minutes=10):
            def picked(what: str | None) -> None:
                if what == "end":
                    self._punch_out(end=last.replace(second=0, microsecond=0))
            self.app.push_screen(RecoverScreen(run, last), picked)
        else:
            self.app.notify("cram closed unexpectedly last time. Nothing was lost.", title="recovered")

    def _mascot_tick(self) -> None:
        """Asleep off the clock; the bell wakes him when you punch in; then he fights."""
        running = getattr(self, "running", None) is not None
        mode = getattr(self, "_mode", None)
        if mode is None:
            self._mode, self._frame = ("fight" if running else "sleep"), 0
        elif running and mode == "sleep":
            self._mode, self._frame = "bell", 0
        elif not running and mode != "sleep":
            self._mode, self._frame = "sleep", 0
        elif mode == "bell" and self._frame >= mascot.BELL_LEN:
            self._mode, self._frame = "fight", 0
        m = self.query_one("#mascot", Static)
        if m.display and self.query_one("#top").display and self.app.screen is self:
            self._frame += 1
            m.update(mascot.frame(self._frame, self._mode))

    def _tick(self) -> None:
        self.reload()
        self._ticks += 1
        if self._ticks % 5 == 0:
            write_heartbeat(draft=self.query_one("#new", Input).value)

    # ── state → widgets ──
    def reload(self) -> None:
        store = Store.load()
        run = store.running()
        if (run is None) != (self.running is None):
            self.running = run
            self.refresh_bindings()
        self.running = run
        at = datetime.now()

        clock_w = self.query_one("#clock").content_size.width or 40
        roomy = self.size.height >= 30
        side_hidden = not self.query_one("#side").display
        stamp = Text(f"{at:%a %b} {at.day} · {fmt_time(at)}" if clock_w >= 44 else fmt_time(at), style=ui.T.dim)
        head = Table.grid(expand=True)
        head.add_column()
        head.add_column(justify="right")
        nowbox = self.query_one("#now")
        new = self.query_one("#new", Input)
        if run:
            head.add_row(ui.chip("● ON THE CLOCK", ui.T.ok), stamp)
            if clock_w >= 32 and self.size.height >= 22:
                clock = ui.big(ui.clock_str(run.seconds(at)))
            else:
                clock = ui.gradient(ui.clock_str(run.seconds(at)))
            meta = Text.assemble(("punched in at ", ui.T.dim), (fmt_time(run.start), f"bold {ui.T.text}"))
            nowbox.border_title = "now"
            new.placeholder = "+ what are you working on?  enter to add"
            new.disabled = False
            notes = tuple(run.notes)
        else:
            today_min = sum(s.minutes() for s in store.on_day(date.today()))
            head.add_row(ui.chip("○ OFF THE CLOCK", ui.T.dim), stamp)
            hhmm = ui.clock_str(today_min * 60, with_seconds=False)
            if clock_w >= 20 and self.size.height >= 22:
                clock = ui.big(hhmm, stops=[ui.T.dim, ui.T.muted])
            else:
                clock = ui.gradient(hhmm, stops=[ui.T.dim, ui.T.muted])
            label = Text("time worked today", style=ui.T.muted)
            meta = Text("")
            nowbox.border_title = "cram"
            new.placeholder = "punch in first: type in below"
            notes = ()
        if side_hidden:
            wk_min = sum(s.minutes() for s in store.in_week(week_start(date.today())))
            if run:
                day_min = sum(s.minutes() for s in store.on_day(date.today()))
                meta.append(f"  ·  today {fmt_hm(day_min)} · week {fmt_hm(wk_min)}", style=ui.T.dim)
            else:
                meta.append(f"this week {fmt_hm(wk_min)}", style=ui.T.dim)
        gap = [Text("")] if roomy else []
        parts = [head, *gap]
        if not run:
            parts.append(label)
        parts.append(clock)
        if meta.plain:
            parts += [*gap, meta]
        self.query_one("#clock", Static).update(Group(*parts))
        self.query_one("#items-label").styles.margin = (1 if roomy else 0, 0, 0, 0)
        nowbox.set_class(run is not None, "running")

        if notes != self._notes_shown:
            self._notes_shown = notes
            ol = self.query_one("#items", ItemList)
            keep = ol.highlighted
            ol.clear_options()
            ol.add_options([_item_text(i, s) for i, s in enumerate(notes)])
            if notes and keep is not None:
                ol.highlighted = min(keep, len(notes) - 1)
            ol.display = bool(notes)
            self.query_one("#items-label").display = bool(notes)

        today = self.query_one("#today", FocusPanel)
        today.update(_today_view(store))
        today.border_subtitle = "enter to open" if today.has_focus else ""
        r = report.build(store, self.cfg, week_start(date.today()))
        week = self.query_one("#week", FocusPanel)
        week.border_title = f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)}"
        if run:
            week.border_subtitle = "● in progress"
        else:
            week.border_subtitle = "approved" if r.approved else "pending approval"
        week.set_class(run is not None, "unsaved")
        bar_w = max(4, min(26, (week.content_size.width or 56) - 30))
        week.update(Group(ui.week_bars(store, r.monday, at, width=bar_w), Text(""), ui.totals_line(r)))
        self._status(run, r)

    def _status(self, run: Session | None, r: report.Report) -> None:
        draft = self.query_one("#new", Input).value.strip()
        left = Text()
        wide = self.size.width >= 110
        if draft:  # the only thing that isn't on disk yet
            left.append("● unsaved  ", style=f"bold {ui.T.warn}")
            left.append("typed item not added yet (enter)" if wide else "typed item (enter)", style=ui.T.muted)
            if run:
                left.append(f"  ·  on the clock since {fmt_time(run.start)}", style=ui.T.dim)
        elif run:
            left.append("● on the clock  ", style=f"bold {ui.T.ok}")
            left.append(f"since {fmt_time(run.start)}", style=ui.T.muted)
            left.append("  ·  everything saved" + ("  (out to finish the block)" if wide else ""), style=ui.T.dim)
        else:
            left.append("✓ saved  ", style=f"bold {ui.T.ok}")
            left.append("week log is up to date", style=ui.T.dim)
        self.status_text = left.plain
        right = Text("week approved 🔒" if r.approved else "week pending approval", style=ui.T.dim)
        if self.size.width < 100:
            right = Text("")
        g = Table.grid(expand=True)
        g.add_column()
        g.add_column(justify="right")
        g.add_row(left, right)
        self.query_one("#status", Static).update(g)

    def check_action(self, action: str, parameters) -> bool | None:
        running = getattr(self, "running", None) is not None
        if action == "punch_in":
            return not running
        if action == "punch_out":
            return running
        return True

    # ── focus ──
    def _is_shown(self, wid: str) -> bool:
        w = self.query_one(f"#{wid}")
        return bool(w.display and w.parent.display)

    def action_toggle_area(self) -> None:
        """Tab: jump between the prompt bar and the top section, which
        remembers where you were in it."""
        cur = self.focused
        if cur is not None and cur.id != "cmd":
            self._last_top = cur.id
            self.query_one("#cmd").focus()
        else:
            last = getattr(self, "_last_top", None)
            self.query_one(f"#{last if last and self._is_shown(last) else 'new'}").focus()
        self.query_one("#slash").display = False
        self.reload()

    def nav(self, src: str, direction: str) -> None:
        """Arrow keys inside the top section."""
        dst = self.NAV.get((src, direction))
        if dst and not self._is_shown(dst) and dst == "today":
            dst = "week" if self._is_shown("week") else None
        if dst and self._is_shown(dst):
            self.query_one(f"#{dst}").focus()
            self.reload()

    @on(FocusPanel.Opened)
    def _open_panel(self, ev: FocusPanel.Opened) -> None:
        if ev.panel_id == "today":
            self.app.push_screen(DayScreen(date.today()))
        else:
            self.action_log()

    # ── punching ──
    def _start(self, text: str) -> None:
        store = Store.load()
        t = now()
        if store.is_approved(week_start(t.date())):
            self.app.notify("This week is already approved. Unapprove it from the palette first.", severity="error")
            return
        o = store.overlap(t, None)
        if o:
            self.app.notify(f"Overlaps block #{o.id}.", severity="error")
            return
        notes = [text] if text else []
        draft = self.query_one("#new", Input).value.strip()
        if draft:
            notes.append(draft)
            self.query_one("#new", Input).value = ""
        store.add(t, None, notes)
        store.save()
        self.app.notify(f"Punched in at {fmt_time(t)}", title="● on the clock")
        self.reload()
        self.query_one("#new", Input).focus()

    def _punch_out(self, end: datetime | None = None, then=None) -> None:
        store = Store.load()
        run = store.running()
        if not run:
            self.app.notify("You're not on the clock.")
            return
        inp = self.query_one("#new", Input)
        if inp.value.strip():
            run.notes.append(inp.value.strip())
            inp.value = ""
            store.save()

        def done(saved: bool | None) -> None:
            if saved:
                s = Store.load().get(run.id)
                self.app.notify(f"{fmt_range(s.start, s.end)} · {fmt_hm(s.minutes())}", title="✓ block saved")
                if then:
                    then()
                    return
            self.reload()
            self.query_one("#cmd", Input).focus()

        self.app.push_screen(BlockEditor(run, "out", end=end), done)

    # ── prompt bar ──
    @on(Input.Changed, "#cmd")
    def _typing(self, event: Input.Changed) -> None:
        menu = self.query_one("#slash", OptionList)
        v = event.value
        if not v.startswith("/") or " " in v:
            menu.display = False
            return
        matches = [c for c in SLASH if c[0].lower().startswith(v.lower())]
        menu.clear_options()
        menu.add_options([
            Option(Text.assemble((f"{cmd:<13}", f"bold {ui.T.accent}"), (f"  {what}", ui.T.text),
                                 (f"   {eg}" if eg else "", ui.T.dim)), id=cmd)
            for cmd, what, eg in matches
        ])
        menu.display = bool(matches)
        if matches:
            menu.highlighted = 0

    @on(OptionList.OptionSelected, "#slash")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.query_one("#cmd", Input).value = ""
        self.query_one("#slash").display = False
        self.slash(event.option.id, "")

    @on(Input.Submitted, "#cmd")
    def _command(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        menu = self.query_one("#slash", OptionList)
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            if cmd.lower() not in {c[0].lower() for c in SLASH} and menu.display and menu.highlighted is not None:
                cmd = menu.get_option_at_index(menu.highlighted).id
            menu.display = False
            self.slash(cmd.lower(), arg.strip())
            return
        word, _, rest = text.partition(" ")
        word = word.lower()
        if word == "in":
            if self.running:
                self.app.notify("Already on the clock.")
            else:
                self._start(rest.strip())
        elif word == "out":
            self._punch_out()
        elif word in ("undo", "redo"):
            n = int(rest) if rest.strip().isdigit() else 1
            for _ in range(n):
                getattr(self.app, f"action_{word}")()
        else:
            event.input.value = text
            self.app.notify("Commands are [b]in[/b], [b]out[/b], [b]undo[/b], [b]redo[/b] or [b]/…[/b]. "
                            "Items go in the working-on box ([b]tab[/b]).", severity="warning")

    def slash(self, cmd: str, arg: str) -> None:
        cmd = cmd.lower()
        try:
            if cmd == "/vday":
                self.app.push_screen(DayScreen(parse_date(arg) if arg else date.today()))
            elif cmd == "/vweek":
                self.app.push_screen(LogScreen(parse_week(arg or None)))
            elif cmd in ("/previewlogs", "/vlog"):
                self.app.run_cli("preview", *(["-w", arg] if arg else []))
            elif cmd == "/exportlogs":
                self.export_logs(arg)
            elif cmd == "/weeks":
                self.app.push_screen(WeekPicker())
            elif cmd == "/config":
                self.action_config()
            elif cmd == "/help":
                self.action_keys()
            else:
                self.app.notify(f"Unknown command {cmd}. Type / to see them.", severity="warning")
        except ParseError as e:
            self.app.notify(str(e), severity="error")

    # ── working-on box ──
    @on(Input.Changed, "#new")
    def _draft_changed(self) -> None:
        self._status(self.running, report.build(Store.load(), self.cfg, week_start(date.today())))

    @on(Input.Submitted, "#new")
    def _add_item(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text.lower() in ("out", "in"):  # a command typed in the wrong box: just do it
            event.input.value = ""
            if text.lower() == "out":
                self._punch_out()
            elif not self.running:
                self._start("")
            return
        if not self.running:
            self.app.notify("Punch in first: type [b]in[/b] in the prompt bar (your text is kept).")
            self.query_one("#cmd", Input).focus()
            return
        event.input.value = ""
        store = Store.load()
        run = store.running()
        if self.renaming is not None and self.renaming <= len(run.notes):
            run.notes.insert(self.renaming, text)
            self.renaming = None
        else:
            run.notes.append(text)
        store.save()
        self.reload()

    @on(ItemList.Removed)
    def _remove(self, ev: ItemList.Removed) -> None:
        store = Store.load()
        run = store.running()
        if run and ev.index < len(run.notes):
            del run.notes[ev.index]
            store.save()
            self.reload()

    @on(ItemList.Renamed)
    def _rename(self, ev: ItemList.Renamed) -> None:
        store = Store.load()
        run = store.running()
        if run and ev.index < len(run.notes):
            inp = self.query_one("#new", Input)
            inp.value = run.notes.pop(ev.index)
            self.renaming = ev.index
            store.save()
            self.reload()
            inp.focus()

    # ── actions ──
    def action_new_item(self) -> None:
        self.query_one("#new", Input).focus()

    def action_punch_in(self) -> None:
        self._start("")

    def action_punch_out(self) -> None:
        self._punch_out()

    def action_edit_block(self) -> None:
        store = Store.load()
        s = store.running() or store.last()
        if s and s.running:
            self.app.notify("Times are set when you punch out.")
            return
        if s:
            self.app.push_screen(BlockEditor(s, "edit"), lambda ok: self.reload())

    def action_config(self) -> None:
        def done(saved: bool | None) -> None:
            self.app.reload_config()
            if saved:
                self.app.notify(f"rate ${float(self.cfg['invoice']['rate']):g}/hr · theme {self.cfg['ui']['theme']}"
                                f" · PDFs → {self.cfg['export']['dir']}", title="✓ config saved")
        self.app.push_screen(ConfigScreen(self.cfg), done)

    def action_keys(self) -> None:
        self.app.push_screen(KeysSheet())

    def action_log(self) -> None:
        self.app.push_screen(LogScreen())

    def export_logs(self, arg: str = "") -> None:
        """Save the week's PDF into the export folder (approving it first if needed)."""
        store = Store.load()
        if arg:
            monday = parse_week(arg)
        else:
            cur = week_start(date.today())
            monday = cur if store.in_week(cur) or not store.in_week(cur - timedelta(weeks=1)) else cur - timedelta(weeks=1)
        if not store.in_week(monday):
            self.app.notify(f"Nothing logged for the week of {fmt_md(monday)}.", severity="warning")
            return
        if not store.is_approved(monday):
            self.app.notify("This week isn't approved yet, so approve it first (you'll be asked to export at the end).")
            self.app.run_cli("approve", "-w", monday.isoformat())
            return
        from . import pdf
        r = report.build(store, self.cfg, monday)
        path = report.export_path(self.cfg, r)
        pdf.render(r, self.cfg, path)
        store = Store.load()
        store.weeks[monday.isoformat()].setdefault("exports", []).append(
            {"path": str(path), "at": datetime.now().isoformat(timespec="seconds")})
        store.save()
        self.app.notify(str(path).replace(str(Path.home()), "~"), title="✓ PDF saved", timeout=8)

    def action_preview(self) -> None:
        self.app.run_cli("preview")

    def action_toggle_side(self) -> None:
        self._side_pref = not self.query_one("#side").display
        self._layout_side()
        self.reload()


# ── command palette ──────────────────────────────────────────────────────────

class CramCommands(Provider):
    def _commands(self):
        app: CramApp = self.app  # type: ignore[assignment]
        main = app.main
        return [
            ("Punch in / out", "ctrl+s · or type in / out", lambda: main.action_punch_out() if main.running else main.action_punch_in()),
            ("Add item", "ctrl+n · what you're working on", main.action_new_item),
            ("View day", "/vday", lambda: main.slash("/vday", "")),
            ("View week", "/vweek · ctrl+l", main.action_log),
            ("Preview log + invoice", "/previewLogs · f5", main.action_preview),
            ("Export log + invoice PDF", "/exportLogs", lambda: main.export_logs()),
            ("Pick a week…", "/weeks · preview, export or approve any week", lambda: app.push_screen(WeekPicker())),
            ("Undo", "ctrl+z · or type undo", app.action_undo),
            ("Redo", "ctrl+y · or type redo", app.action_redo),
            ("Config: rate and theme", "/config", main.action_config),
            ("Add a past block", "forgot to punch in? log it here", lambda: app.push_screen(
                BlockEditor(None, "add"), lambda ok: main.reload())),
            ("Edit last block times", "ctrl+t", main.action_edit_block),
            ("Approve week", "review, set billed hours, lock it", lambda: app.run_cli("approve")),
            ("Unapprove week", "unlock a week to edit it", lambda: app.run_cli("unapprove")),
            ("Weeks history", "hours, invoices, status", lambda: app.run_cli("weeks")),
            ("Edit full config file", "name, client, address…", lambda: app.run_cli("config", "--edit", wait=False)),
            ("Keyboard shortcuts", "f1", main.action_keys),
            ("Toggle side panel", "ctrl+b", main.action_toggle_side),
            ("Quit", "ctrl+q", app.action_quit),
        ]

    async def discover(self) -> Hits:
        for name, help_, cb in self._commands():
            yield DiscoveryHit(name, cb, help=help_)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_, cb in self._commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), cb, help=help_)


# ── app ──────────────────────────────────────────────────────────────────────

class CramApp(App):
    TITLE = "cram"
    BINDINGS = [
        Binding("ctrl+z", "undo", "undo", show=False),
        Binding("ctrl+y,ctrl+shift+z", "redo", "redo", show=False),
        Binding("ctrl+q", "quit", "quit", priority=True),
        Binding("ctrl+p", "command_palette", "commands", priority=True, show=False),
    ]
    COMMANDS = {CramCommands}
    COMMAND_PALETTE_BINDING = "ctrl+p"
    CSS = """
    Screen { background: $background; }
    #top { height: auto; }
    #banner { width: auto; height: auto; padding: 3 2 0 2; }
    #mascot { width: 50; height: auto; padding: 1 0 0 3; }
    #main { height: 1fr; padding: 0 1; }
    #now {
        width: 3fr; border: round $foreground 25%; border-title-color: $accent;
        border-title-style: bold; padding: 1 2 0 2;
    }
    #now.running { border: round $success 70%; border-title-color: $success; }
    #clock { height: auto; }
    .section { color: $text-muted; margin-top: 1; }
    #items { height: auto; max-height: 1fr; background: transparent; border: none; padding: 0; }
    #items:focus { background: $boost; }
    #new { margin: 1 0 0 0; border: tall $foreground 20%; background: transparent; }
    #new:focus { border: tall $accent; }
    #side { width: 2fr; min-width: 44; }
    FocusPanel {
        height: auto; border: round $foreground 25%; border-title-color: $accent;
        border-title-style: bold; border-subtitle-color: $text-muted; padding: 1 2;
    }
    FocusPanel:focus { border: round $accent; background: $boost; }
    #week { height: 1fr; }
    #week.unsaved { border-subtitle-color: $success; border-subtitle-style: bold; }

    #slash {
        display: none; height: auto; max-height: 14; margin: 0 1; border: round $foreground 25%;
        background: $surface; padding: 0 1; scrollbar-size-vertical: 0;
    }
    #slash > .option-list--option-highlighted { background: $primary 35%; text-style: none; }
    #prompt { height: 3; margin: 0 1; border: round $foreground 30%; padding: 0 1; }
    #prompt:focus-within { border: round $accent; }
    #caret { width: 2; color: $accent; text-style: bold; }
    #cmd { border: none; height: 1; padding: 0; background: transparent; }
    #status { height: 1; padding: 0 3; }

    BlockEditor, Confirm, KeysSheet, ConfigScreen, RecoverScreen, QuitScreen, ConflictScreen, OnboardingScreen {
        align: center middle; background: $background 60%;
    }
    #dialog {
        width: 76; height: auto; max-height: 90%; border: round $accent; border-title-color: $accent;
        border-title-style: bold; background: $surface; padding: 1 2;
    }
    #times { height: auto; }
    #times Label { padding: 1 1 0 0; color: $text-muted; }
    #times Input { width: 1fr; border: tall $foreground 20%; background: transparent; }
    #times Input:focus, #extra:focus, #rate:focus { border: tall $accent; }
    #span { margin: 1 0 0 0; }
    #dialog #items { max-height: 12; }
    #question { margin-top: 1; color: $accent; text-style: bold; }
    #extra, #rate { border: tall $foreground 20%; background: transparent; }
    .field { color: $text-muted; margin-top: 1; }
    #quitopts { height: auto; margin-top: 1; background: transparent; border: none; }
    #quitopts > .option-list--option-highlighted { background: $primary 35%; text-style: none; }
    #themes, #naming { height: auto; background: transparent; border: round $foreground 20%; }
    #themes:focus, #naming:focus { border: round $accent; }
    #themes > .option-list--option-highlighted, #naming > .option-list--option-highlighted {
        background: $primary 35%; text-style: none;
    }
    #folder { border: tall $foreground 20%; background: transparent; }
    #folder:focus { border: tall $accent; }
    #error { height: auto; margin-top: 1; }
    #dialog.onboard { width: 80; overflow-y: auto; }
    .ob-step { height: auto; }
    ObPage { height: auto; }
    #ob-banner { margin-bottom: 1; }
    .ob-input { border: tall $foreground 20%; background: transparent; }
    .ob-input:focus { border: tall $accent; }
    #ob-steps { margin-top: 1; }
    #hint { color: $text-muted; margin-top: 1; }
    #keys { width: 96; }
    #confirm { width: 72; }
    #confirm.wide { width: 80; }
    #confirm, #keys {
        max-width: 95%; height: auto; border: round $accent; border-title-color: $accent;
        border-title-style: bold; background: $surface; padding: 1 3;
    }
    #logbox {
        border: round $foreground 25%; border-title-color: $accent; border-title-style: bold;
        border-subtitle-color: $text-muted; margin: 1 1 0 1; padding: 1 2; height: 1fr;
    }
    #logbox.unsaved { border-subtitle-color: $success; border-subtitle-style: bold; }
    #log, #daylog { height: 1fr; background: transparent; }
    #weekhead { height: 1; padding: 0 1; }
    #weeklist { height: 1fr; background: transparent; border: none; }
    #weeklist > .option-list--option-highlighted { background: $primary 35%; text-style: none; }
    #weekinfo { height: auto; margin-top: 1; }
    #dayempty { height: auto; }
    #daytl, #weektl { height: auto; margin-top: 1; }
    #logtotal { height: auto; margin-top: 1; }
    """

    def __init__(self):
        super().__init__()
        self.register_theme(LEONIDA)
        self.cfg = config_mod.load()
        ui.set_theme(self.cfg["ui"].get("theme", "catppuccin"))

    def on_mount(self) -> None:
        self.theme = TEXTUAL_THEMES.get(self.cfg["ui"].get("theme", "catppuccin"), "catppuccin-mocha")
        self.main = MainScreen()
        self.push_screen(self.main)

    def apply_theme(self, name: str) -> None:
        ui.set_theme(name)
        self.theme = TEXTUAL_THEMES.get(name, "catppuccin-mocha")
        if hasattr(self, "main"):
            self.main._notes_shown = None
            self.main.query_one("#banner", Static).update(ui.banner())
            self.main.reload()

    def reload_config(self) -> None:
        self.cfg = config_mod.load()
        self.main.cfg = self.cfg
        self.apply_theme(self.cfg["ui"].get("theme", "catppuccin"))

    async def action_quit(self) -> None:
        main = getattr(self, "main", None)
        if main is None:
            self.exit()
            return
        if isinstance(self.screen, QuitScreen):
            return
        draft = main.query_one("#new", Input).value.strip()

        def chosen(what: str | None) -> None:
            if what in (None, "cancel"):
                return
            if what == "out":
                main._punch_out(then=self._finish_quit)  # the typed item goes into the block too
                return
            if what == "discard":
                run = main.running

                def drop(yes: bool | None) -> None:
                    if not yes:
                        return
                    if run:
                        store = Store.load()
                        store.remove(run.id)
                        store.save()
                    main.query_one("#new", Input).value = ""
                    self._finish_quit()

                if run:
                    self.push_screen(Confirm(
                        f"Throw away the block from {fmt_time(run.start)} ({fmt_hm(run.minutes())})?\n"
                        "It won't be logged."), drop)
                else:
                    drop(True)
                return
            if what == "stay" and draft:
                store = Store.load()
                run = store.running()
                if run:
                    run.notes.append(draft)
                    store.save()
                    main.query_one("#new", Input).value = ""
            self._finish_quit()

        main.reload()
        self.push_screen(QuitScreen(main.running, draft), chosen)

    def action_undo(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return  # finish or close the dialog first
        label = history.undo()
        self.notify(f"↶ undid: {label}" if label else "Nothing left to undo.")
        self._refresh_screens()

    def action_redo(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        label = history.redo()
        self.notify(f"↷ redid: {label}" if label else "Nothing to redo.")
        self._refresh_screens()

    def _refresh_screens(self) -> None:
        for scr in self.screen_stack:
            if hasattr(scr, "_shown"):
                scr._shown = None
            if hasattr(scr, "_notes_shown"):
                scr._notes_shown = None
            if hasattr(scr, "reload"):
                try:
                    scr.reload()
                except Exception:  # noqa: BLE001  (a screen that isn't mounted yet)
                    pass

    def _finish_quit(self) -> None:
        draft = self.main.query_one("#new", Input).value.strip()
        write_heartbeat(clean=True, draft=draft)
        self.exit()

    def run_cli(self, *args: str, wait: bool = True) -> None:
        """Drop back to the normal terminal to run a `cram <cmd>` flow, then come back."""
        with self.suspend():
            subprocess.run([sys.executable, "-m", "cram", *args])
            if wait:
                try:
                    input("\n  ↵  back to cram ")
                except (EOFError, KeyboardInterrupt):
                    pass
        self.reload_config()


def run_tui() -> None:
    CramApp().run()
