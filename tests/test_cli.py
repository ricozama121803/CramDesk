"""The `crm <command>` CLI when people use it in the wrong order or with bad input."""
import os
import subprocess
import sys

from typer.testing import CliRunner

from datetime import date, datetime, time, timedelta

from crm import cli
from crm.cli import app
from crm.store import Store
from tests.conftest import MON

runner = CliRunner()


def block(day, start_h, end_h, note):
    """Put a finished block straight into the store."""
    d = datetime.strptime(day, "%m/%d/%y").date()
    st = Store.load()
    st.add(datetime.combine(d, time(start_h)), datetime.combine(d, time(end_h)), [note])
    st.save()


def crm(*args, input=None):
    return runner.invoke(app, list(args), input=input)


def test_clock_in_twice_is_refused():
    assert crm("in", "--at", "10/14/26 9am", "first").exit_code == 0
    r = crm("in", "--at", "10/14/26 10am")
    assert r.exit_code == 1 and "Already on the clock" in r.output
    assert len(Store.load().sessions) == 1


def test_clock_out_without_clocking_in():
    r = crm("out")
    assert r.exit_code == 1 and "not clocked in" in r.output


def test_clock_out_before_the_start_is_refused():
    crm("in", "--at", "10/14/26 9am")
    r = crm("out", "--at", "10/14/26 8am")
    assert r.exit_code == 1 and Store.load().running() is not None


def test_clock_out_past_midnight_rolls_to_the_next_day():
    crm("in", "--at", "10/14/26 11pm")
    assert crm("out", "--at", "12:30am", "late fix").exit_code == 0
    s = Store.load().sessions[0]
    assert s.end.day == 15 and s.minutes() == 90 and s.notes == ["late fix"]


def test_edit_into_another_block_is_refused():
    block("10/14/26", 9, 10, "a")
    block("10/14/26", 11, 12, "b")
    r = crm("edit", "2", "--start", "9:30am")
    assert r.exit_code == 1 and "overlaps" in r.output


def test_add_with_nothing_logged():
    r = crm("add", "hello")
    assert r.exit_code == 1 and "off the clock" in r.output and "Nothing logged" in r.output


def test_add_describes_the_running_block():
    assert crm("in", "working").exit_code == 0
    assert crm("add", "checked the VPN prefix list").exit_code == 0
    assert Store.load().running().notes == ["working", "checked the VPN prefix list"]


def _this_week_blocks():
    """One block today and one on another day of this week."""
    today = date.today()
    other = today + timedelta(days=1 if today.weekday() == 0 else -1)
    st = Store.load()
    st.add(datetime.combine(today, time(1)), datetime.combine(today, time(2)), ["today thing"])
    st.add(datetime.combine(other, time(1)), datetime.combine(other, time(2)), ["other thing"])
    st.save()


def test_add_off_the_clock_lets_you_pick_a_block_from_today(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    r = crm("add", "late note", input="y\n1\n1\n")
    assert r.exit_code == 0, r.output
    assert "off the clock" in r.output and "From today" in r.output
    notes = {s.notes[0]: s.notes for s in Store.load().sessions}
    assert notes["today thing"] == ["today thing", "late note"]
    assert notes["other thing"] == ["other thing"]


def test_add_off_the_clock_from_another_day_and_typed_lines(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    r = crm("add", input="y\n2\n1\nfirst\nsecond\n\n")
    assert r.exit_code == 0, r.output
    notes = {s.notes[0]: s.notes for s in Store.load().sessions}
    assert notes["other thing"] == ["other thing", "first", "second"]


def test_add_off_the_clock_saying_no_adds_nothing(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    assert crm("add", "nope", input="n\n").exit_code == 0
    assert all(len(s.notes) == 1 for s in Store.load().sessions)


def test_add_off_the_clock_without_a_terminal_is_refused():
    _this_week_blocks()
    r = crm("add", "x")
    assert r.exit_code == 1 and "terminal" in r.output


def test_edit_picks_a_block(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    r = crm("edit", "--note", "renamed", input="1\n1\n")
    assert r.exit_code == 0, r.output
    assert sorted(s.notes[0] for s in Store.load().sessions) == ["other thing", "renamed"]


def test_locked_week_cannot_be_edited_or_deleted():
    block("10/14/26", 9, 10, "a")
    st = Store.load()
    st.weeks[MON.isoformat()] = {"billed_minutes": 60, "rate": 25.0, "invoice_number": 1, "summary": "",
                                 "issued": "2026-10-20", "due": "2026-11-07", "exports": []}
    st.save()
    for args in (["edit", "1", "--end", "11am"], ["add", "--id", "1", "more"]):
        r = crm(*args)
        assert r.exit_code == 1 and "locked" in r.output, args
    assert Store.load().sessions[0].notes == ["a"]


def test_export_needs_approval_first():
    block("10/14/26", 9, 10, "a")
    r = crm("export", "-w", "10/14/26")
    assert r.exit_code == 1 and "isn't approved" in r.output


def test_cannot_approve_while_clocked_in_that_week():
    crm("in", "--at", "10/14/26 9am")
    r = crm("approve", "-w", "10/14/26")
    assert r.exit_code == 1 and "still clocked in" in r.output


def test_undo_redo_from_the_cli():
    assert "Nothing left to undo" in crm("undo").output
    block("10/14/26", 9, 10, "a")
    assert "Undid: add block" in crm("undo").output
    assert Store.load().sessions == []
    assert "Redid" in crm("redo").output
    assert len(Store.load().sessions) == 1


def test_bad_time_gives_a_friendly_message_not_a_traceback(home):
    env = {**os.environ, "CRM_HOME": str(home)}
    r = subprocess.run([sys.executable, "-m", "crm", "in", "--at", "25pm"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "bad hour" in r.stdout + r.stderr and "Traceback" not in r.stderr


def test_plain_crm_without_a_terminal_prints_the_dashboard():
    r = crm()
    assert r.exit_code == 0 and "OFF THE CLOCK" in r.output


def test_view_block_shows_the_running_block_then_the_last_one():
    assert crm("v-block").exit_code == 1
    block("10/14/26", 9, 10, "old work")
    r = crm("v-block")
    assert r.exit_code == 0 and "off the clock currently" in r.output and "what I did" in r.output and "old work" in r.output
    crm("in", "new work")
    r = crm("v-block")
    assert "on the clock" in r.output and "new work" in r.output and "old work" not in r.output


def test_view_today_and_week():
    block("10/14/26", 9, 10, "leads")
    r = crm("v-today", "10/14/26")
    assert r.exit_code == 0 and "leads" in r.output and "1h" in r.output
    r = crm("v-week", "-w", "10/14/26")
    assert r.exit_code == 0 and "leads" in r.output
    assert "nothing logged" in crm("v-today", "10/15/26").output


def test_edit_what_i_did_then_skip_the_time(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    # pick today's block, choose "what I did", rewrite the line, add one more, no to the time
    r = crm("edit", input="1\n1\n2\ntoday thing, fixed\nextra\n\nn\n")
    assert r.exit_code == 0, r.output
    s = next(s for s in Store.load().sessions if s.day == date.today())
    assert s.notes == ["today thing, fixed", "extra"] and s.start.hour == 1 and s.end.hour == 2


def test_edit_time_then_what_i_did_too(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    # choose "time": (today, so no date) start 12:30am, keep the end; yes to editing what I did
    r = crm("edit", input="1\n1\n1\n12:30am\n\ny\nnew words\n\n")
    assert r.exit_code == 0, r.output
    s = next(s for s in Store.load().sessions if s.day == date.today())
    assert s.start.minute == 30 and s.start.hour == 0 and s.notes == ["new words"]


def test_status_is_simple():
    r = crm("status")
    assert r.exit_code == 0 and "OFF THE CLOCK" in r.output and "today" in r.output and "week" in r.output
    crm("in", "working")
    r = crm("status")
    assert "ON THE CLOCK" in r.output and "working" in r.output and "what I did" in r.output


def test_removed_commands_are_gone():
    for name in ("log", "log-past", "bar", "rm", "note"):
        assert crm(name).exit_code == 2, name


def test_cycle_theme_goes_round():
    from crm import config, ui
    seen = []
    for _ in ui.THEMES:
        assert crm("cycle-theme").exit_code == 0
        seen.append(config.load()["ui"]["theme"])
    assert sorted(seen) == sorted(ui.THEMES) and seen[-1] == "catppuccin"


def test_edit_today_skips_the_date_and_offers_to_fix_an_overlap(monkeypatch):
    _this_week_blocks()                      # today 1-2am "today thing"
    st = Store.load()
    t = date.today()
    st.add(datetime.combine(t, time(3)), datetime.combine(t, time(4)), ["later"])
    st.save()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    # pick today, block 1, edit time: (no date asked) start 1am, end 3:30am, no to notes,
    # then "Merge them" (option 1)
    r = crm("edit", input="1\n1\n1\n1am\n3:30am\nn\n1\n")
    assert r.exit_code == 0, r.output
    assert "date ›" not in r.output
    assert "overlaps" in r.output
    todays = [s for s in Store.load().sessions if s.day == t]
    assert len(todays) == 1
    s = todays[0]
    assert (s.start.hour, s.end.hour) == (1, 4) and s.notes == ["today thing", "later"]


def test_edit_overlap_change_the_times_instead(monkeypatch):
    _this_week_blocks()
    st = Store.load()
    t = date.today()
    st.add(datetime.combine(t, time(3)), datetime.combine(t, time(4)), ["later"])
    st.save()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    # overlap, then pick "Change the times" (after merge/trim/shorten/replace) and give non-overlapping ones
    r = crm("edit", input="1\n1\n1\n1am\n3:30am\nn\n" + "5\n" + "1am\n2:30am\n")
    assert r.exit_code == 0, r.output
    s = next(s for s in Store.load().sessions if s.notes == ["today thing"])
    assert s.end.hour == 2 and s.end.minute == 30
