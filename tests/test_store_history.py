"""Saving, overlap checks, damaged files and undo/redo."""
import json

import pytest

from cram import history
from cram.paths import data_file
from cram.store import DataError, Store
from tests.conftest import MON, dt


def make(*blocks):
    st = Store.load()
    for start, end, notes in blocks:
        st.add(dt(start), dt(end) if end else None, list(notes))
    st.save()
    return Store.load()


def test_empty_store_and_roundtrip():
    assert Store.load().sessions == []
    st = make(("2026-10-14 09:00", "2026-10-14 10:30", ["standup", "emails"]))
    s = st.sessions[0]
    assert (s.minutes(), s.notes, s.week) == (90, ["standup", "emails"], MON)


def test_back_to_back_blocks_do_not_overlap():
    st = make(("2026-10-14 09:00", "2026-10-14 10:00", []))
    assert st.overlap(dt("2026-10-14 10:00"), dt("2026-10-14 11:00")) is None
    assert st.overlap(dt("2026-10-14 09:59"), dt("2026-10-14 11:00")) is not None


def test_running_block_blocks_everything_after_it():
    st = make(("2026-10-14 09:00", None, []))
    assert st.overlap(dt("2026-10-20 09:00"), dt("2026-10-20 10:00")) is not None
    assert st.overlap(dt("2026-10-14 08:00"), dt("2026-10-14 09:00")) is None


def test_block_past_midnight_belongs_to_its_start_day_and_week():
    st = make(("2026-10-18 23:30", "2026-10-19 00:45", ["late night"]))  # Sunday into Monday
    s = st.sessions[0]
    assert s.day.isoformat() == "2026-10-18" and s.week == MON and s.minutes() == 75


def test_saving_nothing_new_adds_no_undo_step():
    st = make(("2026-10-14 09:00", "2026-10-14 10:00", []))
    before = len(list((data_file().parent / "undo").glob("*.json")))
    st.save()
    assert len(list((data_file().parent / "undo").glob("*.json"))) == before


def test_undo_redo_walk_back_and_forth():
    st = make(("2026-10-14 09:00", "2026-10-14 10:00", ["a"]))
    st.sessions[0].notes.append("b")
    st.save()
    assert history.peek() == "add “b”"
    assert history.undo() == "add “b”"
    assert Store.load().sessions[0].notes == ["a"]
    assert history.redo() == "add “b”"
    assert Store.load().sessions[0].notes == ["a", "b"]


def test_undo_everything_then_nothing_left():
    make(("2026-10-14 09:00", "2026-10-14 10:00", []))
    assert history.undo().startswith("add session")
    assert Store.load().sessions == []           # back to before the first save
    assert history.undo() is None                # pressing undo again is harmless


def test_new_change_after_undo_clears_redo():
    st = make(("2026-10-14 09:00", "2026-10-14 10:00", ["a"]))
    st.sessions[0].notes.append("b")
    st.save()
    history.undo()
    st = Store.load()
    st.sessions[0].notes.append("c")
    st.save()
    assert history.redo() is None


def test_undo_labels_describe_the_change():
    st = make(("2026-10-14 09:00", None, []))
    assert history.peek().startswith("start studying")
    st.sessions[0].end = dt("2026-10-14 10:00")
    st.save()
    assert history.peek().startswith("stop")
    st = Store.load()
    st.remove(st.sessions[0].id)
    st.save()
    assert history.peek().startswith("delete session")


def test_undo_history_is_capped(monkeypatch):
    monkeypatch.setattr(history, "LIMIT", 5)
    st = make(("2026-10-14 09:00", "2026-10-14 10:00", []))
    for i in range(12):
        st.sessions[0].notes.append(str(i))
        st.save()
    assert len(list((data_file().parent / "undo").glob("*.json"))) == 5


def test_damaged_data_file_falls_back_to_backup(capsys):
    st = make(("2026-10-14 09:00", "2026-10-14 10:00", ["good"]))
    st.sessions[0].notes.append("also good")
    st.save()                                      # writes a .bak of the previous version
    data_file().write_text('{"sessions": [ oops')  # someone hand-edited it badly
    st = Store.load()
    assert st.sessions[0].notes == ["good"]
    assert data_file().with_suffix(".json.broken").exists()
    assert "restored the backup" in capsys.readouterr().err


def test_damaged_file_without_backup_raises_a_clear_error():
    data_file().write_text("not json")
    with pytest.raises(DataError):
        Store.load()


def test_saved_file_is_valid_json_with_sorted_sessions():
    make(("2026-10-15 09:00", "2026-10-15 10:00", []), ("2026-10-14 09:00", "2026-10-14 10:00", []))
    raw = json.loads(data_file().read_text())
    assert [s["start"] for s in raw["sessions"]] == sorted(s["start"] for s in raw["sessions"])
