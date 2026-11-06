"""Find out the terminal's background color, so text can be colored to read on it."""
from __future__ import annotations

import os
import re
import sys

# Stand-ins when we only know "light" or "dark", not the exact color.
LIGHT, DARK = "#ffffff", "#000000"

_OSC11 = re.compile(rb"\x1b\]11;rgba?:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)")


def background(setting: str = "auto") -> str | None:
    """The background as "#rrggbb", or None if there's no way to tell.

    `setting` is the config value: "light", "dark", a "#rrggbb" color, or "auto" to ask the terminal."""
    s = (setting or "auto").strip().lower()
    if s == "light":
        return LIGHT
    if s == "dark":
        return DARK
    if re.fullmatch(r"#[0-9a-f]{6}", s):
        return s
    return _query_terminal() or _from_colorfgbg()


def _from_colorfgbg() -> str | None:
    """COLORFGBG ("15;0") is set by some terminals (rxvt, Konsole, ...): its last number is the background."""
    try:
        bg = int(os.environ.get("COLORFGBG", "").split(";")[-1])
    except ValueError:
        return None
    return LIGHT if bg == 7 or 9 <= bg <= 15 else DARK


def _query_terminal(timeout: float = 0.25) -> str | None:
    """Ask the terminal with OSC 11. A "device attributes" query goes right after it: every terminal
    answers that one, so we always know when to stop reading, and no reply is left over to show
    up at the shell prompt."""
    if os.name != "posix" or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    if os.environ.get("TERM", "") in ("", "dumb", "linux"):
        return None
    try:
        import select
        import termios
        import tty
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except (ImportError, OSError):
        return None
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        os.close(fd)
        return None
    buf = b""
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSANOW, new)
        os.write(fd, b"\x1b]11;?\x07\x1b[c")
        # Read until the device-attributes reply (ESC [ ? ... c) arrives.
        while not re.search(rb"\x1b\[\?[0-9;]*c", buf):
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                break
            chunk = os.read(fd, 256)
            if not chunk:
                break
            buf += chunk
    except OSError:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        os.close(fd)
    return _parse_osc11(buf)


def _parse_osc11(reply: bytes) -> str | None:
    """b"\\x1b]11;rgb:ffff/f6f6/e3e3\\x07" -> "#fff6e3" (each channel is 1-4 hex digits)."""
    m = _OSC11.search(reply)
    if not m:
        return None
    return "#" + "".join(f"{round(int(c, 16) / (16 ** len(c) - 1) * 255):02x}" for c in m.groups())
