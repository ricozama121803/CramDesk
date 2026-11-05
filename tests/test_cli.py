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


def _this_week_blocks():
    """One session today and one on another day of this week."""
    today = date.today()
    other = today + timedelta(days=1 if today.weekday() == 0 else -1)
    st = Store.load()
    st.add(datetime.combine(today, time(1)), datetime.combine(today, time(2)), ["today thing"])
    st.add(datetime.combine(other, time(1)), datetime.combine(other, time(2)), ["other thing"])
    st.save()


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
    assert r.exit_code == 0 and "Created Linear Algebra" in r.output and "Created Chemistry" in r.output
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
    cram("todo", "add", "read", "ch", "7")
    cram("todo", "add", "-w", "last", "review", "notes")
    cram("todo", "add", "-w", "last", "old", "finished", "thing")
    cram("todo", "done", "3")
    t = Store.load().get_todo(1)
    assert t.course is None and t.text == "read ch 7"       # notes aren't tagged with a class
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
    assert "memo board" in cram("v-week").output and "finish essay" in cram("v-week").output
    assert "add memo “finish essay”" in cram("undo").output
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
    assert set(complete("cram ")) == {"start", "stop", "log", "add", "create", "ls-class", "ls-log", "ls-memo"}
    assert set(complete("cram add ")) == {"details", "memo", "block"}
    assert complete("cram log lin") == ["linear"]
    assert complete("cram start lin") == ["linear"]
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


def test_list_and_remove_aliases():
    cram("class", "add", "Chemistry, Statics")
    cram("todo", "add", "read ch 7")
    assert "Chemistry" in cram("class", "list").output and "Chemistry" in cram("class", "ls").output
    assert "read ch 7" in cram("todo", "list").output and "read ch 7" in cram("todo", "ls").output
    assert cram("class", "remove", "statics").exit_code == 0 and Store.load().classes == ["Chemistry"]
    assert cram("todo", "delete", "1").exit_code == 0 and Store.load().todos == []




# ── the commands you see ─────────────────────────────────────────────────────

def test_start_details_stop():
    cram("create", "class", "Statics, Calc III")
    r = cram("start", "--at", "10/14/26 9am", "stat", "review", "slides")
    assert r.exit_code == 0, r.output
    run_ = Store.load().running()
    assert run_.course == "Statics" and run_.notes == ["review slides"]
    assert cram("add", "details", "ch 5 problems").exit_code == 0
    r = cram("stop", "--at", "10/14/26 10:30am")
    assert r.exit_code == 0 and "stopped" in r.output
    s = Store.load().sessions[0]
    assert s.end is not None and s.notes == ["review slides", "ch 5 problems"]


def test_start_without_a_class_asks_in_a_terminal(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: True)
    cram("create", "class", "Statics, Calc III")
    r = cram("start", "--at", "10/14/26 9am", input="2\n")
    assert r.exit_code == 0, r.output
    assert Store.load().running().course == "Calc III"


def test_start_without_a_class_and_none_yet_asks_for_a_name(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: True)
    r = cram("start", "--at", "10/14/26 9am", input="Statics\n")
    assert r.exit_code == 0, r.output
    assert Store.load().classes == ["Statics"] and Store.load().running().course == "Statics"


def test_start_without_a_class_outside_a_terminal_says_which():
    cram("create", "class", "Statics")
    r = cram("start", "--at", "10/14/26 9am")
    assert r.exit_code == 1 and "Which class?" in r.output and "Statics" in r.output
    assert Store.load().sessions == []


def test_start_with_an_unknown_class():
    r = cram("start", "--at", "10/14/26 9am", "bio")
    assert r.exit_code == 1 and "cram create class bio" in r.output


def test_details_and_stop_need_a_running_block():
    assert cram("add", "details", "x").exit_code == 1
    assert cram("stop").exit_code == 1


def test_add_memo_and_ls_memo():
    assert cram("add", "memo", "email the TA").exit_code == 0
    assert cram("add", "todo", "old word works").exit_code == 0        # hidden alias
    r = cram("ls-memo")
    assert "memo board" in r.output and "email the TA" in r.output and "old word works" in r.output


def test_log_per_class():
    cram("create", "class", "Statics, Calc III")
    assert cram("log", "statics", "finished", "ch", "5").exit_code == 0
    assert cram("log", "calc", "iii", "practice exam 2").exit_code == 0
    r = cram("log", "no class given")
    assert r.exit_code == 1 and "Which class" in r.output
    assert cram("log", "statics").exit_code == 1                        # a class but nothing logged
    cram("start", "--at", "10/14/26 9am", "calc")
    assert cram("log", "series", "tests", "clicking").exit_code == 0    # goes to the class you're in
    st = Store.load()
    assert [(n.course, n.text) for n in st.notes] == [("Statics", "finished ch 5"), ("Calc III", "practice exam 2"),
                                                      ("Calc III", "series tests clicking")]
    r = cram("ls-log", "stat")
    assert "finished ch 5" in r.output and "practice exam" not in r.output
    r = cram("ls-log")
    assert "Statics" in r.output and "Calc III" in r.output and "series tests clicking" in r.output
    cram("class", "rename", "calc iii", "Calc 3")
    assert {n.course for n in Store.load().notes} == {"Statics", "Calc 3"}   # the log follows a rename
    assert cram("add", "note", "x").exit_code == 2                     # 'note' is gone


def test_memo_can_be_tagged_with_a_class():
    cram("create", "class", "Statics")
    cram("add", "memo", "statics: review the slides")
    cram("add", "memo", "-c", "stat", "print the lab sheet")
    cram("add", "memo", "note: not a class, so not a tag")
    cram("add", "memo", "email the TA")
    assert [(t.course, t.text) for t in Store.load().todos] == [
        ("Statics", "review the slides"), ("Statics", "print the lab sheet"),
        (None, "note: not a class, so not a tag"), (None, "email the TA")]
    assert cram("add", "memo", "-c", "bio", "x").exit_code == 1
    assert "Statics" in cram("ls-memo").output



def test_add_block_asks_everything(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: True)
    cram("create", "class", "Statics")
    r = cram("add", "block", input="1\n10/14/26\n9am\n10:15am\nmoments\n")
    assert r.exit_code == 0, r.output
    s = Store.load().sessions[0]
    assert (s.course, s.start, s.end, s.notes) == ("Statics", datetime(2026, 10, 14, 9),
                                                    datetime(2026, 10, 14, 10, 15), ["moments"])


def test_add_block_retries_an_overlap(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: True)
    cram("create", "class", "Statics")
    block("10/14/26", 9, 10, "a")
    r = cram("add", "block", input="1\n10/14/26\n9:30am\n11am\n10/14/26\n10am\n11am\n\n")
    assert r.exit_code == 0 and "overlaps" in r.output
    assert len(Store.load().sessions) == 2


def test_create_class_and_ls_class():
    r = cram("create", "class", "Statics, Calc III")
    assert r.exit_code == 0 and "cram start statics" in r.output
    r = cram("ls-class")
    assert "Statics" in r.output and "Calc III" in r.output


def test_old_commands_still_work_but_are_hidden():
    cram("class", "add", "Statics")
    assert cram("in", "--at", "10/14/26 9am", "statics").exit_code == 0
    assert cram("out", "--at", "10/14/26 10am", "x").exit_code == 0
    r = cram("--help")
    listed = r.output[r.output.index("Commands"):]
    for old in ("│ in ", "│ out ", "│ v-week", "│ todo", "│ class ", "│ cycle-theme", "│ edit", "│ undo"):
        assert old not in listed, old



def test_add_memo_asks_the_class_in_a_terminal(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: True)
    cram("create", "class", "Calc III, Statics")
    r = cram("add", "memo", "refresh on statics slides from this week", input="\n")
    assert "(mentioned)" in r.output                          # Statics is offered first...
    cram("add", "memo", "email the TA", input="\n")          # ...and with nothing mentioned, enter = no class
    cram("add", "memo", "email the TA again", input="1\n")
    cram("add", "memo", "statics: tagged already")            # no question
    assert [(t.course, t.text) for t in Store.load().todos] == [
        ("Statics", "refresh on statics slides from this week"), (None, "email the TA"),
        ("Calc III", "email the TA again"), ("Statics", "tagged already")]


def test_add_memo_without_a_terminal_does_not_ask():
    cram("create", "class", "Statics")
    assert cram("add", "memo", "refresh on statics slides").exit_code == 0
    assert Store.load().todos[0].course is None
