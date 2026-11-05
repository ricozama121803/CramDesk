"""Full-screen, keyboard-driven cram (what you get from plain `cram`)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

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
    ("bottom bar", "(same words as the shell)"),
    ("  start [class]", "start a block (no class: pick one)"),
    ("  stop", "stop the block"),
    ("  add details …", "details for the block you're in"),
    ("  log [class] …", "log what you did for a class (class optional while studying)"),
    ("  add memo …", "a memo on the board (statics: … tags it with a class)"),
    ("  add block", "log a past block you forgot"),
    ("  create class …", "a new class"),
    ("  undo / redo", "take back your last change (or put it back)"),
    ("  /day /week /log /memo /classes /config", "views and settings"),
    ("", ""),
    ("tab", "switch between the bottom bar and the top section"),
    ("arrows", "move between details, memo, today and week"),
    ("enter (on a panel)", "open it"),
    ("ctrl+s", "start / stop"),
    ("ctrl+z · ctrl+y", "undo · redo"),
    ("ctrl+q", "quit"),
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
        what = Text(s.course or ui.NO_CLASS, style=f"bold {ui.T.text}" if s.course else ui.T.dim,
                    overflow="ellipsis", no_wrap=True)
        if s.notes:
            what.append(f"  {s.notes[0]}", style=ui.T.muted)
        g.add_row(rng, Text(fmt_hm(s.minutes()), style=ui.T.accent2), what)
    total = sum(s.minutes() for s in blocks)
    if blocks:
        g.add_row(Text("total", style=ui.T.dim), Text(fmt_hm(total), style=f"bold {ui.T.text}"), "")
    return g


def _notes_view(store: Store, monday: date, rows: int = 8):
    """The side panel version of the to-do list: open ones first, then a count of the rest."""
    mine, older = store.todos_for(monday)
    open_ = [t for t in mine if not t.done] + older
    done = [t for t in mine if t.done]
    if not open_ and not done:
        return Text.assemble(("nothing yet · type ", ui.T.dim), ("add memo email the TA", f"bold {ui.T.accent}"),
                             (" below", ui.T.dim))
    lines = []
    for t in open_[:rows]:
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append("☐ ", style=ui.T.warn if t in older else ui.T.accent)
        line.append(t.text, style=ui.T.text)
        if t.course:
            line.append(f" · {t.course}", style=ui.T.accent2)
        if t in older:
            line.append(f" · from {fmt_md(t.week)}", style=ui.T.dim)
        lines.append(line)
    more = len(open_) - len(lines)
    tail = []
    if more > 0:
        tail.append(f"+{more} more")
    if done:
        tail.append(f"{len(done)} done")
    if not open_:
        lines = [Text("all done for this week ✓", style=f"bold {ui.T.ok}")]
    if tail:
        lines.append(Text(" · ".join(tail), style=ui.T.dim))
    return Group(*lines)


# ── dialogs ──────────────────────────────────────────────────────────────────

class ItemList(OptionList):
    """A list of notes. delete removes, f2 renames (handled by the owner)."""

    BINDINGS = [
        Binding("delete,backspace", "remove", "remove note", show=False),
        Binding("f2", "rename", "rename note", show=False),
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
    """Confirm / edit / create a study session. mode: out | edit | add"""

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
        self.course: str | None = session.course if session else None
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
        titles = {"out": "stop session", "edit": "edit session", "add": "add a past session"}
        if self.live:
            titles["edit"] = "edit descriptions"
        if self.mode == "out":
            q = "Did you cover anything else?" if self.items else "What did you cover?"
        else:
            q = "Add a note"
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
            with Horizontal(id="classrow"):
                yield Label("class")
                classes = Store.load().classes
                yield Input(self.course or "", id="course",
                            placeholder=", ".join(classes[:4]) + (" …" if len(classes) > 4 else "") if classes
                            else "none yet: add classes with /classes")
            yield Label("what you covered", classes="section")
            yield ItemList(id="items")
            yield Label(q, id="question")
            yield ItemInput(id="extra", placeholder="type + enter, saved right away")
            yield Static(id="error")
            items_hint = (" · ", ("↑", "bold"), " notes: ", ("f2", "bold"), " rename ", ("del", "bold"), " remove")
            if self.mode == "out":
                hint = Text.assemble(("enter", "bold"), " adds a line (saved) · ", ("empty enter", "bold"), "/",
                                     ("ctrl+s", "bold"), " stops the session · ", ("esc", "bold"), " keep studying", *items_hint)
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
                (f"{fmt_time(st)}-now", f"bold {ui.T.ok}"), ("  ·  still running, times are set when you stop", ui.T.dim)))
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
        order = ["day", "start", "end", "course"]
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

    @on(Input.Submitted, "#course")
    def _course_done(self, event: Input.Submitted) -> None:
        store = Store.load()
        text = event.value.strip()
        if text:
            course, rest = store.match_class(text)
            if not course or rest:
                self._error(f"'{text}' isn't one of your classes (add it with /classes)")
                return
        else:
            course = None
        event.input.value = course or ""
        if course != self.course:
            self.course = course
            s = store.get(self.sid) if self.sid else None
            if s:
                s.course = course
                store.save()
                self.changed = True
                self._saved("class saved")
        self.query_one("#extra", Input).focus()

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
        return st, en

    def _commit(self, st: datetime, en: datetime, then) -> None:
        """Save this session's times + items, sorting out any overlap first."""
        store = Store.load()
        if conflict.overlapping(store, st, en, self.sid):
            def chosen(how: str | None) -> None:
                if how in (None, "back"):
                    self._error("times overlap another session; change them or pick a fix")
                    return
                fixed = Store.load()
                self.sid = conflict.apply(fixed, how, st, en, list(self.items), self.sid)
                fixed.get(self.sid).course = self.course
                fixed.save()
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
            s.start, s.end, s.notes, s.course = st, en, list(self.items), self.course
        else:
            self.sid = store.add(st, en, list(self.items), self.course).id
        store.save()
        self.changed = True
        then()

    def _persist_items(self, what: str = "saved") -> None:
        """Write the note list to disk right away."""
        if self.sid is None:
            # a brand-new past block: create it as soon as its times are valid
            times = self._valid_times()
            if times is None:
                self.query_one("#error", Static).update(
                    Text("fill in valid times above and this session will save", style=ui.T.warn))
                return
            self._commit(*times, then=lambda: self._saved("session saved"))
            return
        store = Store.load()
        s = store.get(self.sid)
        if s is None:
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
    """Two sessions claim the same time: pick how to fix it."""

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
            head = Text.assemble(("This session ", ui.T.text),
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
    """A table of sessions; enter (shown in the footer as "edit") opens the selected one."""

    BINDINGS = [Binding("enter", "select_cursor", "edit")]


class LogScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("f2", "edit", "edit", show=False),
        Binding("ctrl+n", "new", "new session"),
        Binding("delete", "delete", "delete"),
        Binding("left_square_bracket", "week(-1)", "prev week", show=False),
        Binding("right_square_bracket", "week(1)", "next week", show=False),
    ]

    def __init__(self, monday: date | None = None):
        super().__init__()
        self.monday = monday or week_start(date.today())

    def compose(self) -> ComposeResult:
        with Vertical(id="logbox") as v:
            v.border_title = "week"
            yield BlockTable(id="log", cursor_type="row", zebra_stripes=False)
            yield Static(id="weektl")
            yield Static(id="logtotal")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("day", "time", "dur", "class", "what")
        self.reload()

    def reload(self) -> None:
        cfg = config_mod.load()
        store = Store.load()
        r = report.build(store, cfg, self.monday)
        box = self.query_one("#logbox")
        box.border_title = f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)}   [ ] to switch weeks"
        live = any(s.running for s in store.in_week(self.monday))
        box.border_subtitle = "● session in progress" if live else ""
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
                      Text(s.course or "—", style=f"bold {ui.T.accent}" if s.course else ui.T.faint),
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
            store.remove(s.id)
            store.save()
            self.reload()

        self.app.push_screen(Confirm(f"Delete session #{s.id} ({fmt_md(s.day)} {fmt_range(s.start, s.end)})?"), done)


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
    ("/day", "a day's blocks", "/day yesterday"),
    ("/week", "the week, by day and class", "/week last"),
    ("/log", "what you did, per class", ""),
    ("/memo", "the memo board: add, check off", ""),
    ("/classes", "your classes", ""),
    ("/config", "color theme", ""),
    ("/help", "every key", ""),
]


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
    """Where you type what you're covering. Up jumps into the note list;
    right at the end of the text moves over to the side panels."""

    BINDINGS = [
        Binding("up", "to_list", show=False),
        Binding("down", "down", show=False),
        Binding("right", "right", show=False),
    ]

    def action_down(self) -> None:
        nav = getattr(self.screen, "nav", None)
        if nav:
            nav(self.id, "down")

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
    """A calendar of weeks: ↑↓ to pick one, enter to open its log."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("q", "app.pop_screen", "back", show=False),
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
            opts.append(Option(row, id=monday.isoformat()))
        ol.clear_options()
        ol.add_options(opts)
        ol.highlighted = len(opts) - 1 if select_last or keep is None else min(keep, len(opts) - 1)
        head = Text(f"{'':<5}", style=ui.T.dim)
        for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"):
            head.append(f"{name:>3}", style=f"bold {ui.T.muted}")
        head.append(f"   {'hours':>8}", style=f"bold {ui.T.muted}")
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


class DayScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("q", "app.pop_screen", "back", show=False),
        Binding("f2", "edit", "edit", show=False),
        Binding("ctrl+n", "new", "new session"),
        Binding("delete", "delete", "delete session"),
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
        t.add_column("class", key="class")
        t.add_column("what you covered", key="what")
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
                             f"  ·  {n} session{'s' if n != 1 else ''}")
        box_.border_subtitle = "● session in progress" if live else "enter to edit a session"
        box_.set_class(live, "unsaved")

        t = self.query_one(DataTable)
        snapshot = tuple((s.id, s.start, s.end, tuple(s.notes), s.minutes(), s.course) for s in blocks)
        if snapshot != self._shown:
            self._shown = snapshot
            keep = t.cursor_row
            t.clear()
            for s in blocks:
                rng = Text(fmt_range(s.start, s.end), style=f"bold {ui.T.ok}" if s.running else ui.T.text)
                if s.running:
                    rng.append("\n● running", style=f"bold {ui.T.ok}")
                what = ui.notes_text(s.notes) if s.notes else Text("— no description yet, enter to add one", style=ui.T.dim)
                cls = Text(s.course or "—", style=f"bold {ui.T.accent}" if s.course else ui.T.faint)
                t.add_row(rng, Text(fmt_hm(s.minutes()), style=ui.T.accent2), cls, what, key=str(s.id), height=None)
            # last row: always there, enter on it adds a block for this day
            t.add_row(Text("+", style=f"bold {ui.T.accent}"), "", "",
                      Text("add a session" + ("" if blocks else " (nothing logged this day yet)"), style=ui.T.muted),
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
            self.app.notify("Session updated.", title="✓ saved")
        self._shown = None
        self.reload()

    def action_new(self) -> None:
        self.app.push_screen(BlockEditor(None, "add", day=self.day), self._edited)

    def action_delete(self) -> None:
        s = self._selected()
        if not s:
            return
        if s.running:
            self.app.notify("That session is still running. Stop it first.", severity="warning")
            return

        def done(yes: bool | None) -> None:
            if not yes:
                return
            store = Store.load()
            store.remove(s.id)
            store.save()
            self._edited(False)

        self.app.push_screen(Confirm(f"Delete session {fmt_range(s.start, s.end)} on {fmt_md(s.day)}?"), done)


def _theme_options() -> list[Option]:
    return [Option(Text.assemble((f"{name:<12}", f"bold {ui.THEMES[name]['text']}"),
                                 *[("██", c) for c in ui.ramp(6, ui.THEMES[name]["grad"])]), id=name)
            for name in ui.THEMES]


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
            yield Label("color theme  (↑↓ previews)", classes="field")
            yield OptionList(*_theme_options(), id="themes")
            yield Static(id="error")
            yield Static(Text.assemble(("enter", "bold"), " save · ", ("esc", "bold"), " cancel · ",
                                       ("ctrl+o", "bold"), " full config file"), id="hint")

    def on_mount(self) -> None:
        ol = self.query_one("#themes", OptionList)
        names = list(ui.THEMES)
        ol.highlighted = names.index(self.theme0) if self.theme0 in names else 0
        ol.focus()

    @on(OptionList.OptionHighlighted, "#themes")
    def _preview(self, ev: OptionList.OptionHighlighted) -> None:
        self.app.apply_theme(ev.option.id)

    @on(OptionList.OptionSelected, "#themes")
    def _theme_done(self) -> None:
        self.action_save()

    def action_save(self) -> None:
        ol = self.query_one("#themes", OptionList)
        config_mod.set_value("ui", "theme", ol.get_option_at_index(ol.highlighted or 0).id)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.app.apply_theme(self.theme0)
        self.dismiss(False)

    def action_full(self) -> None:
        self.dismiss(False)
        self.app.run_cli("config", "--edit", wait=False)


class ClassesScreen(ModalScreen[bool]):
    """Your classes: type a name + enter to add one; on the list, delete removes it."""

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("delete", "remove", "remove", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as v:
            v.border_title = "classes"
            yield Static(id="classhead")
            yield OptionList(id="classlist")
            yield Label("add a class", classes="field")
            yield Input(id="newclass", placeholder="e.g. Linear Algebra, then enter")
            yield Static(id="error")
            yield Static(Text.assemble(("enter", "bold"), " adds · ", ("tab", "bold"), " to the list, ",
                                       ("delete", "bold"), " removes · ", ("esc", "bold"), " close",
                                       style=ui.T.muted), id="hint")

    def on_mount(self) -> None:
        self.reload()
        self.query_one("#newclass").focus()

    def reload(self) -> None:
        store = Store.load()
        monday = week_start(date.today())
        ol = self.query_one("#classlist", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        for c in store.classes:
            mine = [s for s in store.sessions if s.course == c]
            week = sum(s.minutes() for s in mine if s.week == monday)
            total = sum(s.minutes() for s in mine)
            ol.add_option(Option(Text.assemble((f"{c:<28}", f"bold {ui.T.text}"),
                                               (f"{fmt_hm(week) if week else '·':>9}", ui.T.text),
                                               (f"{fmt_hm(total) if total else '·':>10}", ui.T.accent2)), id=c))
        ol.display = bool(store.classes)
        if store.classes:
            ol.highlighted = min(keep or 0, len(store.classes) - 1)
        head = Text.assemble((f"{'class':<28}", f"bold {ui.T.muted}"), (f"{'this week':>9}", f"bold {ui.T.muted}"),
                             (f"{'total':>10}", f"bold {ui.T.muted}"))
        self.query_one("#classhead", Static).update(
            head if store.classes else Text("No classes yet. Add the ones you're taking below.", style=ui.T.dim))

    @on(Input.Submitted, "#newclass")
    def _add(self, ev: Input.Submitted) -> None:
        if not ev.value.strip():
            self.action_close()
            return
        store = Store.load()
        added = [store.add_class(p) for p in ev.value.split(",") if p.strip()]
        store.save()
        ev.input.value = ""
        self.query_one("#error", Static).update(Text(f"✓ added {', '.join(added)}", style=f"bold {ui.T.ok}"))
        self.reload()

    def action_remove(self) -> None:
        ol = self.query_one("#classlist", OptionList)
        if self.focused is not ol or ol.highlighted is None:
            return
        name = ol.get_option_at_index(ol.highlighted).id

        def done(yes: bool | None) -> None:
            if yes:
                store = Store.load()
                store.remove_class(name)
                store.save()
                self.query_one("#error", Static).update(
                    Text(f"removed {name} (past sessions keep it)", style=ui.T.muted))
                self.reload()
        self.app.push_screen(Confirm(f"Remove {name} from your classes? Past sessions keep it."), done)

    def action_close(self) -> None:
        self.dismiss(True)


class TodoScreen(ModalScreen[bool]):
    """The week's to-dos: type + enter adds one; on the list, enter/space checks it off, delete removes it."""

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("delete", "remove", "remove", show=False),
        Binding("space", "toggle", "check off", show=False),
        Binding("left_square_bracket", "week(-1)", "prev week", show=False),
        Binding("right_square_bracket", "week(1)", "next week", show=False),
    ]

    def __init__(self, monday: date | None = None):
        super().__init__()
        self.monday = monday or week_start(date.today())

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as v:
            v.border_title = "memo board"
            yield Static(id="todohead")
            yield OptionList(id="todolist")
            yield Label("add a memo  (start with a class and a colon to tag it: statics: review slides)", classes="field")
            yield Input(id="newtodo", placeholder="e.g. finish problem set 4, then enter")
            yield Static(id="error")
            yield Static(Text.assemble(("enter", "bold"), " adds · ", ("tab", "bold"), " to the list: ",
                                       ("enter", "bold"), " check off, ", ("delete", "bold"), " remove · ",
                                       ("[ ]", "bold"), " weeks · ", ("esc", "bold"), " close",
                                       style=ui.T.muted), id="hint")

    def on_mount(self) -> None:
        self.reload()
        self.query_one("#newtodo").focus()

    def reload(self) -> None:
        store = Store.load()
        mine, older = store.todos_for(self.monday)
        self.query_one("#dialog").border_title = (
            f"memo board · week {fmt_md(self.monday)} – {fmt_md(self.monday + timedelta(days=6))}")
        ol = self.query_one("#todolist", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        for t in mine:
            ol.add_option(Option(ui.todo_line(t), id=str(t.id)))
        if older:
            ol.add_option(Option(Text("still open from earlier weeks", style=f"bold {ui.T.warn}"), disabled=True))
            for t in older:
                ol.add_option(Option(ui.todo_line(t, show_week=True), id=str(t.id)))
        ol.display = bool(mine or older)
        if ol.option_count:
            ol.highlighted = min(keep or 0, ol.option_count - 1)
        summary = ui.todo_summary(store, self.monday)
        self.query_one("#todohead", Static).update(
            summary if summary.plain else Text("The memo board is empty. Memos for any class (or none) go here: no reminders.", style=ui.T.dim))

    def _selected(self):
        ol = self.query_one("#todolist", OptionList)
        if self.focused is not ol or ol.highlighted is None:
            return None
        opt = ol.get_option_at_index(ol.highlighted)
        return int(opt.id) if opt.id else None

    @on(Input.Submitted, "#newtodo")
    def _add(self, ev: Input.Submitted) -> None:
        if not ev.value.strip():
            self.action_close()
            return
        store = Store.load()
        t = store.add_todo(ev.value, self.monday)
        store.save()
        ev.input.value = ""
        self.query_one("#error", Static).update(Text.assemble(("✓ added ", f"bold {ui.T.ok}"), ui.todo_line(t)))
        self.reload()

    @on(OptionList.OptionSelected, "#todolist")
    def _picked(self) -> None:
        self.action_toggle()

    def action_toggle(self) -> None:
        tid = self._selected()
        if tid is None:
            return
        store = Store.load()
        t = store.get_todo(tid)
        t.done = not t.done
        store.save()
        self.reload()

    def action_remove(self) -> None:
        tid = self._selected()
        if tid is None:
            return
        store = Store.load()
        t = store.get_todo(tid)
        store.remove_todo(tid)
        store.save()
        self.query_one("#error", Static).update(Text(f"removed “{t.text}” (ctrl+z in the main screen undoes it)",
                                                     style=ui.T.muted))
        self.reload()

    def action_week(self, delta: int) -> None:
        self.monday += timedelta(weeks=delta)
        self.reload()

    def action_close(self) -> None:
        self.dismiss(True)


class ClassPicker(ModalScreen[str | None]):
    """Which class is this block for? ↑↓ + enter picks one, or type a new name."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        classes = Store.load().classes
        with Vertical(id="dialog") as v:
            v.border_title = "which class?"
            if classes:
                yield OptionList(*[Option(Text(c, style=f"bold {ui.T.text}"), id=c) for c in classes], id="picklist")
                yield Label("or a new class", classes="field")
            else:
                yield Static(Text("You don't have any classes yet. Name your first one:", style=ui.T.text))
            yield Input(id="pickname", placeholder="e.g. Statics, then enter")
            yield Static(Text.assemble(("enter", "bold"), " start · ", ("tab", "bold"), " switch · ",
                                       ("esc", "bold"), " cancel", style=ui.T.muted), id="hint")

    def on_mount(self) -> None:
        lists = self.query("#picklist")
        (lists.first() if lists else self.query_one("#pickname")).focus()

    @on(OptionList.OptionSelected, "#picklist")
    def _picked(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    @on(Input.Submitted, "#pickname")
    def _named(self, ev: Input.Submitted) -> None:
        name = ev.value.strip()
        if not name:
            return
        store = Store.load()
        name = store.add_class(name)
        store.save()
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ClassLogScreen(ModalScreen[bool]):
    """Your activity log, newest first under each class. Type 'statics finished ch 5' + enter to add."""

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("delete", "remove", "remove", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as v:
            v.border_title = "log"
            yield Static(id="noteshead")
            yield OptionList(id="notelist")
            yield Label("log what you did: the class, then what you did  (while studying, the class is optional)",
                        classes="field")
            yield Input(id="newnote", placeholder="e.g. statics finished ch 5, moments still shaky")
            yield Static(id="error")
            yield Static(Text.assemble(("enter", "bold"), " adds · ", ("tab", "bold"), " to the list, ",
                                       ("delete", "bold"), " removes · ", ("esc", "bold"), " close",
                                       style=ui.T.muted), id="hint")

    def on_mount(self) -> None:
        self.reload()
        self.query_one("#newnote").focus()

    def reload(self) -> None:
        store = Store.load()
        ol = self.query_one("#notelist", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        notes = store.notes_for()
        order = list(store.classes) + sorted({n.course for n in notes} - set(store.classes))
        for c in order:
            mine = [n for n in notes if n.course == c]
            if mine:
                ol.add_option(Option(Text(c, style=f"bold {ui.T.accent}"), disabled=True))
                for n in mine:
                    ol.add_option(Option(ui.note_line(n), id=str(n.id)))
        ol.display = bool(notes)
        if ol.option_count:
            ol.highlighted = min(keep or 1, ol.option_count - 1)
        self.query_one("#noteshead", Static).update(
            Text(f"{len(notes)} entr{'ies' if len(notes) != 1 else 'y'}", style=ui.T.dim) if notes
            else Text("Nothing logged yet. Write what you did, what's still shaky, anything.", style=ui.T.dim))

    @on(Input.Submitted, "#newnote")
    def _add(self, ev: Input.Submitted) -> None:
        text = ev.value.strip()
        if not text:
            self.action_close()
            return
        store = Store.load()
        course, rest = store.match_class(text)
        if not course:
            run = store.running()
            if run and run.course:
                course, rest = run.course, text
            else:
                self.query_one("#error", Static).update(Text(
                    "start with the class: e.g. statics finished ch 5", style=f"bold {ui.T.err}"))
                return
        if not rest:
            self.query_one("#error", Static).update(Text(f"write what you did after {course}", style=f"bold {ui.T.err}"))
            return
        n = store.add_note(course, rest)
        store.save()
        ev.input.value = ""
        self.query_one("#error", Static).update(Text(f"✓ logged for {n.course}", style=f"bold {ui.T.ok}"))
        self.reload()

    def action_remove(self) -> None:
        ol = self.query_one("#notelist", OptionList)
        if self.focused is not ol or ol.highlighted is None:
            return
        opt = ol.get_option_at_index(ol.highlighted)
        if not opt.id:
            return
        store = Store.load()
        store.remove_note(int(opt.id))
        store.save()
        self.query_one("#error", Static).update(Text("entry removed (ctrl+z in the main screen undoes it)",
                                                     style=ui.T.muted))
        self.reload()

    def action_close(self) -> None:
        self.dismiss(True)


class ObPage(Static, can_focus=True):
    """A setup page with nothing to type: enter moves on."""

    BINDINGS = [Binding("enter", "screen.next", show=False)]


class OnboardingScreen(ModalScreen[bool]):
    """First-run setup: a welcome, pick a color theme, then a quick tour.
    esc skips the rest (the theme is in /config later)."""

    AUTO_FOCUS = None
    BINDINGS = [
        Binding("escape", "skip", "skip setup"),
        Binding("ctrl+s", "next", "next", show=False),
    ]
    STEPS = [("welcome", "welcome"), ("classes", "your classes"), ("look", "pick a look"), ("tour", "you're set")]

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.step = 0
        self.theme0 = cfg["ui"].get("theme", "catppuccin")

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="onboard") as v:
            v.border_title = "set up cram"
            with Vertical(id="ob-welcome", classes="ob-step"):
                yield Static(ui.banner(), id="ob-banner")
                yield ObPage(Text.assemble(
                    ("Welcome! ", f"bold {ui.T.accent}"),
                    ("cram is a study clock for your terminal. Start a session, jot down what you're "
                     "studying, stop, and see how many hours you put in each day and week.\n\n", ui.T.text),
                    ("Two quick steps: add the classes you're taking, then pick a color theme. "
                     "You can change both later with ", ui.T.muted), ("/classes", f"bold {ui.T.text}"),
                    (" and ", ui.T.muted), ("/config", f"bold {ui.T.text}"), (".", ui.T.muted)))
            with Vertical(id="ob-classes", classes="ob-step"):
                yield Static(Text.assemble(
                    ("Add each class you study for. Then ", ui.T.text), ("start statics", f"bold {ui.T.accent}"),
                    (" logs time for it.\n", ui.T.text)))
                yield Label("class name  (enter adds it; empty enter when you're done)", classes="field")
                yield Input(id="ob-class", placeholder="e.g. Linear Algebra")
                yield Static(id="ob-classlist")
            with Vertical(id="ob-look", classes="ob-step"):
                yield Label("color theme  (↑↓ previews)", classes="field")
                yield OptionList(*_theme_options(), id="themes")
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
            ("start", "type it in the bar at the bottom (add a class: start statics)"),
            ("details", "type what you're covering as you go"),
            ("stop", "ends the block"),
            ("log …", "log what you did for the class you're studying"),
            ("add memo …", "a memo on the board, for any class"),
            ("tab", "switch between the bottom bar and the top section"),
            ("/", "views: a day, the week, notes, classes"),
            ("f1", "every key"),
        ]:
            t.add_row(k, v)
        return Group(Text("The basics:\n", style=f"bold {ui.T.text}"), t)

    def on_mount(self) -> None:
        names = list(ui.THEMES)
        self.query_one("#themes", OptionList).highlighted = names.index(self.theme0) if self.theme0 in names else 0
        self._show()

    def _show(self) -> None:
        sid, title = self.STEPS[self.step]
        for i, (other, _t) in enumerate(self.STEPS):
            self.query_one(f"#ob-{other}").display = i == self.step
        self.query_one("#dialog").border_title = f"set up cram · {title}"
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
        if sid == "look":
            self.query_one("#themes").focus()
        elif sid == "classes":
            self._show_classes()
            self.query_one("#ob-class").focus()
        else:
            self.query_one(f"#ob-{sid} ObPage").focus()

    @on(OptionList.OptionHighlighted, "#themes")
    def _preview(self, ev: OptionList.OptionHighlighted) -> None:
        self.app.apply_theme(ev.option.id)

    @on(OptionList.OptionSelected, "#themes")
    def _theme_done(self) -> None:
        self.action_next()

    def _show_classes(self) -> None:
        classes = Store.load().classes
        out = Text()
        for c in classes:
            out.append("  › ", style=ui.T.accent)
            out.append(f"{c}\n", style=f"bold {ui.T.text}")
        self.query_one("#ob-classlist", Static).update(out if classes else Text("  none yet", style=ui.T.dim))

    @on(Input.Submitted, "#ob-class")
    def _class_added(self, ev: Input.Submitted) -> None:
        if not ev.value.strip():
            self.action_next()
            return
        store = Store.load()
        for part in ev.value.split(","):
            if part.strip():
                store.add_class(part)
        store.save()
        ev.input.value = ""
        self._show_classes()

    def _save_step(self) -> None:
        if self.STEPS[self.step][0] == "look":
            ol = self.query_one("#themes", OptionList)
            config_mod.set_value("ui", "theme", ol.get_option_at_index(ol.highlighted or 0).id)

    def action_next(self) -> None:
        self._save_step()
        if self.step == len(self.STEPS) - 1:
            self._finish()
            return
        self.step += 1
        self._show()

    def action_skip(self) -> None:
        if self.step < len(self.STEPS) - 1:
            self.app.notify("Setup skipped. Pick a theme any time with /config.")
        self._finish()

    def _finish(self) -> None:
        config_mod.set_value("ui", "onboarded", True)
        self.dismiss(True)


class RecoverScreen(ModalScreen[str]):
    """Shown when cram died while you were studying."""

    BINDINGS = [
        Binding("k,escape", "pick('keep')", "still studying"),
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
                (f"Your session is still running, since {fmt_time(self.run.start)}", ui.T.text),
                (f" ({DAY_ABBR[self.run.day.weekday()]} {fmt_md(self.run.day)}).\n", ui.T.text),
                (f"cram last saw you at {fmt_time(self.last_seen)}, {fmt_hm(int(gap.total_seconds() // 60))} ago.", ui.T.muted),
            ))
            yield Static("")
            yield Static(Text.assemble(("k", f"bold {ui.T.accent}"), "  keep the session running (I'm still studying)\n",
                                       ("e", f"bold {ui.T.accent}"), f"  end the session at {fmt_time(self.last_seen)} (you can adjust it)"))

    def action_pick(self, what: str) -> None:
        self.dismiss(what)


class QuitScreen(ModalScreen[str]):
    """Are you sure? / stop or keep studying."""

    BINDINGS = [
        Binding("escape", "pick('cancel')", "cancel"),
        Binding("o", "pick('out')", "stop", show=False),
        Binding("k", "pick('stay')", "keep studying", show=False),
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
                    ("You're studying", f"bold {ui.T.ok}"),
                    (f" since {fmt_time(self.run.start)} ({fmt_hm(self.run.minutes())}).\n", ui.T.text),
                    ("Stop now, or keep the session running while cram is closed?", ui.T.text),
                ))
                opts = [("out", "o", "stop", "save the session, then quit"),
                        ("stay", "k", "keep studying", "the session keeps running"),
                        ("discard", "d", "don't save", "throw away this session" + (" and typed note" if self.draft else "")),
                        ("cancel", "esc", "cancel", "back to cram")]
            else:
                v.border_title = "quit cram?"
                yield Static(Text("Are you sure? Everything will be saved.", style=f"bold {ui.T.text}"))
                opts = [("save", "s", "save and quit", "everything is kept"),
                        ("discard", "d", "don't save", "throw away the typed note" if self.draft else "quit"),
                        ("cancel", "esc", "cancel", "back to cram")]
            if self.draft:
                where = "added to your session" if self.run else "kept for next time"
                yield Static(Text(f"Your typed note “{self.draft}” will be {where} unless you don't save.",
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
        Binding("ctrl+n", "new_item", "add details", show=False),
        Binding("ctrl+s", "punch_in", "start studying", show=False),
        Binding("ctrl+s", "punch_out", "stop", show=False),
        Binding("tab", "toggle_area", "switch window"),
        Binding("ctrl+tab", "toggle_area", "switch window", show=False),
        Binding("ctrl+shift+tab", "toggle_area", "switch window", show=False),
        Binding("shift+tab", "toggle_area", "switch window", show=False),
        Binding("ctrl+p", "app.command_palette", "commands", show=False),
        Binding("f1", "keys", "shortcuts", show=False),
        Binding("ctrl+t", "edit_block", "edit times", show=False),
        Binding("ctrl+l", "log", "week view", show=False),
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
        ("new", "down"): "notes",
        ("notes", "up"): "new",
        ("notes", "right"): "week",
    }

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(id="banner")
            yield Static(id="mascot")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                with Vertical(id="now"):
                    yield Static(id="clock")
                    yield Label("details", id="items-label", classes="section")
                    yield ItemList(id="items")
                    yield ItemInput(id="new")
                yield FocusPanel(id="notes")
            with Vertical(id="side"):
                yield FocusPanel(id="today")
                yield FocusPanel(id="week")
        yield OptionList(id="slash")
        with Horizontal(id="prompt"):
            yield Static("❯", id="caret")
            yield CommandInput(id="cmd", placeholder="start · stop · log … · add memo … · / for more")
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
        self.query_one("#notes").border_title = "memo"
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
            self.app.notify("Type [b]start[/b] below to begin.", title="all set")
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
        """Asleep when you're not studying; the bell wakes him when you start studying; then he swims."""
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
            head.add_row(ui.chip("● STUDYING", ui.T.ok), stamp)
            if clock_w >= 32 and self.size.height >= 22:
                clock = ui.big(ui.clock_str(run.seconds(at)))
            else:
                clock = ui.gradient(ui.clock_str(run.seconds(at)))
            meta = Text.assemble((run.course or "no class", f"bold {ui.T.accent}" if run.course else ui.T.dim),
                                 ("  ·  since ", ui.T.dim), (fmt_time(run.start), f"bold {ui.T.text}"))
            nowbox.border_title = run.course or "now"
            new.placeholder = "+ add details, enter to save"
            new.disabled = False
            notes = tuple(run.notes)
        else:
            today_min = sum(s.minutes() for s in store.on_day(date.today()))
            head.add_row(ui.chip("○ NOT STUDYING", ui.T.dim), stamp)
            hhmm = ui.clock_str(today_min * 60, with_seconds=False)
            if clock_w >= 20 and self.size.height >= 22:
                clock = ui.big(hhmm, stops=[ui.T.dim, ui.T.muted])
            else:
                clock = ui.gradient(hhmm, stops=[ui.T.dim, ui.T.muted])
            label = Text("studied today", style=ui.T.muted)
            meta = Text("")
            nowbox.border_title = "cram"
            new.placeholder = "type start below to begin"
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
        week.border_subtitle = "● in progress" if run else ""
        week.set_class(run is not None, "unsaved")
        bar_w = max(4, min(26, (week.content_size.width or 56) - 30))
        week.update(Group(ui.week_bars(store, r.monday, at, width=bar_w), Text(""),
                          ui.class_bars(store.in_week(r.monday), at, width=max(4, bar_w - 6)), Text(""), ui.totals_line(r)))
        notes_panel = self.query_one("#notes", FocusPanel)
        notes_panel.update(_notes_view(store, r.monday, rows=6 if self.size.height >= 36 else 3))
        summary = ui.todo_summary(store, r.monday).plain
        notes_panel.border_subtitle = "enter to open" if notes_panel.has_focus else summary
        self._status(run, r)

    def _status(self, run: Session | None, r: report.Report) -> None:
        draft = self.query_one("#new", Input).value.strip()
        left = Text()
        wide = self.size.width >= 110
        if draft:  # the only thing that isn't on disk yet
            left.append("● unsaved  ", style=f"bold {ui.T.warn}")
            left.append("typed note not added yet (enter)" if wide else "typed note (enter)", style=ui.T.muted)
            if run:
                left.append(f"  ·  studying since {fmt_time(run.start)}", style=ui.T.dim)
        elif run:
            left.append("● studying  ", style=f"bold {ui.T.ok}")
            left.append(f"since {fmt_time(run.start)}", style=ui.T.muted)
            left.append("  ·  everything saved" + ("  (stop to finish)" if wide else ""), style=ui.T.dim)
        else:
            left.append("✓ saved  ", style=f"bold {ui.T.ok}")
            left.append("week log is up to date", style=ui.T.dim)
        self.status_text = left.plain
        right = Text(f"this week {fmt_hm(r.total_minutes)}", style=ui.T.dim)
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
        elif ev.panel_id == "notes":
            self.action_todo()
        else:
            self.action_log()

    # ── punching ──
    def _start(self, text: str) -> None:
        """'in linear algebra ch 5' -> a session for that class with 'ch 5' as its first note."""
        store = Store.load()
        if not text:
            self._pick_then_begin()
            return
        course, rest = store.match_class(text)
        if course:
            self._begin(course, rest)
            return
        first = text.split()[0].lower()
        hits = [c for c in store.classes if len(first) >= 3 and c.lower().startswith(first)]
        if len(hits) > 1:
            self.app.notify(f"'{text.split()[0]}' could be {' or '.join(hits)}. Type more of the name.",
                            severity="warning")
            return

        def answered(add: bool | None) -> None:
            if add:
                st = Store.load()
                name = st.add_class(text)
                st.save()
                self._begin(name, "")
            else:
                self._pick_then_begin()
        self.app.push_screen(Confirm(f"'{text}' isn't one of your classes. Create it?"), answered)

    def _pick_then_begin(self) -> None:
        self.app.push_screen(ClassPicker(), lambda name: self._begin(name, "") if name else None)

    def _begin(self, course: str | None, text: str) -> None:
        store = Store.load()
        t = now()
        o = store.overlap(t, None)
        if o:
            self.app.notify(f"Overlaps session #{o.id}.", severity="error")
            return
        notes = [text] if text else []
        draft = self.query_one("#new", Input).value.strip()
        if draft:
            notes.append(draft)
            self.query_one("#new", Input).value = ""
        store.add(t, None, notes, course)
        store.save()
        self.app.notify(f"{course or 'Studying'} from {fmt_time(t)}", title="● studying")
        self.reload()
        self.query_one("#new", Input).focus()

    def _punch_out(self, end: datetime | None = None, then=None) -> None:
        """Stop the block right away (anything typed in the details box goes in first)."""
        store = Store.load()
        run = store.running()
        if not run:
            self.app.notify("You're not studying right now.")
            return
        inp = self.query_one("#new", Input)
        if inp.value.strip():
            run.notes.append(inp.value.strip())
            inp.value = ""
        run.end = max(end or now(), run.start + timedelta(minutes=1))
        store.save()
        self.app.notify(f"{run.course or 'Block'} · {fmt_range(run.start, run.end)} · {fmt_hm(run.minutes())}",
                        title="✓ stopped")
        if then:
            then()
            return
        self.reload()
        self.query_one("#cmd", Input).focus()

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
        words = text.split()
        w0, w1 = words[0].lower(), (words[1].lower() if len(words) > 1 else "")
        if w0 in ("start", "in"):
            if self.running:
                self.app.notify("Already studying. Type [b]stop[/b] first.")
            else:
                self._start(" ".join(words[1:]))
        elif w0 in ("stop", "out"):
            self._punch_out()
        elif w0 == "add" and w1 in ("details", "detail"):
            self._add_details(" ".join(words[2:]))
        elif w0 == "add" and w1 in ("memo", "todo"):
            self._add_from_bar("todo", " ".join(words[2:]))
        elif w0 == "add" and w1 == "block":
            self.app.push_screen(BlockEditor(None, "add"), lambda ok: self.reload())
        elif (w0 == "create" or w0 == "add") and w1 == "class":
            self._add_from_bar("class", " ".join(words[2:]))
        elif w0 in ("memo", "todo"):
            self._add_from_bar("todo", " ".join(words[1:]))
        elif w0 == "log":
            self._add_class_note(" ".join(words[1:]))
        elif w0 == "ls-memo":
            self.action_todo()
        elif w0 == "ls-log":
            self.app.push_screen(ClassLogScreen(), lambda _: self.reload())
        elif w0 in ("ls-class", "classes"):
            self.action_classes()
        elif w0 in ("undo", "redo"):
            n = int(w1) if w1.isdigit() else 1
            for _ in range(n):
                getattr(self.app, f"action_{w0}")()
        else:
            event.input.value = text
            self.app.notify("Try [b]start[/b], [b]stop[/b], [b]log …[/b], [b]add details …[/b], [b]add memo …[/b] "
                            "or [b]/[/b] for more.",
                            severity="warning")

    def _add_class_note(self, text: str) -> None:
        store = Store.load()
        course, rest = store.match_class(text)
        if not course:
            run = store.running()
            if run and run.course:
                course, rest = run.course, text
        if not course or not rest:
            self.app.push_screen(ClassLogScreen(), lambda _: self.reload())    # pick the class there
            return
        store.add_note(course, rest)
        store.save()
        self.app.notify(rest, title=f"✓ logged for {course}")

    def _add_details(self, text: str) -> None:
        store = Store.load()
        run = store.running()
        if not run:
            self.app.notify("You're not studying right now. Type [b]start[/b] first.", severity="warning")
            return
        if not text:
            self.query_one("#new", Input).focus()
            return
        run.notes.append(text)
        store.save()
        self.reload()

    def _add_from_bar(self, group: str, text: str) -> None:
        if not text:
            self.action_todo() if group == "todo" else self.action_classes()
            return
        store = Store.load()
        if group == "todo":
            t = store.add_todo(text, week_start(date.today()))
            msg = (f"{t.course} · " if t.course else "") + t.text
            title = "✓ memo added"
        else:
            msg = ", ".join(store.add_class(p) for p in text.split(",") if p.strip())
            title = "✓ class created"
        store.save()
        self.app.notify(msg, title=title)
        self.reload()

    def slash(self, cmd: str, arg: str) -> None:
        cmd = cmd.lower()
        try:
            if cmd in ("/day", "/vday"):
                self.app.push_screen(DayScreen(parse_date(arg) if arg else date.today()))
            elif cmd in ("/week", "/vweek"):
                self.app.push_screen(LogScreen(parse_week(arg or None)))
            elif cmd == "/weeks":
                self.app.push_screen(WeekPicker())
            elif cmd == "/config":
                self.action_config()
            elif cmd == "/classes":
                self.action_classes()
            elif cmd in ("/log", "/notes"):
                self.app.push_screen(ClassLogScreen(), lambda _: self.reload())
            elif cmd in ("/memo", "/todo"):
                self.action_todo()
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
        first, _, rest = text.partition(" ")
        if text.lower() in ("out", "stop"):          # a command typed in the wrong box: just do it
            event.input.value = ""
            self._punch_out()
            return
        if first.lower() in ("in", "start") and not self.running:
            event.input.value = ""
            self._start(rest.strip())
            return
        if not self.running:
            self.app.notify("Start first: type [b]start[/b] in the bottom bar (your text is kept).")
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
            self.app.notify("Times are set when you stop.")
            return
        if s:
            self.app.push_screen(BlockEditor(s, "edit"), lambda ok: self.reload())

    def action_config(self) -> None:
        def done(saved: bool | None) -> None:
            self.app.reload_config()
            if saved:
                self.app.notify(f"theme {self.cfg['ui']['theme']}", title="✓ config saved")
        self.app.push_screen(ConfigScreen(self.cfg), done)

    def action_todo(self) -> None:
        self.app.push_screen(TodoScreen(), lambda _: self.reload())

    def action_classes(self) -> None:
        self.app.push_screen(ClassesScreen(), lambda _: self.reload())

    def action_keys(self) -> None:
        self.app.push_screen(KeysSheet())

    def action_log(self) -> None:
        self.app.push_screen(LogScreen())

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
            ("Start / stop studying", "ctrl+s · or type in / out", lambda: main.action_punch_out() if main.running else main.action_punch_in()),
            ("Add note", "ctrl+n · what you're covering", main.action_new_item),
            ("View day", "/vday", lambda: main.slash("/vday", "")),
            ("View week", "/vweek · ctrl+l", main.action_log),
            ("Pick a week…", "/weeks · open any past week", lambda: app.push_screen(WeekPicker())),
            ("Undo", "ctrl+z · or type undo", app.action_undo),
            ("Redo", "ctrl+y · or type redo", app.action_redo),
            ("To-do list", "/todo · notes to self for the week", main.action_todo),
            ("Classes", "/classes · add or remove your classes", main.action_classes),
            ("Config: color theme", "/config", main.action_config),
            ("Add a past session", "forgot to start studying? log it here", lambda: app.push_screen(
                BlockEditor(None, "add"), lambda ok: main.reload())),
            ("Edit last session times", "ctrl+t", main.action_edit_block),
            ("Weeks history", "hours studied per week", lambda: app.run_cli("weeks")),
            ("Edit full config file", "theme, banner, mascot", lambda: app.run_cli("config", "--edit", wait=False)),
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
    #left { width: 3fr; }
    #now {
        height: 1fr; border: round $foreground 25%; border-title-color: $accent;
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
    #week { height: auto; }
    #notes { height: auto; max-height: 12; margin-top: 0; }
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

    BlockEditor, Confirm, KeysSheet, ConfigScreen, ClassesScreen, TodoScreen, ClassLogScreen, ClassPicker, RecoverScreen, QuitScreen, ConflictScreen, OnboardingScreen {
        align: center middle; background: $background 60%;
    }
    #dialog {
        width: 76; height: auto; max-height: 90%; border: round $accent; border-title-color: $accent;
        border-title-style: bold; background: $surface; padding: 1 2;
    }
    #times { height: auto; }
    #times Label { padding: 1 1 0 0; color: $text-muted; }
    #times Input { width: 1fr; border: tall $foreground 20%; background: transparent; }
    #classrow { height: auto; }
    #classrow Label { padding: 1 1 0 0; color: $text-muted; }
    #course { width: 1fr; border: tall $foreground 20%; background: transparent; }
    #times Input:focus, #extra:focus, #course:focus { border: tall $accent; }
    #span { margin: 1 0 0 0; }
    #dialog #items { max-height: 12; }
    #question { margin-top: 1; color: $accent; text-style: bold; }
    #extra { border: tall $foreground 20%; background: transparent; }
    .field { color: $text-muted; margin-top: 1; }
    #quitopts { height: auto; margin-top: 1; background: transparent; border: none; }
    #quitopts > .option-list--option-highlighted { background: $primary 35%; text-style: none; }
    #notelist { height: auto; max-height: 18; background: transparent; border: round $foreground 20%; }
    #notelist:focus { border: round $accent; }
    #newnote { border: tall $foreground 20%; background: transparent; }
    #newnote:focus { border: tall $accent; }
    #notelist > .option-list--option-highlighted { background: $primary 35%; text-style: none; }
    #picklist { height: auto; max-height: 14; background: transparent; border: round $foreground 20%; }
    #picklist:focus { border: round $accent; }
    #pickname { border: tall $foreground 20%; background: transparent; }
    #pickname:focus { border: tall $accent; }
    #todolist { height: auto; max-height: 18; background: transparent; border: round $foreground 20%; }
    #todolist:focus { border: round $accent; }
    #newtodo { border: tall $foreground 20%; background: transparent; }
    #newtodo:focus { border: tall $accent; }
    #classlist { height: auto; max-height: 14; background: transparent; border: round $foreground 20%; }
    #classlist:focus { border: round $accent; }
    #newclass, #ob-class { border: tall $foreground 20%; background: transparent; }
    #newclass:focus, #ob-class:focus { border: tall $accent; }
    #themes { height: auto; background: transparent; border: round $foreground 20%; }
    #themes:focus { border: round $accent; }
    #themes > .option-list--option-highlighted, #picklist > .option-list--option-highlighted,
    #classlist > .option-list--option-highlighted, #todolist > .option-list--option-highlighted {
        background: $primary 35%; text-style: none;
    }
    #error { height: auto; margin-top: 1; }
    #dialog.onboard { width: 80; overflow-y: auto; }
    .ob-step { height: auto; }
    ObPage { height: auto; }
    #ob-banner { margin-bottom: 1; }
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
                        f"Throw away the session from {fmt_time(run.start)} ({fmt_hm(run.minutes())})?\n"
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
