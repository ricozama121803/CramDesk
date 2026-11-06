"""Terminal look & feel: themes, gradients, big clock digits, bars, tables."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .report import Report
from .store import Note, Session, Store, Todo
from .timeutil import DAY_ABBR, fmt_hm, fmt_md, fmt_range, fmt_time, week_days

console = Console(highlight=False)

THEMES = {
    "catppuccin": dict(
        base="#1e1e2e", text="#cdd6f4", muted="#a6adc8", dim="#585b70", faint="#313244",
        accent="#cba6f7", accent2="#89b4fa", ok="#a6e3a1", warn="#f9e2af", err="#f38ba8",
        grad=["#b4befe", "#89b4fa"],
    ),
    "nord": dict(
        base="#2e3440", text="#eceff4", muted="#d8dee9", dim="#4c566a", faint="#3b4252",
        accent="#88c0d0", accent2="#81a1c1", ok="#a3be8c", warn="#ebcb8b", err="#bf616a",
        grad=["#88c0d0", "#81a1c1"],
    ),
    "gruvbox": dict(
        base="#282828", text="#ebdbb2", muted="#bdae93", dim="#665c54", faint="#3c3836",
        accent="#fe8019", accent2="#fabd2f", ok="#b8bb26", warn="#fabd2f", err="#fb4934",
        grad=["#fabd2f", "#fe8019"],
    ),
    "tokyonight": dict(
        base="#1a1b26", text="#c0caf5", muted="#a9b1d6", dim="#565f89", faint="#292e42",
        accent="#bb9af7", accent2="#7aa2f7", ok="#9ece6a", warn="#e0af68", err="#f7768e",
        grad=["#7aa2f7", "#7dcfff"],
    ),
    "rosepine": dict(
        base="#191724", text="#e0def4", muted="#908caa", dim="#524f67", faint="#26233a",
        accent="#c4a7e7", accent2="#9ccfd8", ok="#9ccfd8", warn="#f6c177", err="#eb6f92",
        grad=["#ebbcba", "#c4a7e7"],
    ),
    # Leonida: a neon sunset over the bay, kept to hot pink into violet.
    "leonida": dict(
        base="#1a0f2e", text="#fdeaff", muted="#cdb4e0", dim="#6c4f8f", faint="#2b1a48",
        accent="#ff4fa3", accent2="#2ee6d6", ok="#4ef0b3", warn="#ffb347", err="#ff5470",
        grad=["#ff5f8f", "#c04dff"],
    ),
}

T = SimpleNamespace(**THEMES["catppuccin"])

# Lowest contrast ratio (WCAG, 1-21) each color may have against the terminal background.
# Text roles need 4.5 to read comfortably; dim labels 3; faint is for rules and "·" placeholders.
MIN_CONTRAST = dict(text=4.5, muted=4.5, accent=4.5, accent2=4.5, ok=4.5, warn=4.5, err=4.5,
                    grad=3.0, dim=3.0, faint=1.6)


def set_theme(name: str, background: str | None = None) -> None:
    """Load a theme. With `background` (the terminal's own background color), every color is
    nudged until it reads clearly on it, so a dark theme still works in a light terminal.
    The full-screen app paints the theme's own background, so it passes none."""
    T.__dict__.update(THEMES.get(name, THEMES["catppuccin"]))
    if background:
        readable_on(background)


def readable_on(bg: str) -> None:
    """Fit the current theme to background `bg`."""
    for role, ratio in MIN_CONTRAST.items():
        cur = getattr(T, role)
        setattr(T, role, [_ensure_contrast(c, bg, ratio) for c in cur] if isinstance(cur, list)
                else _ensure_contrast(cur, bg, ratio))
    T.base = bg


# ── color helpers ────────────────────────────────────────────────────────────

def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ra, rb))


def luminance(h: str) -> float:
    def lin(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def is_light(bg: str) -> bool:
    # Past this point black text out-contrasts white text.
    return luminance(bg) > 0.179


def _ensure_contrast(fg: str, bg: str, ratio: float) -> str:
    """fg, mixed toward black or white (keeping its hue) just enough to reach `ratio` against bg."""
    if contrast(fg, bg) >= ratio:
        return fg
    target = "#000000" if is_light(bg) else "#ffffff"
    for i in range(1, 21):
        c = _lerp(fg, target, i / 20)
        if contrast(c, bg) >= ratio:
            return c
    return target


def ramp(n: int, stops: list[str] | None = None) -> list[str]:
    stops = stops or T.grad
    if n <= 1:
        return [stops[0]]
    out = []
    for i in range(n):
        pos = i / (n - 1) * (len(stops) - 1)
        k = min(int(pos), len(stops) - 2)
        out.append(_lerp(stops[k], stops[k + 1], pos - k))
    return out


def gradient(s: str, bold: bool = True, stops: list[str] | None = None) -> Text:
    t = Text()
    for ch, col in zip(s, ramp(len(s), stops)):
        t.append(ch, style=f"{'bold ' if bold else ''}{col}")
    return t


# ── big digits ───────────────────────────────────────────────────────────────

GLYPHS = {
    "0": ["█▀█", "█ █", "▀▀▀"],
    "1": ["▀█ ", " █ ", "▀▀▀"],
    "2": ["▀▀█", "█▀▀", "▀▀▀"],
    "3": ["▀▀█", " ▀█", "▀▀▀"],
    "4": ["█ █", "▀▀█", "  ▀"],
    "5": ["█▀▀", "▀▀█", "▀▀▀"],
    "6": ["█▀▀", "█▀█", "▀▀▀"],
    "7": ["▀▀█", "  █", "  ▀"],
    "8": ["█▀█", "█▀█", "▀▀▀"],
    "9": ["█▀█", "▀▀█", "▀▀▀"],
    ":": [" ", "▀", "▀"],
    " ": [" ", " ", " "],
}


def big(s: str, stops: list[str] | None = None) -> Text:
    rows = ["", "", ""]
    for ch in s:
        g = GLYPHS.get(ch, GLYPHS[" "])
        for i in range(3):
            rows[i] += g[i] + " "
    width = len(rows[0])
    cols = ramp(width, stops)
    out = Text()
    for i, row in enumerate(rows):
        for ch, col in zip(row, cols):
            out.append(ch, style=f"bold {col}")
        if i < 2:
            out.append("\n")
    return out


def clock_str(seconds: int, with_seconds: bool = True) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if with_seconds else f"{h:02d}:{m:02d}"


# ── small pieces ─────────────────────────────────────────────────────────────

def chip(label: str, color: str) -> Text:
    fg = max((T.base, T.text), key=lambda c: contrast(c, color))
    return Text.assemble((f" {label} ", f"bold {fg} on {color}"))


def bar(minutes: int, scale: int, width: int = 26, color: str | None = None) -> Text:
    parts = " ▏▎▍▌▋▊▉█"
    color = color or T.accent
    if minutes <= 0:
        return Text("·" + " " * (width - 1), style=T.faint)
    cells = min(width, minutes / max(scale, 1) * width)
    full = int(cells)
    frac = parts[int((cells - full) * 8)] if full < width else ""
    t = Text("█" * full + (frac.strip() and frac), style=color)
    t.append("─" * (width - len(t)), style=T.faint)
    return t


def ok(msg: str) -> None:
    console.print(Text("  ✓ ", style=f"bold {T.ok}") + Text.from_markup(msg, style=T.text))


def info(msg: str) -> None:
    console.print(Text("  › ", style=f"bold {T.accent}") + Text.from_markup(msg, style=T.muted))


def warn(msg: str) -> None:
    console.print(Text("  ! ", style=f"bold {T.warn}") + Text.from_markup(msg, style=T.text))


def err(msg: str) -> None:
    console.print(Text("  ✗ ", style=f"bold {T.err}") + Text.from_markup(msg, style=T.text))


def panel(body, title: str, subtitle=None, border: str | None = None, padding=(1, 2)) -> Panel:
    return Panel(
        body,
        box=box.ROUNDED,
        border_style=border or T.dim,
        title=Text.assemble(" ", (title, f"bold {T.accent}"), " "),
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        padding=padding,
    )


def kv(*pairs: tuple[str, str | Text]) -> Table:
    g = Table.grid(padding=(0, 2))
    g.add_column(style=T.dim, justify="right")
    g.add_column(style=T.text)
    for k, v in pairs:
        g.add_row(k, v)
    return g


def notes_text(notes: list[str], indent: str = "") -> Text:
    t = Text()
    for i, n in enumerate(notes):
        if i:
            t.append("\n")
        t.append(f"{indent}› ", style=T.accent)
        t.append(n, style=T.text)
    return t


# ── composite views ──────────────────────────────────────────────────────────

def week_bars(store: Store, monday: date, at: datetime | None = None, width: int = 26) -> Table:
    days = week_days(monday)
    mins = [sum(s.minutes(at) for s in store.on_day(d)) for d in days]
    scale = max(8 * 60, max(mins))
    g = Table.grid(padding=(0, 1))
    g.add_column(width=2)
    g.add_column(width=6)
    g.add_column(width=6)
    g.add_column()
    g.add_column(justify="right", width=8)
    g.add_column()
    today = date.today()
    for d, m in zip(days, mins):
        is_today = d == today
        running = any(s.running for s in store.on_day(d))
        g.add_row(
            Text("›", style=f"bold {T.accent}") if is_today else Text(""),
            Text(DAY_ABBR[d.weekday()], style=f"bold {T.text}" if is_today else T.muted),
            Text(fmt_md(d), style=T.dim),
            bar(m, scale, width=width, color=_lerp(T.accent2, T.base, 0.2)),
            Text(fmt_hm(m) if m else "", style=T.text),
            Text(" ●", style=f"bold {T.ok}") if running else Text(""),
        )
    return g


def totals_line(r: Report) -> Text:
    t = Text()
    t.append("total ", style=T.dim)
    t.append(fmt_hm(r.total_minutes), style=f"bold {T.text}")
    n = len(r.days)
    if n:
        t.append(f"   {n} day{'s' if n != 1 else ''} studied", style=T.dim)
    return t


def dashboard(store: Store, r: Report, cfg: dict, live: bool = False) -> Group:
    """cram status: studying or not, today's hours, the week."""
    now = datetime.now()
    run = store.running()
    head = chip("● STUDYING", T.ok) if run else chip("○ NOT STUDYING", T.dim)

    today_min = sum(s.minutes(now) for s in store.on_day(date.today()))
    rows = kv(("studied today", Text(fmt_hm(today_min), style=f"bold {T.text}")))
    if run:
        el = run.seconds(now)
        shift = clock_str(el, with_seconds=live) if live else fmt_hm(el // 60)
        rows.add_row(Text("this session", style=T.muted),
                     Text.assemble((shift, f"bold {T.ok}"), (f"  since {fmt_time(run.start)}", T.dim)))
    parts = [head, Text(""), rows]
    if run and run.notes:
        parts += [Text(""), Text("what I covered", style=f"bold {T.accent}"), notes_text(run.notes)]
    top = panel(Group(*parts), f"today · {now:%a %b} {now.day}", border=T.ok if run else None, padding=(0, 2))

    week = panel(
        Group(week_bars(store, r.monday, now), Text(""), class_bars(store.in_week(r.monday), now),
              Text(""), totals_line(r), todo_summary(store, r.monday)),
        f"week {fmt_md(r.monday)} – {fmt_md(r.sunday)}",
    )
    return Group(top, week)


def sessions_table(store: Store, monday: date) -> Table:
    return blocks_table(store.in_week(monday))


def blocks_table(blocks: list[Session]) -> Table:
    t = Table(
        box=box.SIMPLE_HEAD, header_style=f"bold {T.accent}", border_style=T.dim,
        show_edge=False, pad_edge=False, expand=False,
    )
    t.add_column("day", style=T.muted, no_wrap=True)
    t.add_column("time", style=T.text, no_wrap=True)
    t.add_column("dur", style=T.accent2, justify="right", no_wrap=True)
    t.add_column("class", style=f"bold {T.accent}", no_wrap=True, max_width=22)
    t.add_column("what", style=T.text, max_width=60)
    last_day = None
    for s in blocks:
        if last_day and s.day != last_day:
            t.add_section()
        day = f"{DAY_ABBR[s.day.weekday()]} {fmt_md(s.day)}" if s.day != last_day else ""
        last_day = s.day
        rng = Text(fmt_range(s.start, s.end))
        if s.running:
            rng.stylize(f"bold {T.ok}")
        t.add_row(day, rng, fmt_hm(s.minutes()), s.course or Text("—", style=T.faint),
                  notes_text(s.notes) if s.notes else Text("—", style=T.faint))
    return t


NO_CLASS = "no class"


def class_minutes(sessions: list[Session], at: datetime | None = None) -> list[tuple[str | None, int]]:
    """[(class, minutes)], most studied first; sessions without a class are grouped under None."""
    tot: dict[str | None, int] = {}
    for s in sessions:
        tot[s.course] = tot.get(s.course, 0) + s.minutes(at)
    return sorted(((c, m) for c, m in tot.items() if m), key=lambda x: (-x[1], x[0] is None, x[0] or ""))


def class_bars(sessions: list[Session], at: datetime | None = None, width: int = 20) -> Table | Text:
    """Hours per class with a bar each, for a day or a week."""
    rows = class_minutes(sessions, at)
    if not rows:
        return Text("nothing studied yet", style=T.dim)
    g = Table.grid(padding=(0, 1))
    g.add_column(no_wrap=True, max_width=22)
    g.add_column(no_wrap=True)
    g.add_column(justify="right", no_wrap=True)
    g.add_column(justify="right", no_wrap=True)
    total = sum(m for _, m in rows) or 1
    top = rows[0][1]
    for c, m in rows:
        g.add_row(Text(c or NO_CLASS, style=f"bold {T.text}" if c else T.dim),
                  bar(m, top, width=width, color=T.accent if c else T.dim),
                  Text(fmt_hm(m), style=T.text), Text(f"{round(100 * m / total)}%", style=T.dim))
    return g


def classes_table(store: Store) -> Table:
    """cram classes: every class with this week's and all-time hours."""
    from .timeutil import week_start
    monday = week_start(date.today())
    t = Table(box=box.SIMPLE_HEAD, header_style=f"bold {T.accent}", show_edge=False, pad_edge=False)
    for col, j in [("class", "left"), ("this week", "right"), ("total", "right"), ("last studied", "left")]:
        t.add_column(col, justify=j, no_wrap=True)
    names: list[str | None] = list(store.classes)
    names += sorted({s.course for s in store.sessions if s.course and s.course not in store.classes})
    if any(s.course is None for s in store.sessions):
        names.append(None)
    for c in names:
        mine = [s for s in store.sessions if s.course == c]
        week = sum(s.minutes() for s in mine if s.week == monday)
        last = max((s.day for s in mine), default=None)
        label = Text(c or NO_CLASS, style=f"bold {T.text}" if c in store.classes else T.dim)
        if c and c not in store.classes:
            label.append("  (removed)", style=T.faint)
        t.add_row(label, Text(fmt_hm(week) if week else "·", style=T.text if week else T.faint),
                  Text(fmt_hm(sum(s.minutes() for s in mine)) if mine else "·", style=T.accent2),
                  Text(f"{DAY_ABBR[last.weekday()]} {fmt_md(last)}" if last else "never", style=T.dim))
    return t


def todo_line(t: Todo, show_week: bool = False) -> Text:
    """☐ #3  Linear Algebra  problem set 4"""
    line = Text()
    line.append("☑ " if t.done else "☐ ", style=T.ok if t.done else T.accent)
    line.append(f"#{t.id:<3} ", style=T.dim)
    if t.course:
        line.append(f"{t.course}  ", style=T.dim if t.done else f"bold {T.accent2}")
    line.append(t.text, style=f"{T.dim} strike" if t.done else T.text)
    if show_week:
        line.append(f"   from week of {fmt_md(t.week)}", style=T.faint)
    return line


def todo_summary(store: Store, monday: date) -> Text:
    mine, older = store.todos_for(monday)
    if not mine and not older:
        return Text("")
    done = sum(t.done for t in mine)
    out = Text.assemble(("memo ", T.dim), (f"{done}/{len(mine)} done", f"bold {T.text}"))
    if older:
        out.append(f"   +{len(older)} open from earlier", style=T.warn)
    return out


def todo_list(store: Store, monday: date) -> Group:
    mine, older = store.todos_for(monday)
    parts: list = []
    if not mine and not older:
        return Group(Text('the memo board is empty. add one with  cram add memo "email the TA"', style=T.dim))
    parts += [todo_line(t) for t in mine] or [Text("nothing for this week yet", style=T.dim)]
    if older:
        parts += [Text(""), Text("still open from earlier weeks", style=f"bold {T.warn}")]
        parts += [todo_line(t, show_week=True) for t in older]
    return Group(*parts)


def note_line(n: Note, show_class: bool = False) -> Text:
    line = Text()
    line.append(f"#{n.id:<3} ", style=T.dim)
    line.append(f"{DAY_ABBR[n.at.weekday()]} {fmt_md(n.at.date())} {fmt_time(n.at)}  ", style=T.muted)
    if show_class:
        line.append(f"{n.course}  ", style=f"bold {T.accent2}")
    line.append(n.text, style=T.text)
    return line


def notes_view(store: Store, course: str | None = None) -> Group:
    """One class's log, or every class's log grouped under its name. Newest first."""
    notes = store.notes_for(course)
    if not notes:
        who = course or "any class"
        return Group(Text(f'nothing logged for {who} yet. add one with  cram log {(course or "statics").lower()} "…"',
                          style=T.dim))
    if course:
        return Group(*[note_line(n) for n in notes])
    parts: list = []
    order = list(store.classes) + sorted({n.course for n in notes} - set(store.classes))
    for c in order:
        mine = [n for n in notes if n.course == c]
        if mine:
            if parts:
                parts.append(Text(""))
            parts.append(Text(c, style=f"bold {T.accent}"))
            parts += [note_line(n) for n in mine]
    return Group(*parts)


def block_label(s: Session) -> str:
    """Mon 11/2 9:10pm-10:38pm"""
    return f"{DAY_ABBR[s.day.weekday()]} {fmt_md(s.day)} {fmt_range(s.start, s.end)}"


def session_card(s: Session, title: str, border: str | None = None, notes_label: str | None = None) -> Panel:
    body = kv(
        ("class", Text(s.course, style=f"bold {T.accent}") if s.course else Text("none", style=T.dim)),
        ("day", f"{DAY_ABBR[s.day.weekday()]} {fmt_md(s.day)}"),
        ("time", fmt_range(s.start, s.end)),
        ("duration", fmt_hm(s.minutes())),
    )
    parts = [body]
    if s.notes:
        parts.append(Text(""))
        if notes_label:
            parts.append(Text(notes_label, style=f"bold {T.accent}"))
        parts.append(notes_text(s.notes))
    return panel(Group(*parts), title, border=border, padding=(0, 2))


# ── banner ───────────────────────────────────────────────────────────────────

# Figlet "ANSI Shadow"-style letters. The first "!" is flipped upside down into a "¡".
BANNER_GLYPHS = {
    "¡": ["██╗", "╚═╝", "██╗", "██║", "██║", "╚═╝"],
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "M": ["███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║", "╚═╝     ╚═╝"],
    "!": ["██╗", "██║", "██║", "╚═╝", "██╗", "╚═╝"],
}


def _fit(a: list[str], b: list[str], gap: int = 1) -> list[str]:
    """Slide glyph b left against a (figlet-style kerning), keeping at least `gap` spaces."""
    w = max(len(x) for x in a)
    a = [x.ljust(w) for x in a]
    free = min(w - len(x.rstrip()) + len(y) - len(y.lstrip()) for x, y in zip(a, b))
    if free < gap:
        b = [" " * (gap - free) + y for y in b]
        free = gap
    k = free - gap
    out = []
    for x, y in zip(a, b):
        tail, overlap = x[: w - k], x[w - k:]
        merged = "".join(c2 if c1 == " " else c1 for c1, c2 in zip(overlap, y[:k].ljust(k)))
        out.append(tail + merged + y[k:])
    return out


def banner(word: str = "¡CRAM!") -> Text:
    rows = BANNER_GLYPHS[word[0]]
    for ch in word[1:]:
        rows = _fit(rows, BANNER_GLYPHS[ch])
    rows = [r.rstrip() for r in rows]
    cols = ramp(max(len(r) for r in rows))
    out = Text(no_wrap=True, overflow="crop")
    for i, row in enumerate(rows):
        out.append("  ")
        for ch, col in zip(row, cols):
            out.append(ch, style=f"bold {col}")
        if i < len(rows) - 1:
            out.append("\n")
    return out


# ── timeline bar ─────────────────────────────────────────────────────────────

def _hour_label(h: int) -> str:
    h %= 24
    return f"{h % 12 or 12}{'a' if h < 12 else 'p'}"


def day_range(blocks: list[Session], at: datetime | None = None) -> tuple[int, int]:
    """Hours to show: at least 8a-6p, stretched to fit every session (sessions past midnight stop at 12a)."""
    import math
    lo, hi = 8, 18
    for s in blocks:
        start = s.start.hour + s.start.minute / 60
        end_dt = s.end or at or datetime.now()
        end = 24 if end_dt.date() > s.day else end_dt.hour + end_dt.minute / 60
        lo, hi = min(lo, math.floor(start)), max(hi, math.ceil(end))
    return lo, min(hi, 24)


def timeline_bar(blocks: list[Session], day: date, width: int, lo: int, hi: int,
                 at: datetime | None = None) -> Text:
    """One row: each session filled in on an hour scale, gaps as a thin line."""
    from datetime import datetime as dt, time as tm, timedelta
    width = max(4, width)
    at = at or dt.now()
    base = dt.combine(day, tm()) + timedelta(hours=lo)
    span = (hi - lo) * 3600
    colors = [_lerp(T.accent2, T.base, 0.2), _lerp(T.accent2, T.base, 0.5)]
    cells = []
    for c in range(width):
        c0 = base + timedelta(seconds=span * c / width)
        c1 = base + timedelta(seconds=span * (c + 1) / width)
        hit = None
        for i, s in enumerate(blocks):
            end = s.end or at
            if s.start < c1 and end > c0:
                hit = (i, s)
        cells.append(hit)
    t = Text(no_wrap=True, overflow="crop")
    for c, hit in enumerate(cells):
        if hit is None:
            t.append("─", style=T.faint)
        else:
            i, s = hit
            t.append("█", style=f"bold {T.ok}" if s.running else colors[i % 2])
    return t


def timeline_axis(width: int, lo: int, hi: int) -> Text:
    """Hour labels under a timeline bar."""
    width = max(4, width)
    hours = hi - lo
    step = next((s for s in (1, 2, 3, 4, 6, 12) if width / max(1, hours / s) >= 5), 12)
    line = [" "] * (width + 4)
    for h in range(lo, hi + 1):
        if (h - lo) % step:
            continue
        pos = min(round((h - lo) / max(1, hours) * width), width)
        label = _hour_label(h)
        pos = max(0, min(pos - (len(label) // 2 if pos else 0), width + 4 - len(label)))
        if all(ch == " " for ch in line[max(0, pos - 1):pos + len(label) + 1]):
            line[pos:pos + len(label)] = label
    return Text("".join(line).rstrip(), style=T.dim, no_wrap=True, overflow="crop")
