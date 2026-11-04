"""The `cram <command>` CLI when people use it in the wrong order or with bad input."""
import os
import subprocess
import sys

from typer.testing import CliRunner

from datetime import date, datetime, time, timedelta

from cram import cli
from cram.cli import app
from cram.store import Store
from tests.conftest import MON

runner = CliRunner()


def block(day, start_h, end_h, note):
    """Put a finished session straight into the store."""
    d = datetime.strptime(day, "%m/%d/%y").date()
    st = Store.load()
    st.add(datetime.combine(d, time(start_h)), datetime.combine(d, time(end_h)), [note])
    st.save()


def cram(*args, input=None):
    return runner.invoke(app, list(args), input=input)


def test_clock_in_twice_is_refused():
    assert cram("in", "--at", "10/14/26 9am", "first").exit_code == 0
    r = cram("in", "--at", "10/14/26 10am")
    assert r.exit_code == 1 and "Already studying" in r.output
    assert len(Store.load().sessions) == 1


def test_clock_out_without_clocking_in():
    r = cram("out")
    assert r.exit_code == 1 and "not studying" in r.output


def test_clock_out_before_the_start_is_refused():
    cram("in", "--at", "10/14/26 9am")
    r = cram("out", "--at", "10/14/26 8am")
    assert r.exit_code == 1 and Store.load().running() is not None


def test_clock_out_past_midnight_rolls_to_the_next_day():
    cram("in", "--at", "10/14/26 11pm")
    assert cram("out", "--at", "12:30am", "late fix").exit_code == 0
    s = Store.load().sessions[0]
    assert s.end.day == 15 and s.minutes() == 90 and s.notes == ["late fix"]


def test_edit_into_another_block_is_refused():
    block("10/14/26", 9, 10, "a")
    block("10/14/26", 11, 12, "b")
    r = cram("edit", "2", "--start", "9:30am")
    assert r.exit_code == 1 and "overlaps" in r.output


def test_add_with_nothing_logged():
    r = cram("add", "hello")
    assert r.exit_code == 1 and "not studying" in r.output and "Nothing logged" in r.output


def test_add_describes_the_running_block():
    assert cram("in", "working").exit_code == 0
    assert cram("add", "checked the VPN prefix list").exit_code == 0
    assert Store.load().running().notes == ["working", "checked the VPN prefix list"]


def _this_week_blocks():
    """One session today and one on another day of this week."""
    today = date.today()
    other = today + timedelta(days=1 if today.weekday() == 0 else -1)
    st = Store.load()
    st.add(datetime.combine(today, time(1)), datetime.combine(today, time(2)), ["today thing"])
    st.add(datetime.combine(other, time(1)), datetime.combine(other, time(2)), ["other thing"])
    st.save()


def test_add_off_the_clock_lets_you_pick_a_block_from_today(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    r = cram("add", "late note", input="y\n1\n1\n")
    assert r.exit_code == 0, r.output
    assert "not studying" in r.output and "From today" in r.output
    notes = {s.notes[0]: s.notes for s in Store.load().sessions}
    assert notes["today thing"] == ["today thing", "late note"]
    assert notes["other thing"] == ["other thing"]


def test_add_off_the_clock_from_another_day_and_typed_lines(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    r = cram("add", input="y\n2\n1\nfirst\nsecond\n\n")
    assert r.exit_code == 0, r.output
    notes = {s.notes[0]: s.notes for s in Store.load().sessions}
    assert notes["other thing"] == ["other thing", "first", "second"]


def test_add_off_the_clock_saying_no_adds_nothing(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    assert cram("add", "nope", input="n\n").exit_code == 0
    assert all(len(s.notes) == 1 for s in Store.load().sessions)


def test_add_off_the_clock_without_a_terminal_is_refused():
    _this_week_blocks()
    r = cram("add", "x")
    assert r.exit_code == 1 and "terminal" in r.output


def test_edit_picks_a_block(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    r = cram("edit", "--note", "renamed", input="1\n1\n")
    assert r.exit_code == 0, r.output
    assert sorted(s.notes[0] for s in Store.load().sessions) == ["other thing", "renamed"]


def test_invoice_commands_are_gone():
    block("10/14/26", 9, 10, "a")
    for name in ("preview", "approve", "unapprove", "export"):
        assert cram(name, "-w", "10/14/26").exit_code == 2, name


def test_weeks_shows_hours_without_billing():
    block("10/14/26", 9, 10, "a")
    r = cram("weeks")
    assert r.exit_code == 0 and "1h" in r.output
    assert "billed" not in r.output and "invoice" not in r.output


def test_undo_redo_from_the_cli():
    assert "Nothing left to undo" in cram("undo").output
    block("10/14/26", 9, 10, "a")
    assert "Undid: add session" in cram("undo").output
    assert Store.load().sessions == []
    assert "Redid" in cram("redo").output
    assert len(Store.load().sessions) == 1


def test_bad_time_gives_a_friendly_message_not_a_traceback(home):
    env = {**os.environ, "CRAM_HOME": str(home)}
    r = subprocess.run([sys.executable, "-m", "cram", "in", "--at", "25pm"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "bad hour" in r.stdout + r.stderr and "Traceback" not in r.stderr


def test_plain_cram_without_a_terminal_prints_the_dashboard():
    r = cram()
    assert r.exit_code == 0 and "NOT STUDYING" in r.output


def test_view_block_shows_the_running_block_then_the_last_one():
    assert cram("v-block").exit_code == 1
    block("10/14/26", 9, 10, "old work")
    r = cram("v-block")
    assert r.exit_code == 0 and "not studying right now" in r.output and "what I covered" in r.output and "old work" in r.output
    cram("in", "new work")
    r = cram("v-block")
    assert "studying" in r.output and "new work" in r.output and "old work" not in r.output


def test_view_today_and_week():
    block("10/14/26", 9, 10, "leads")
    r = cram("v-today", "10/14/26")
    assert r.exit_code == 0 and "leads" in r.output and "1h" in r.output
    r = cram("v-week", "-w", "10/14/26")
    assert r.exit_code == 0 and "leads" in r.output
    assert "nothing logged" in cram("v-today", "10/15/26").output


def test_edit_what_i_did_then_skip_the_time(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    # pick today's block, choose "what I did", rewrite the line, add one more, no to the time
    r = cram("edit", input="1\n1\n2\ntoday thing, fixed\nextra\n\nn\n")
    assert r.exit_code == 0, r.output
    s = next(s for s in Store.load().sessions if s.day == date.today())
    assert s.notes == ["today thing, fixed", "extra"] and s.start.hour == 1 and s.end.hour == 2


def test_edit_time_then_what_i_did_too(monkeypatch):
    _this_week_blocks()
    monkeypatch.setattr(cli, "_tty", lambda: True)
    # choose "time": (today, so no date) start 12:30am, keep the end; yes to editing what I did
    r = cram("edit", input="1\n1\n1\n12:30am\n\ny\nnew words\n\n")
    assert r.exit_code == 0, r.output
    s = next(s for s in Store.load().sessions if s.day == date.today())
    assert s.start.minute == 30 and s.start.hour == 0 and s.notes == ["new words"]


def test_status_is_simple():
    r = cram("status")
    assert r.exit_code == 0 and "NOT STUDYING" in r.output and "today" in r.output and "week" in r.output
    cram("in", "working")
    r = cram("status")
    assert "STUDYING" in r.output and "working" in r.output and "what I covered" in r.output


def test_removed_commands_are_gone():
    for name in ("log", "log-past", "bar", "rm", "note"):
        assert cram(name).exit_code == 2, name


def test_cycle_theme_goes_round():
    from cram import config, ui
    seen = []
    for _ in ui.THEMES:
        assert cram("cycle-theme").exit_code == 0
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
    r = cram("edit", input="1\n1\n1\n1am\n3:30am\nn\n1\n")
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
    r = cram("edit", input="1\n1\n1\n1am\n3:30am\nn\n" + "5\n" + "1am\n2:30am\n")
    assert r.exit_code == 0, r.output
    s = next(s for s in Store.load().sessions if s.notes == ["today thing"])
    assert s.end.hour == 2 and s.end.minute == 30


def test_class_add_list_rename_and_remove():
    r = cram("class", "add", "Linear", "Algebra,", "Chemistry")
    assert r.exit_code == 0 and "Added Linear Algebra" in r.output and "Added Chemistry" in r.output
    assert "already one of your classes" in cram("class", "add", "chemistry").output
    cram("in", "--at", "10/14/26 9am", "linear", "algebra", "eigenvectors")
    cram("out", "--at", "10/14/26 10am", "done")
    s = Store.load().sessions[0]
    assert s.course == "Linear Algebra" and s.notes == ["eigenvectors", "done"]
    r = cram("classes")
    assert "Linear Algebra" in r.output and "Chemistry" in r.output and "never" in r.output
    assert cram("class", "rename", "linear algebra", "Lin Alg").exit_code == 0
    assert Store.load().sessions[0].course == "Lin Alg"
    r = cram("class", "rm", "lin alg")
    assert r.exit_code == 0 and "keep the class" in r.output
    assert Store.load().classes == ["Chemistry"] and Store.load().sessions[0].course == "Lin Alg"


def test_in_matches_short_names_and_refuses_ambiguous_ones():
    cram("class", "add", "Chemistry, Chem Lab, US History")
    r = cram("in", "--at", "10/14/26 9am", "chem")
    assert r.exit_code == 1 and "Chemistry or Chem Lab" in r.output
    cram("in", "--at", "10/14/26 9am", "uh", "reading")         # initials
    assert Store.load().running().course == "US History"
    assert Store.load().running().notes == ["reading"]


def test_in_with_unknown_text_without_a_terminal_is_just_a_note():
    cram("class", "add", "Chemistry")
    cram("in", "--at", "10/14/26 9am", "random", "reading")
    s = Store.load().running()
    assert s.course is None and s.notes == ["random reading"]
    assert Store.load().classes == ["Chemistry"]


def test_class_option_and_edit_class():
    cram("class", "add", "Chemistry, Linear Algebra")
    assert cram("in", "--at", "10/14/26 9am", "-c", "bio").exit_code == 1
    cram("in", "--at", "10/14/26 9am", "-c", "chem", "titration")
    cram("out", "--at", "10/14/26 10am", "x")
    assert Store.load().sessions[0].course == "Chemistry"
    assert cram("edit", "1", "-c", "la").exit_code == 0
    assert Store.load().sessions[0].course == "Linear Algebra"
    assert cram("edit", "1", "-c", "none").exit_code == 0
    assert Store.load().sessions[0].course is None


def test_week_view_shows_hours_per_class():
    cram("class", "add", "Chemistry, Linear Algebra")
    cram("in", "--at", "10/14/26 9am", "chemistry"); cram("out", "--at", "10/14/26 10am", "x")
    cram("in", "--at", "10/14/26 11am", "la"); cram("out", "--at", "10/14/26 11:30am", "x")
    r = cram("v-week", "-w", "10/14/26")
    assert "by class" in r.output and "Chemistry" in r.output and "67%" in r.output and "33%" in r.output


def test_undo_takes_back_a_new_class():
    cram("class", "add", "Chemistry")
    assert "add class Chemistry" in cram("undo").output
    assert Store.load().classes == []


def test_todo_add_list_done_and_carry_over():
    cram("class", "add", "Chemistry")
    assert "No to-do" not in cram("todo").output
    cram("todo", "add", "chem", "read", "ch", "7")
    cram("todo", "add", "-w", "last", "review", "notes")
    cram("todo", "add", "-w", "last", "old", "finished", "thing")
    cram("todo", "done", "3")
    t = Store.load().get_todo(1)
    assert t.course == "Chemistry" and t.text == "read ch 7"
    r = cram("todo")
    assert "read ch 7" in r.output and "still open from earlier weeks" in r.output
    assert "review notes" in r.output and "old finished thing" not in r.output   # done ones stay in their week
    assert cram("todo", "done", "1").exit_code == 0 and Store.load().get_todo(1).done
    assert cram("todo", "uncheck", "1").exit_code == 0 and not Store.load().get_todo(1).done
    cram("todo", "done", "1"); assert cram("todo", "undo", "1").exit_code == 0     # old name still works
    assert cram("todo", "done", "42").exit_code == 1
    assert cram("todo", "rm", "2").exit_code == 0 and Store.load().get_todo(2) is None
    r = cram("todo", "clear", "-w", "last")
    assert "Cleared 1" in r.output and Store.load().get_todo(3) is None


def test_todo_shows_in_the_week_view_and_undo():
    cram("todo", "add", "finish", "essay")
    assert "to-do" in cram("v-week").output and "finish essay" in cram("v-week").output
    assert "add to-do “finish essay”" in cram("undo").output
    assert Store.load().todos == []


def complete(line):
    """What tab would offer for `line` in bash (a trailing space means a new, empty word)."""
    words = line.split()
    cword = len(words) if line.endswith(" ") else len(words) - 1
    r = runner.invoke(app, [], prog_name="cram",
                      env={"_CRAM_COMPLETE": "complete_bash", "COMP_WORDS": line, "COMP_CWORD": str(cword)})
    return r.output.split()


def test_tab_completes_commands_and_class_names_word_by_word():
    cram("class", "add", "Linear Algebra, Linear Programming, Chemistry")
    assert {"in", "out", "todo", "class"} <= set(complete("cram "))
    assert complete("cram in lin") == ["linear"]
    assert complete("cram in linear ") == ["algebra", "programming"]
    assert complete("cram in --at 9am ch") == ["chemistry"]
    assert set(complete("cram in -c ")) == {"none", "la", "lp", "chemistry"}   # a word cram will understand
    assert complete("cram class rm linear p") == ["programming"]


def test_tab_completes_todo_numbers_weeks_and_days():
    cram("todo", "add", "read ch 7")
    cram("todo", "add", "problem set")
    cram("todo", "done", "2")
    assert complete("cram todo done ") == ["1"]          # only the open ones
    assert complete("cram todo uncheck ") == ["2"]       # only the checked-off ones
    assert complete("cram v-week -w ") == ["this", "last", "next", "-2"]
    assert complete("cram v-today ye") == ["yesterday"]


def test_week_next_works():
    from cram.timeutil import parse_week
    assert parse_week("next", MON) == MON + timedelta(weeks=1)


def test_swapped_add_todo_is_caught_not_saved_as_a_note():
    cram("in", "--at", "10/14/26 9am")
    r = cram("add", "todo", "review the statics notes/slides from mon and wednesday")
    assert r.exit_code == 1 and "cram todo add" in r.output
    assert Store.load().running().notes == [] and Store.load().todos == []


def test_swapped_add_todo_in_a_terminal_offers_the_fix(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: True)
    cram("in", "--at", "10/14/26 9am")
    r = cram("add", "todo", "review the statics notes", input="y\n")
    assert r.exit_code == 0, r.output
    assert [t.text for t in Store.load().todos] == ["review the statics notes"]
    assert Store.load().running().notes == []
    r = cram("add", "class", "Statics", input="y\n")
    assert Store.load().classes == ["Statics"]


def test_swapped_add_todo_answered_no_keeps_it_as_a_note(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: True)
    cram("in", "--at", "10/14/26 9am")
    cram("add", "todo", "list for later", input="n\n")
    assert Store.load().running().notes == ["todo list for later"] and Store.load().todos == []


def test_quoted_note_starting_with_todo_is_just_a_note():
    cram("in", "--at", "10/14/26 9am")
    assert cram("add", "todo: ask about the quiz").exit_code == 0
    assert Store.load().running().notes == ["todo: ask about the quiz"]


def test_list_and_remove_aliases():
    cram("class", "add", "Chemistry, Statics")
    cram("todo", "add", "read ch 7")
    assert "Chemistry" in cram("class", "list").output and "Chemistry" in cram("class", "ls").output
    assert "read ch 7" in cram("todo", "list").output and "read ch 7" in cram("todo", "ls").output
    assert cram("class", "remove", "statics").exit_code == 0 and Store.load().classes == ["Chemistry"]
    assert cram("todo", "delete", "1").exit_code == 0 and Store.load().todos == []
    assert set(complete("cram todo ")) == {"add", "list", "done", "uncheck", "rm", "clear"}   # aliases stay hidden
