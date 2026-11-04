"""Dewey, the cram mascot: a dolphin who swims along and leaps over floating books while
you study, and naps at the surface when you're not. Drawn in shading characters."""
from __future__ import annotations

import math

from rich.text import Text

from . import ui

W, H = 46, 10          # canvas size
WATER = 6              # the surface row; everything below is water
DX = 2                 # Dewey's x position (he swims in place; the sea scrolls)

# ── Dewey (facing right) ─────────────────────────────────────────────────────
# b body  l belly  e eye (c = closed)  n snout  f dorsal fin  p flipper  k tail
SWIM = [
    "        f          ",
    "       ffb         ",
    "k   bbbbbbbbbbb    ",
    "kkbbbbbbbbbbbbbebnn",
    "k   bllllllllbbb   ",
    "      pp           ",
]
UP = [  # leaping, nose up
    "               nn",
    "            febbn",
    "        ffbbbbbb ",
    "     bbbbbbbblll ",
    "  bbbbbbbblllpp  ",
    "kbbbbbllll       ",
    "kk               ",
]
DOWN = [  # diving, nose down
    "kk               ",
    "kbbbbbf          ",
    "  bbbbbbbbbbb    ",
    "     bbbbbbbbbe  ",
    "       pplllbbbn ",
    "           lllnn ",
    "               nn",
]
SLEEP = [row.replace("e", "c") for row in SWIM]
SLEEP_Y = WATER - 3    # floats with his back just above the surface

# a little stack of books bobbing on the water: r and y covers, = page edges
BOOKS = [
    "rrrrr",
    "=====",
    " yyyy",
]
BOOK_START = W + 2

# ── timeline (one tick ≈ 0.12s) ──────────────────────────────────────────────
LEAP_AT = BOOK_START - (DX + 16)   # start the jump when the books are this close
LEAP_LEN = 16
SPLASH_LEN = 8
CYCLE = LEAP_AT + LEAP_LEN + SPLASH_LEN + 6

C = {
    "body_hi": "#9ab8d0", "body_lo": "#5b7a94", "belly": "#e4edf3", "eye": "#fafafa", "snout": "#7f9fb8",
    "fin": "#4f6c85", "tail": "#4f6c85", "water": ["#89b4fa", "#74c7ec", "#5e8fd8", "#3f6fb5"],
    "deep": "#2b4f86", "foam": "#e4edf3", "cover_r": "#f38ba8", "cover_y": "#f9e2af", "pages": "#fafafa",
    "spark": "#f9e2af", "bubble": "#a6d8f0",
}
_BODY = "@@%@&@@%@@&"
_WAVES = "~~-~~~.~~-~~~~.~"


def _canvas(scroll: int):
    canvas = [[(" ", "") for _ in range(W)] for _ in range(H)]
    for y in range(WATER, H):
        for x in range(W):
            ch = _WAVES[(x + scroll + y * 5) % len(_WAVES)] if y == WATER else " ~"[(x * 3 + y + scroll) % 7 == 0]
            canvas[y][x] = (ch, C["water"][min(3, y - WATER)])
    return canvas


def _blit(canvas, art, x0, y0, painter, t):
    for dy, row in enumerate(art):
        y = y0 + dy
        if not 0 <= y < H:
            continue
        for dx, m in enumerate(row):
            x = x0 + dx
            if m != " " and 0 <= x < W:
                canvas[y][x] = painter(m, x, y, t)


def _paint_dewey(m, x, y, t):
    under = y > WATER               # below the surface he looks deeper blue
    body = ui._lerp(C["body_hi"], C["body_lo"], max(0, min(1, y / (H - 1))))
    ch, color = {
        "b": (_BODY[(x * 3 + y * 2 + t // 4) % len(_BODY)], body),
        "l": ("%", C["belly"]),
        "e": ("●", C["eye"]),
        "c": ("-", C["eye"]),
        "n": ("@", C["snout"]),
        "f": ("#", C["fin"]),
        "p": ("=", C["fin"]),
        "k": ("#" if (x + y) % 2 else "x", C["tail"]),
    }.get(m, ("?", body))
    if under:
        color = ui._lerp(color, C["deep"], 0.55)
    return ch, color


def _paint_books(m, x, y, t):
    if m == "r":
        return "▄", C["cover_r"]
    if m == "y":
        return "▀", C["cover_y"]
    return "=", C["pages"]


def _render(canvas) -> Text:
    out = Text(no_wrap=True, overflow="crop")
    for y, row in enumerate(canvas):
        for ch, style in row:
            out.append(ch, style=style or None)
        if y < H - 1:
            out.append("\n")
    return out


def _swim(i: int) -> Text:
    t = i % CYCLE
    canvas = _canvas(i)
    bob = (i // 6) % 2
    _blit(canvas, BOOKS, BOOK_START - t, WATER - 2 + (i // 5) % 2, _paint_books, i)

    if LEAP_AT <= t < LEAP_AT + LEAP_LEN:              # the jump
        k = t - LEAP_AT
        lift = round(5 * math.sin(math.pi * (k + 0.5) / LEAP_LEN))
        pose = UP if k < LEAP_LEN / 3 else DOWN if k >= 2 * LEAP_LEN / 3 else SWIM
        top = WATER - 3 - lift - (len(pose) - len(SWIM)) // 2
        _blit(canvas, pose, DX, top, _paint_dewey, i)
        if k < 3:                                      # spray as he leaves the water
            for dx, dy in [(2, -1), (4, -2), (6, -1)][: k + 1]:
                canvas[WATER + dy][DX + dx] = ("'", C["foam"])
    else:
        _blit(canvas, SWIM, DX, WATER - 3 + bob, _paint_dewey, i)
        s = t - (LEAP_AT + LEAP_LEN)
        if 0 <= s < SPLASH_LEN:                        # splash back down
            for n, dx in enumerate(range(10, 22, 2)):
                h = max(0, 3 - abs(n - 3) - s // 3)
                for dy in range(1, h + 1):
                    canvas[WATER - dy][DX + dx] = ("'.:"[(n + dy + s) % 3], C["foam"])
    return _render(canvas)


# ── asleep / the bell buoy ───────────────────────────────────────────────────
BUOY = [
    "  qqqqq ",
    "    p   ",
    "  aaaaa ",
    " aaaaaaa",
    " aaaaaaa",
    "aaaaaaaaa",
    "  rrrrr ",
]
BUOY_X = 34
BELL_LEN = 22          # ticks: ding ding (buoy rocking), Dewey wakes with a start, then hops


def _paint_buoy(m, x, y, t):
    if m in "qp":
        return ("=" if m == "q" else "|"), C["fin"]
    if m == "r":
        return "#", C["cover_r"]
    return "@%#@%"[(x + y + t) % 5], ("#f9e2af" if (x + y + t) % 3 else "#e5c07b")


def sleep_frame(i: int) -> Text:
    canvas = _canvas(i // 3)                            # the sea barely moves
    _blit(canvas, SLEEP, DX, SLEEP_Y + (i // 10) % 2, _paint_dewey, i)
    for n in range(3):                                  # z's drifting up from his blowhole
        k = (i + n * 9) % 27
        x, y = DX + 9 + k // 3, 3 - k // 6
        if y >= 0:
            canvas[y][x] = ("z" if k < 18 else "Z", ui.ramp(3, [ui.T.muted, ui.T.accent2])[min(2, k // 9)])
    return _render(canvas)


def bell_frame(i: int) -> Text:
    """i runs 0..BELL_LEN-1: the buoy rings, Dewey wakes with a start, then hops up."""
    rock = (-1, 1)[i % 2] if i < 12 else 0
    canvas = _canvas(i)
    pose = SLEEP if i < 6 else SWIM
    y0 = SLEEP_Y - (1 if 14 <= i < 18 else 0)
    _blit(canvas, pose, DX, y0, _paint_dewey, i)
    _blit(canvas, BUOY, BUOY_X + rock, WATER - 6, _paint_buoy, i)
    if i < 12:                                          # DING! and sound waves
        word = "DING!" if (i // 3) % 2 == 0 else "DING!!"
        for n, ch in enumerate(word):
            canvas[1 + (i % 2)][BUOY_X - 7 + n] = (ch, f"bold {C['spark']}")
        for n, (dx, dy) in enumerate([(-2, 2), (-3, 3), (10, 2), (11, 3)]):
            x = BUOY_X + dx
            if 0 <= x < W and (i + n) % 2 == 0:
                canvas[dy][x] = (")" if dx > 0 else "(", C["spark"])
    if 6 <= i < 14:                                     # "!" over his head
        canvas[1][DX + 9] = ("!", f"bold {C['spark']}")
    return _render(canvas)


def frame(i: int, mode: str = "fight") -> Text:
    """mode: sleep (not studying) · bell (just started) · fight (studying: swim and leap)"""
    if mode == "sleep":
        return sleep_frame(i)
    if mode == "bell":
        return bell_frame(min(i, BELL_LEN - 1))
    return _swim(i)


def frame_count() -> int:
    return CYCLE
