"""The full-screen app, driven with real key presses, focused on things users do by accident."""
import asyncio
from datetime import datetime, timedelta

from cram.store import Store
from cram.timeutil import now
from cram.tui import BlockEditor, ConflictScreen, DayScreen, CramApp, QuitScreen


def run(script, size=(120, 40)):
    """Run an async test body against a fresh app."""
    async def main():
        from cram import config
        config.set_value("ui", "onboarded", True)   # skip first-run setup (tested on its own)
        app = CramApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await script(app, pilot)
    asyncio.run(main())


async def type_(pilot, text, enter=True):
    await pilot.press(*text)
    if enter:
        await pilot.press("enter")
    await pilot.pause()


def punched_in_earlier(minutes=30, notes=("working",)):
    st = Store.load()
    st.add(now() - timedelta(minutes=minutes), None, list(notes))
    st.save()


def with_classes(*names):
    st = Store.load()
    for n in names:
        st.add_class(n)
    st.save()


def test_start_and_stop_from_the_bar():
    with_classes("Statics", "Calc III")
    async def script(app, pilot):
        await type_(pilot, "start stat review slides")
        run_ = Store.load().running()
        assert run_.course == "Statics" and run_.notes == ["review slides"]
        assert app.focused.id == "new"                      # cursor jumps to the details box
        await type_(pilot, "ch 5 problems")
        await type_(pilot, "stop")                          # typed in the details box: still stops
        assert Store.load().running() is None                # right away, no dialog
        assert Store.load().sessions[0].notes == ["review slides", "ch 5 problems"]
    run(script)


def test_start_without_a_class_asks_which():
    from cram.tui import ClassPicker
    with_classes("Statics", "Calc III")
    async def script(app, pilot):
        await type_(pilot, "start")
        assert isinstance(app.screen, ClassPicker)
        await pilot.press("down", "enter"); await pilot.pause()
        assert Store.load().running().course == "Calc III"
    run(script)


def test_start_with_no_classes_yet_names_the_first_one():
    from cram.tui import ClassPicker
    async def script(app, pilot):
        await pilot.press("ctrl+s"); await pilot.pause()
        assert isinstance(app.screen, ClassPicker)
        await type_(pilot, "Statics")
        st = Store.load()
        assert st.classes == ["Statics"] and st.running().course == "Statics"
    run(script)


def test_start_with_an_unknown_class_offers_to_create_it():
    from cram.tui import ClassPicker
    with_classes("Statics")
    async def script(app, pilot):
        await type_(pilot, "start biology")
        await pilot.press("y"); await pilot.pause()
        assert Store.load().running().course == "biology"
        await type_(pilot, "stop")
        await type_(pilot, "start chem")
        await pilot.press("n"); await pilot.pause()         # don't create it: pick one instead
        assert isinstance(app.screen, ClassPicker)
    run(script)


def test_random_text_in_the_prompt_bar_is_kept_not_lost():
    async def script(app, pilot):
        await type_(pilot, "fixing stuff")
        assert app.focused.value == "fixing stuff"          # left there with a hint, nothing logged
        assert Store.load().sessions == []
    run(script)


def test_item_typed_while_off_the_clock_is_not_thrown_away():
    with_classes("Statics")
    async def script(app, pilot):
        app.main.query_one("#new").focus()
        await type_(pilot, "early idea")
        assert app.main.query_one("#new").value == "early idea"
        assert app.focused.id == "cmd"                      # sent to the bottom bar to type `start`
        await type_(pilot, "start statics")
        assert Store.load().running().notes == ["early idea"]   # carried into the block
    run(script)


def test_double_in_and_stray_out_are_harmless():
    async def script(app, pilot):
        await type_(pilot, "out")
        assert Store.load().sessions == []
        await type_(pilot, "in")
        app.main.query_one("#cmd").focus()
        await type_(pilot, "in")
        assert len(Store.load().sessions) == 1
    run(script)


def test_status_line_only_says_unsaved_for_typed_but_not_added_text():
    punched_in_earlier()
    async def script(app, pilot):
        app.main.reload()
        assert "studying" in app.main.status_text and "unsaved" not in app.main.status_text
        app.main.query_one("#new").value = "half typed"
        app.main.reload()
        assert "unsaved" in app.main.status_text
    run(script, size=(130, 40))


def test_slash_menu_arrows_and_enter():
    async def script(app, pilot):
        await pilot.press("/", "down", "down", "up"); await pilot.pause()
        assert app.main.query_one("#slash").highlighted == 1
        await pilot.press("enter"); await pilot.pause()
        assert type(app.screen).__name__ == "LogScreen"      # /vweek
        await pilot.press("escape"); await pilot.pause()
        await type_(pilot, "/nonsense")
        assert type(app.screen).__name__ == "MainScreen"
    run(script)


def test_overlap_while_adding_a_past_block_asks_and_esc_saves_nothing():
    st = Store.load()
    st.add(datetime(2026, 10, 14, 22, 32), datetime(2026, 10, 14, 22, 49), ["docs"])
    st.save()
    async def script(app, pilot):
        app.push_screen(DayScreen(datetime(2026, 10, 14).date())); await pilot.pause()
        await pilot.press("down", "enter"); await pilot.pause()        # the "+ add a block" row
        assert isinstance(app.screen, BlockEditor)
        await type_(pilot, "10:30pm"); await type_(pilot, "10:47pm")
        await pilot.press("enter"); await pilot.pause()                 # class: none
        await type_(pilot, "test")
        assert isinstance(app.screen, ConflictScreen)
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app.screen, BlockEditor)
        assert [s.notes for s in Store.load().sessions] == [["docs"]]   # nothing changed
    run(script)


def test_delete_on_the_plus_row_does_nothing():
    st = Store.load()
    st.add(datetime(2026, 10, 14, 9), datetime(2026, 10, 14, 10), ["keep me"])
    st.save()
    async def script(app, pilot):
        app.push_screen(DayScreen(datetime(2026, 10, 14).date())); await pilot.pause()
        await pilot.press("down", "delete"); await pilot.pause()
        assert type(app.screen).__name__ == "DayScreen"
        assert len(Store.load().sessions) == 1
    run(script)


def test_end_time_in_the_future_is_refused():
    st = Store.load()
    st.add(datetime(2026, 10, 14, 9), datetime(2026, 10, 14, 10), ["a"])
    st.save()
    async def script(app, pilot):
        app.push_screen(BlockEditor(Store.load().sessions[0], "edit")); await pilot.pause()
        app.screen.query_one("#day").value = (now() + timedelta(days=2)).strftime("%m/%d/%y")
        await pilot.press("ctrl+s"); await pilot.pause()
        assert isinstance(app.screen, BlockEditor)
        assert "future" in str(app.screen.query_one("#error").render())
        assert Store.load().sessions[0].start == datetime(2026, 10, 14, 9)
    run(script)


def test_undo_is_ignored_inside_a_dialog_and_works_outside():
    from cram.tui import TodoScreen
    with_classes("Statics")
    async def script(app, pilot):
        await type_(pilot, "start statics first thing")
        await type_(pilot, "second thing")
        await pilot.press("ctrl+z"); await pilot.pause()
        assert Store.load().running().notes == ["first thing"]
        app.main.action_todo(); await pilot.pause()                   # a dialog
        await pilot.press("ctrl+z"); await pilot.pause()              # ignored there
        assert isinstance(app.screen, TodoScreen) and Store.load().running().notes == ["first thing"]
    run(script)


def test_quit_off_the_clock_dont_save_drops_the_draft():
    async def script(app, pilot):
        app.main.query_one("#new").value = "never mind"
        await pilot.press("ctrl+q"); await pilot.pause()
        assert isinstance(app.screen, QuitScreen)
        await pilot.press("escape"); await pilot.pause()
        assert app.is_running and type(app.screen).__name__ == "MainScreen"
        await pilot.press("ctrl+q"); await pilot.pause()
        await pilot.press("d"); await pilot.pause()
        assert app.main.query_one("#new").value == ""
    run(script)


def test_quit_while_clocked_in_and_stay_keeps_the_typed_item():
    punched_in_earlier()
    async def script(app, pilot):
        app.main.reload()
        app.main.query_one("#new").value = "last thing"
        await pilot.press("ctrl+q"); await pilot.pause()
        await pilot.press("k"); await pilot.pause()
        assert Store.load().running().notes == ["working", "last thing"]
    run(script)


def test_running_block_can_get_descriptions_without_being_ended():
    punched_in_earlier()
    async def script(app, pilot):
        app.push_screen(DayScreen(Store.load().running().day)); await pilot.pause()   # the block's own day (it may have started yesterday)
        await pilot.press("enter"); await pilot.pause()
        assert app.screen.live and not app.screen.query_one("#times").display
        await type_(pilot, "added later")
        await pilot.press("escape"); await pilot.pause()
        s = Store.load().running()
        assert s is not None and s.notes == ["working", "added later"]
    run(script)


def test_tab_toggles_areas_and_arrows_move_in_top():
    punched_in_earlier(notes=("one", "two"))

    async def body(app, pilot):
        app.main.reload(); await pilot.pause()
        assert app.focused.id == "cmd"
        await pilot.press("tab"); await pilot.pause()
        assert app.focused.id == "new"                      # tab lands in the top section
        await pilot.press("right"); await pilot.pause()
        assert app.focused.id == "today"                    # empty box: right goes to the panels
        await pilot.press("down"); await pilot.pause()
        assert app.focused.id == "week"
        await pilot.press("up", "left"); await pilot.pause()
        assert app.focused.id == "new"
        await pilot.press("up"); await pilot.pause()
        assert app.focused.id == "items"
        await pilot.press("down"); await pilot.pause()
        assert app.focused.id == "new"                      # past the last item, back to the box
        await pilot.press("right", "down"); await pilot.pause()
        assert app.focused.id == "week"
        await pilot.press("tab"); await pilot.pause()
        assert app.focused.id == "cmd"                      # tab goes back to the prompt bar
        await pilot.press("shift+tab"); await pilot.pause()
        assert app.focused.id == "week"                     # ...and returns where you were
        await pilot.press("tab", "tab"); await pilot.pause()
        await pilot.press("left"); await pilot.pause()
        await pilot.press(*"ab", "left", "right"); await pilot.pause()
        assert app.focused.id == "new"                      # right inside text just moves the cursor
        await pilot.press("right"); await pilot.pause()
        assert app.focused.id == "today"                    # at the end of the text it moves over
    run(body)


def test_first_run_onboarding_adds_classes_then_a_theme():
    from cram import config
    from cram.tui import OnboardingScreen

    async def main():
        app = CramApp()                                     # fresh home: no config yet
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(); await pilot.pause()
            assert isinstance(app.screen, OnboardingScreen)
            assert [sid for sid, _ in app.screen.STEPS] == ["welcome", "classes", "look", "tour"]
            await pilot.press("enter"); await pilot.pause()                 # welcome
            assert app.focused.id == "ob-class"
            await type_(pilot, "Linear Algebra")
            await type_(pilot, "Chemistry, US History")                     # several at once
            await pilot.press("enter"); await pilot.pause()                 # empty enter: done
            assert app.focused.id == "themes"
            await pilot.press("down", "enter"); await pilot.pause()         # theme
            await pilot.press("enter"); await pilot.pause()                 # tour
            assert not isinstance(app.screen, OnboardingScreen)
            assert app.focused.id == "cmd"
        cfg = config.load()
        assert cfg["ui"]["theme"] == "nord" and cfg["ui"]["onboarded"] is True
        assert Store.load().classes == ["Linear Algebra", "Chemistry", "US History"]

    asyncio.run(main())


def test_onboarding_can_be_skipped_and_only_shows_once():
    from cram import config
    from cram.tui import OnboardingScreen

    async def main():
        for expect in (True, False):
            app = CramApp()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(); await pilot.pause()
                assert isinstance(app.screen, OnboardingScreen) is expect
                await pilot.press("escape"); await pilot.pause()
        assert config.load()["ui"]["theme"] == "catppuccin"

    asyncio.run(main())


def test_every_theme_works_in_the_app():
    from cram import ui
    from cram.tui import TEXTUAL_THEMES

    async def body(app, pilot):
        for name in ui.THEMES:
            assert name in TEXTUAL_THEMES
            app.apply_theme(name); await pilot.pause()
            assert app.theme == TEXTUAL_THEMES[name]
    run(body)


def test_in_with_a_class_from_the_prompt_bar():
    with_classes("Linear Algebra", "Chemistry")
    async def script(app, pilot):
        await type_(pilot, "in la ch 5 review")
        run = Store.load().running()
        assert run.course == "Linear Algebra" and run.notes == ["ch 5 review"]
        assert app.main.query_one("#now").border_title == "Linear Algebra"
    run(script)


def test_in_with_a_new_class_offers_to_add_it():
    with_classes("Chemistry")
    async def script(app, pilot):
        await type_(pilot, "in biology")
        await pilot.press("y"); await pilot.pause()
        st = Store.load()
        assert st.classes == ["Chemistry", "biology"] and st.running().course == "biology"
        assert st.running().notes == []
    run(script)


def test_in_with_an_ambiguous_short_name_starts_nothing():
    with_classes("Chemistry", "Chem Lab")
    async def script(app, pilot):
        await type_(pilot, "in chem")
        assert Store.load().sessions == []
    run(script)


def test_classes_screen_adds_and_removes():
    from cram.tui import ClassesScreen
    async def script(app, pilot):
        await type_(pilot, "/classes")
        assert isinstance(app.screen, ClassesScreen)
        await type_(pilot, "Linear Algebra")
        await type_(pilot, "Chemistry")
        assert Store.load().classes == ["Linear Algebra", "Chemistry"]
        app.screen.query_one("#classlist").focus(); await pilot.pause()
        await pilot.press("down", "delete"); await pilot.pause()
        await pilot.press("y"); await pilot.pause()
        assert Store.load().classes == ["Linear Algebra"]
        await pilot.press("escape"); await pilot.pause()
        assert not isinstance(app.screen, ClassesScreen)
    run(script)


def test_class_set_in_the_block_editor():
    with_classes("Linear Algebra", "Chemistry")
    st = Store.load()
    st.add(datetime(2026, 10, 14, 9), datetime(2026, 10, 14, 10), ["reading"])
    st.save()
    async def script(app, pilot):
        app.push_screen(BlockEditor(Store.load().sessions[0], "edit")); await pilot.pause()
        app.screen.query_one("#course").focus(); await pilot.pause()
        await type_(pilot, "chemistry")
        assert Store.load().sessions[0].course == "Chemistry"
    run(script)


def test_todo_screen_add_check_off_and_remove():
    from datetime import date, timedelta as td
    from cram.timeutil import week_start
    from cram.tui import TodoScreen
    with_classes("Linear Algebra")
    st = Store.load()
    st.add_todo("review notes", week_start(date.today()) - td(weeks=1))   # left open last week
    st.save()
    async def script(app, pilot):
        await type_(pilot, "/todo")
        assert isinstance(app.screen, TodoScreen)
        await type_(pilot, "finish problem set 4")
        await type_(pilot, "email the TA")
        st = Store.load()
        t = next(t for t in st.todos if t.text == "finish problem set 4")
        assert t.course is None and not t.done                            # notes aren't tagged
        ol = app.screen.query_one("#todolist")
        ol.focus(); await pilot.pause()
        ol.highlighted = 0
        await pilot.press("enter"); await pilot.pause()                   # check off the first one
        assert sum(t.done for t in Store.load().todos) == 1
        done_id = next(str(t.id) for t in Store.load().todos if t.done)   # checked ones sink to the bottom
        ol.highlighted = next(i for i in range(ol.option_count) if ol.get_option_at_index(i).id == done_id)
        await pilot.press("space"); await pilot.pause()                   # and back
        assert sum(t.done for t in Store.load().todos) == 0
        ol.highlighted = ol.option_count - 1                               # the carried-over one
        await pilot.press("delete"); await pilot.pause()
        assert [t.text for t in Store.load().todos] == ["finish problem set 4", "email the TA"]
        await pilot.press("escape"); await pilot.pause()
        assert not isinstance(app.screen, TodoScreen)
        notes = app.main.query_one("#notes")
        assert notes.border_subtitle == "notes 0/2 done"
        shown = "".join(seg.text for line in notes.render_lines(notes.region.reset_offset) for seg in line)
        assert "finish problem set 4" in shown and "email the TA" in shown
    run(script)


def test_bar_uses_the_same_words_as_the_shell():
    with_classes("Statics")
    async def script(app, pilot):
        for line in ("start statics", "add details ch 5 problems", "add note read ch 7",
                     "note problem set 4", "create class Calc III"):
            app.main.query_one("#cmd").focus()
            await type_(pilot, line)
        st = Store.load()
        assert st.running().notes == ["ch 5 problems"]
        assert [t.text for t in st.todos] == ["read ch 7", "problem set 4"]
        assert st.classes == ["Statics", "Calc III"]
    run(script)


def test_notes_panel_shows_open_todos_and_opens_the_list():
    from cram.tui import TodoScreen
    from datetime import date
    from cram.timeutil import week_start
    st = Store.load()
    for text in ("read ch 7", "problem set 4"):
        st.add_todo(text, week_start(date.today()))
    st.get_todo(2).done = True
    st.save()
    async def script(app, pilot):
        await pilot.pause()
        notes = app.main.query_one("#notes")
        shown = "".join(seg.text for line in notes.render_lines(notes.region.reset_offset) for seg in line)
        assert "read ch 7" in shown and "problem set 4" not in shown and "1 done" in shown
        app.main.query_one("#new").focus(); await pilot.pause()
        await pilot.press("down"); await pilot.pause()       # down from the notes box below the session
        assert app.focused.id == "notes"
        await pilot.press("enter"); await pilot.pause()
        assert isinstance(app.screen, TodoScreen)
    run(script)
