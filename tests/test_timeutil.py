"""Typing times and dates the way people actually type them (and mistype them)."""
from datetime import date, time

import pytest

from cram.timeutil import (
    ParseError, fmt_hm, fmt_range, parse_date, parse_time, parse_week, week_start,
)
from tests.conftest import MON, dt


@pytest.mark.parametrize("text, want", [
    ("9", time(9)), ("9am", time(9)), ("9 am", time(9)), ("9AM", time(9)), ("9a", time(9)),
    ("9:30", time(9, 30)), ("9:30pm", time(21, 30)), ("930pm", time(21, 30)), ("0930", time(9, 30)),
    ("21:30", time(21, 30)), ("12am", time(0)), ("12pm", time(12)), ("12:15am", time(0, 15)),
    ("noon", time(12)), ("midnight", time(0)), ("  7:05 p.m. ", time(19, 5)), ("0:00", time(0)),
])
def test_parse_time_accepts_common_forms(text, want):
    assert parse_time(text) == want


@pytest.mark.parametrize("text", ["25:00", "13pm", "0am", "9:60", "", "nine", "9.30", "9:3", "-1", "9:30xm"])
def test_parse_time_rejects_nonsense(text):
    with pytest.raises(ParseError):
        parse_time(text)


def test_parse_date_forms():
    ref = date(2026, 11, 2)  # a Monday
    assert parse_date("today", ref) == ref
    assert parse_date("yesterday", ref) == date(2026, 11, 1)
    assert parse_date("mon", ref) == ref                       # today counts as the most recent Monday
    assert parse_date("tues", ref) == date(2026, 10, 27)       # never jumps into the future
    assert parse_date("10/14", ref) == date(2026, 10, 14)
    assert parse_date("10/14/26", ref) == date(2026, 10, 14)
    assert parse_date("2026-10-14", ref) == date(2026, 10, 14)


def test_parse_date_year_wrap_in_january():
    # typing "12/30" on Jan 2 means last December, not next one
    assert parse_date("12/30", date(2027, 1, 2)) == date(2026, 12, 30)


@pytest.mark.parametrize("text", ["2/30", "13/1", "0/5", "someday", "10/14/2026/1"])
def test_parse_date_rejects_impossible_dates(text):
    with pytest.raises(ParseError):
        parse_date(text, date(2026, 11, 2))


def test_parse_week_shortcuts():
    ref = date(2026, 10, 15)  # Thursday
    assert parse_week(None, ref) == MON
    assert parse_week("this", ref) == MON
    assert parse_week("last", ref) == date(2026, 10, 5)
    assert parse_week("-2", ref) == date(2026, 9, 28)
    assert parse_week("+1", ref) == date(2026, 10, 19)
    assert parse_week("10/18", ref) == MON                     # a Sunday belongs to the week before it


def test_formatting_edges():
    assert fmt_hm(0) == "0m" and fmt_hm(540) == "9h" and fmt_hm(61) == "1h 01m"
    assert fmt_range(dt("2026-10-14 23:00"), dt("2026-10-15 00:12")) == "11:00pm-12:12am"
    assert fmt_range(dt("2026-10-14 23:00"), None) == "11:00pm-now"
    assert week_start(date(2026, 10, 18)) == MON
