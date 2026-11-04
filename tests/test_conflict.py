"""Sessions whose times overlap: which fixes are offered and what they do."""
from cram import conflict
from cram.store import Store
from tests.conftest import dt


def setup(*blocks):
    st = Store.load()
    for start, end, notes in blocks:
        st.add(dt(start), dt(end) if end else None, list(notes))
    st.save()
    return Store.load()


NEW = (dt("2026-10-14 22:30"), dt("2026-10-14 22:47"))   # the case from the screenshot


def test_all_four_fixes_offered_for_a_partial_overlap():
    st = setup(("2026-10-14 22:32", "2026-10-14 22:49", ["reading docs"]))
    assert set(conflict.options(st, *NEW, None)) == {"merge", "trim", "shorten", "replace"}


def test_merge_unions_times_and_keeps_every_item_in_order():
    st = setup(("2026-10-14 22:32", "2026-10-14 22:49", ["reading docs", "test"]))
    sid = conflict.apply(st, "merge", *NEW, ["test", "new thing"], None)
    st = Store.load()
    assert len(st.sessions) == 1
    s = st.get(sid)
    assert (s.start, s.end) == (dt("2026-10-14 22:30"), dt("2026-10-14 22:49"))
    assert s.notes == ["test", "new thing", "reading docs"]          # no duplicates


def test_trim_fits_into_the_free_gap():
    st = setup(("2026-10-14 22:32", "2026-10-14 22:49", []))
    sid = conflict.apply(st, "trim", *NEW, ["x"], None)
    s = Store.load().get(sid)
    assert (s.start, s.end) == (dt("2026-10-14 22:30"), dt("2026-10-14 22:32"))


def test_trim_picks_the_biggest_gap_between_two_blocks():
    st = setup(("2026-10-14 10:10", "2026-10-14 10:20", []), ("2026-10-14 10:50", "2026-10-14 10:55", []))
    assert conflict.free_slot(st, dt("2026-10-14 10:00"), dt("2026-10-14 11:00"), None) == \
        (dt("2026-10-14 10:20"), dt("2026-10-14 10:50"))


def test_no_trim_when_fully_covered():
    st = setup(("2026-10-14 09:00", "2026-10-14 12:00", []))
    opts = conflict.options(st, dt("2026-10-14 10:00"), dt("2026-10-14 11:00"), None)
    assert "trim" not in opts and "shorten" not in opts       # can't shorten a block that surrounds this one
    assert "merge" in opts and "replace" in opts


def test_shorten_moves_the_other_block_out_of_the_way():
    st = setup(("2026-10-14 22:32", "2026-10-14 22:49", ["docs"]))
    conflict.apply(st, "shorten", *NEW, ["test"], None)
    other = [s for s in Store.load().sessions if s.notes == ["docs"]][0]
    assert (other.start, other.end) == (dt("2026-10-14 22:47"), dt("2026-10-14 22:49"))


def test_replace_deletes_the_other_block():
    st = setup(("2026-10-14 22:32", "2026-10-14 22:49", ["docs"]))
    conflict.apply(st, "replace", *NEW, ["test"], None)
    assert [s.notes for s in Store.load().sessions] == [["test"]]


def test_never_offer_replace_for_a_running_block():
    st = setup(("2026-10-14 22:32", None, ["live"]))
    opts = conflict.options(st, *NEW, None)
    assert "replace" not in opts


def test_merge_with_running_block_stays_running():
    st = setup(("2026-10-14 22:32", None, ["live"]))
    sid = conflict.apply(st, "merge", *NEW, ["earlier"], None)
    s = Store.load().get(sid)
    assert s.running and s.start == dt("2026-10-14 22:30") and s.notes == ["earlier", "live"]


def test_editing_a_block_into_its_neighbor_keeps_its_id():
    st = setup(("2026-10-14 09:00", "2026-10-14 10:00", ["a"]), ("2026-10-14 11:00", "2026-10-14 12:00", ["b"]))
    a = [s for s in st.sessions if s.notes == ["a"]][0]
    sid = conflict.apply(st, "merge", dt("2026-10-14 09:00"), dt("2026-10-14 11:30"), ["a"], a.id)
    assert sid == a.id and len(Store.load().sessions) == 1
