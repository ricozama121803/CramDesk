"""Parsing and formatting of dates, times, durations and weeks."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta

DAY_ABBR = ["Mon", "Tues", "Wed", "Thurs", "Fri", "Sat", "Sun"]
_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


class ParseError(ValueError):
    pass


# ── clock ────────────────────────────────────────────────────────────────────

def now() -> datetime:
    """Current time, truncated to the minute (what we store)."""
    return datetime.now().replace(second=0, microsecond=0)


def today() -> date:
    return date.today()


def week_start(d: date) -> date:
    """Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def week_days(monday: date) -> list[date]:
    return [monday + timedelta(days=i) for i in range(7)]


# ── formatting ───────────────────────────────────────────────────────────────

def fmt_time(t: datetime | time) -> str:
    """4:00pm"""
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def fmt_range(start: datetime, end: datetime | None) -> str:
    """4:00pm-5:03pm"""
    return f"{fmt_time(start)}-{fmt_time(end) if end else 'now'}"


def fmt_hrmin(minutes: int) -> str:
    """9hr 20min (the PDF style)"""
    h, m = divmod(int(minutes), 60)
    if m == 0:
        return f"{h}hr"
    if h == 0:
        return f"{m}min"
    return f"{h}hr {m}min"


def fmt_hm(minutes: int) -> str:
    """9h 20m (the terminal style)"""
    h, m = divmod(int(minutes), 60)
    if h and not m:
        return f"{h}h"
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_hours_num(minutes: int) -> str:
    """540 -> '9', 570 -> '9.5', 560 -> '9.33'"""
    h = round(minutes / 60, 2)
    return str(int(h)) if h == int(h) else f"{h:.2f}".rstrip("0").rstrip(".")


def fmt_md(d: date) -> str:
    return f"{d.month}/{d.day}"


def fmt_mdy(d: date) -> str:
    return f"{d.month}/{d.day}/{d:%y}"


def fmt_long(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def fmt_money(amount: float, cents: bool = True) -> str:
    if not cents and amount == int(amount):
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


# ── parsing ──────────────────────────────────────────────────────────────────

_TIME_RE = re.compile(r"^(\d{1,2})(?::?(\d{2}))?\s*(?:([ap])\.?(?:m\.?)?)?$")


def parse_time(s: str) -> time:
    """9, 9am, 9:30, 9:30pm, 930pm, 21:30, noon, midnight, now"""
    s = s.strip().lower()
    if s == "now":
        return now().time()
    if s == "noon":
        return time(12)
    if s == "midnight":
        return time(0)
    m = _TIME_RE.match(s)
    if not m:
        raise ParseError(f"can't read the time '{s}' (try 9:30am, 21:30 or 930pm)")
    h, mi, ap = int(m[1]), int(m[2] or 0), m[3]
    if mi > 59:
        raise ParseError(f"bad minutes in '{s}'")
    if ap:
        if not 1 <= h <= 12:
            raise ParseError(f"bad hour in '{s}'")
        h = h % 12 + (12 if ap == "p" else 0)
    elif h > 23:
        raise ParseError(f"bad hour in '{s}'")
    return time(h, mi)


def parse_date(s: str, ref: date | None = None) -> date:
    """today, yesterday, mon..sun (most recent), 10/14, 10/14/26, 2026-10-14"""
    ref = ref or today()
    s = s.strip().lower()
    if s in ("today", "t", "now"):
        return ref
    if s in ("yesterday", "y", "yday"):
        return ref - timedelta(days=1)
    if s in _WEEKDAYS:
        return ref - timedelta(days=(ref.weekday() - _WEEKDAYS[s]) % 7)
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?", s)
    if m:
        mo, d = int(m[1]), int(m[2])
        y = int(m[3]) if m[3] else ref.year
        if y < 100:
            y += 2000
        try:
            out = date(y, mo, d)
        except ValueError:
            raise ParseError(f"'{s}' isn't a real date") from None
        # "12/30" typed in early January means last year
        if not m[3] and out > ref + timedelta(days=31):
            try:
                out = out.replace(year=y - 1)
            except ValueError:
                pass
        return out
    raise ParseError(f"can't read the date '{s}' (try 10/14, 2026-10-14, yesterday or tue)")


def parse_when(s: str, default_date: date | None = None) -> datetime:
    """'9:30am' (on default_date/today) or 'DATE TIME' like 'yesterday 9pm', '10/14 11pm'."""
    parts = s.strip().replace("T", " ").split()
    if len(parts) == 1:
        return datetime.combine(default_date or today(), parse_time(parts[0]))
    if len(parts) == 2:
        return datetime.combine(parse_date(parts[0]), parse_time(parts[1]))
    raise ParseError(f"can't read '{s}' (try '9:30am' or 'yesterday 9:30am')")


def parse_week(s: str | None, ref: date | None = None) -> date:
    """this | last | -2 | any date inside the week  ->  that week's Monday"""
    ref = ref or today()
    cur = week_start(ref)
    if s is None:
        return cur
    s = s.strip().lower()
    if s in ("this", "current", "now", "0"):
        return cur
    if s in ("last", "prev", "previous"):
        return cur - timedelta(weeks=1)
    if re.fullmatch(r"[-+]\d{1,3}", s):
        return cur + timedelta(weeks=int(s))
    return week_start(parse_date(s, ref))


_HOURS_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*h(?:rs?|ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?$")


def parse_hours(s: str) -> int:
    """'9' / '9.5' / '9:30' / '9h30m' / '9hr 30min' -> minutes"""
    s = s.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return round(float(s) * 60)
    m = re.fullmatch(r"(\d+):(\d{2})", s)
    if m:
        return int(m[1]) * 60 + int(m[2])
    m = _HOURS_RE.match(s)
    if m and (m[1] or m[2]):
        return round(float(m[1] or 0) * 60) + int(m[2] or 0)
    raise ParseError(f"can't read hours '{s}' (try 9, 9.5, 9:30 or 9h30m)")


# ── billing ──────────────────────────────────────────────────────────────────

def billed_minutes(total: int, mode: str) -> int:
    mode = (mode or "down").lower()
    if mode == "exact":
        return total
    if mode == "nearest":
        return int(math.floor(total / 60 + 0.5)) * 60
    if mode == "quarter":
        return int(math.floor(total / 15 + 0.5)) * 15
    if mode == "up":
        return int(math.ceil(total / 60)) * 60
    return (total // 60) * 60  # "down"


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d
