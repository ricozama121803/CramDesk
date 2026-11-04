"""Typing times, dates and hours the way people actually type them (and mistype them)."""
from datetime import date, time

import pytest

from cram.timeutil import (
    ParseError, add_business_days, billed_minutes, fmt_hm, fmt_hours_num, fmt_hrmin, fmt_range,
    parse_date, parse_hours, parse_time, parse_week, week_start,
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


@pytest.mark.parametrize("text, minutes", [
    ("9", 540), ("9.5", 570), ("9:30", 570), ("9h30m", 570), ("9hr 30min", 570), ("30m", 30), ("0", 0),
])
def test_parse_hours(text, minutes):
    assert parse_hours(text) == minutes


@pytest.mark.parametrize("text", ["abc", "9:5", "", "h"])
def test_parse_hours_rejects(text):
    with pytest.raises(ParseError):
        parse_hours(text)


@pytest.mark.parametrize("mode, total, want", [
    ("down", 560, 540), ("down", 59, 0), ("nearest", 570, 600), ("nearest", 569, 540),
    ("up", 541, 600), ("quarter", 562, 555), ("quarter", 563, 570), ("exact", 563, 563), ("bogus", 560, 540),
])
def test_billing_rules(mode, total, want):
    assert billed_minutes(total, mode) == want


def test_business_days_skip_weekends():
    assert add_business_days(date(2026, 10, 23), 1) == date(2026, 10, 26)   # Fri -> Mon
    assert add_business_days(date(2026, 10, 21), 14) == date(2026, 11, 10)  # the example invoice's due date


def test_formatting_edges():
    assert fmt_hrmin(0) == "0hr" and fmt_hrmin(560) == "9hr 20min" and fmt_hrmin(45) == "45min"
    assert fmt_hm(0) == "0m" and fmt_hm(540) == "9h" and fmt_hm(61) == "1h 01m"
    assert fmt_hours_num(570) == "9.5" and fmt_hours_num(540) == "9" and fmt_hours_num(560) == "9.33"
    assert fmt_range(dt("2026-10-14 23:00"), dt("2026-10-15 00:12")) == "11:00pm-12:12am"
    assert fmt_range(dt("2026-10-14 23:00"), None) == "11:00pm-now"
    assert week_start(date(2026, 10, 18)) == MON
