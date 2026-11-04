"""Config edits and the week totals."""
import tomllib

import pytest

from cram import config, report
from cram.paths import config_file
from cram.store import Store
from tests.conftest import MON, dt


def test_first_run_writes_a_config_with_defaults():
    cfg = config.load()
    assert config_file().exists()
    assert cfg["ui"]["theme"] == "catppuccin" and cfg["ui"]["mascot"] is True
    assert "invoice" not in cfg and "client" not in cfg


def test_set_value_keeps_comments_and_handles_quotes():
    config.load()
    config.set_value("ui", "theme", "nord")
    config.set_value("extra", "note", 'say "hi"')
    text = config_file().read_text()
    assert "# cram config" in text and "# catppuccin | nord" in text
    cfg = config.load()
    assert cfg["ui"]["theme"] == "nord" and cfg["extra"]["note"] == 'say "hi"'


def test_set_value_adds_missing_keys_and_sections():
    config.load()
    config.set_value("ui", "mascot", False)
    config.set_value("extra", "thing", "yes")
    cfg = config.load()
    assert cfg["ui"]["mascot"] is False and cfg["extra"]["thing"] == "yes"


def test_partial_user_config_is_filled_from_defaults():
    config_file().parent.mkdir(parents=True, exist_ok=True)
    config_file().write_text('[ui]\ntheme = "nord"\n')
    cfg = config.load()
    assert cfg["ui"]["theme"] == "nord" and cfg["ui"]["banner"] is True


def test_broken_config_raises():
    config_file().parent.mkdir(parents=True, exist_ok=True)
    config_file().write_text('[ui\ntheme = ')
    with pytest.raises(tomllib.TOMLDecodeError):
        config.load()


def week(*blocks):
    st = Store.load()
    for start, end, notes in blocks:
        st.add(dt(start), dt(end), list(notes))
    st.save()
    return Store.load()


def test_week_totals_add_up_every_day():
    st = week(("2026-10-13 16:00", "2026-10-13 17:03", ["Linear algebra"]),
              ("2026-10-14 23:00", "2026-10-15 00:12", ["Chem"]),
              ("2026-10-15 11:00", "2026-10-15 13:26", ["Linear algebra"]),
              ("2026-10-15 15:00", "2026-10-15 16:30", ["History"]),
              ("2026-10-18 20:00", "2026-10-18 23:09", ["Chem"]))
    r = report.build(st, config.load(), MON)
    assert r.total_minutes == 560
    assert [b.day.day for b in r.days] == [13, 14, 15, 18]


def test_old_data_with_approved_weeks_still_loads():
    """A data file from before invoices were removed still opens, and the old week records are dropped."""
    import json
    from cram.paths import data_file
    data_file().write_text(json.dumps({
        "version": 1, "next_id": 2,
        "sessions": [{"id": 1, "start": "2026-10-14T09:00", "end": "2026-10-14T11:00", "notes": []}],
        "weeks": {MON.isoformat(): {"billed_minutes": 120, "invoice_number": 7}},
    }))
    st = Store.load()
    assert len(st.sessions) == 1
    st.add(dt("2026-10-15 09:00"), dt("2026-10-15 10:00"), [])
    st.save()
    assert "weeks" not in json.loads(data_file().read_text())
