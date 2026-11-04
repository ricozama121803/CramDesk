"""Config edits, report numbers, export file names and the PDF itself."""
import tomllib

import pypdfium2 as pdfium
import pytest

from cram import config, pdf, report
from cram.paths import config_file
from cram.store import Store
from tests.conftest import MON, dt


def test_first_run_writes_a_config_with_placeholders():
    cfg = config.load()
    assert config_file().exists()
    assert cfg["you"]["name"] == "Your Name" and cfg["invoice"]["rate"] == 25


def test_set_value_keeps_comments_and_handles_quotes():
    config.load()
    config.set_value("you", "name", 'Rico "the Punch" Z')
    config.set_value("invoice", "rate", 32.5)
    text = config_file().read_text()
    assert "# cram config" in text and "# $ per hour" in text
    cfg = config.load()
    assert cfg["you"]["name"] == 'Rico "the Punch" Z' and cfg["invoice"]["rate"] == 32.5


def test_set_value_adds_missing_keys_and_sections():
    config.load()
    config.set_value("ui", "mascot", False)
    config.set_value("extra", "thing", "yes")
    cfg = config.load()
    assert cfg["ui"]["mascot"] is False and cfg["extra"]["thing"] == "yes"


def test_partial_user_config_is_filled_from_defaults():
    config_file().parent.mkdir(parents=True, exist_ok=True)
    config_file().write_text('[invoice]\nrate = 40\n')
    cfg = config.load()
    assert cfg["invoice"]["rate"] == 40 and cfg["invoice"]["billing"] == "down" and "you" in cfg


def test_broken_config_raises():
    config_file().parent.mkdir(parents=True, exist_ok=True)
    config_file().write_text('[invoice\nrate = ')
    with pytest.raises(tomllib.TOMLDecodeError):
        config.load()


def week(*blocks):
    st = Store.load()
    for start, end, notes in blocks:
        st.add(dt(start), dt(end), list(notes))
    st.save()
    return Store.load()


def test_report_totals_match_the_example_week():
    st = week(("2026-10-13 16:00", "2026-10-13 17:03", ["Meeting"]),
              ("2026-10-14 23:00", "2026-10-15 00:12", ["Leads"]),
              ("2026-10-15 11:00", "2026-10-15 13:26", ["Meeting"]),
              ("2026-10-15 15:00", "2026-10-15 16:30", ["Lambda"]),
              ("2026-10-18 20:00", "2026-10-18 23:09", ["Scraper"]))
    r = report.build(st, config.load(), MON)
    assert r.total_minutes == 560 and r.billed_minutes == 540 and r.amount == 225
    assert [b.day.day for b in r.days] == [13, 14, 15, 18]


def test_approved_week_keeps_its_numbers_after_the_rate_changes():
    st = week(("2026-10-14 09:00", "2026-10-14 11:00", []))
    st.weeks[MON.isoformat()] = {"billed_minutes": 120, "rate": 25.0, "invoice_number": 7, "summary": "",
                                 "issued": "2026-10-20", "due": "2026-11-07", "exports": []}
    st.save()
    config.load()
    config.set_value("invoice", "rate", 99)
    r = report.build(Store.load(), config.load(), MON)
    assert r.rate == 25.0 and r.amount == 50


@pytest.mark.parametrize("pattern, want", [
    ("logsweek{start}-{end}.pdf", "logsweek10_12-10_18.pdf"),
    ("{start_iso}_to_{end_iso}", "2026-10-12_to_2026-10-18.pdf"),   # .pdf added when missing
    ("{nope}.pdf", "logsweek10_12-10_18.pdf"),                     # unknown placeholder -> safe default
    ("{start_iso}/{end_iso}.pdf", "2026-10-12-2026-10-18.pdf"),    # no sneaky sub-folders
    ("{name}.pdf", "Your-Name.pdf"),
    ("{", "logsweek10_12-10_18.pdf"),                              # broken braces
])
def test_export_file_names(pattern, want):
    r = report.build(Store.load(), config.load(), MON)
    assert report.export_filename(config.load(), r, pattern) == want


def pages(path):
    return len(pdfium.PdfDocument(str(path)))


def test_pdf_with_a_huge_week_spills_onto_more_pages(tmp_path):
    long = "a very long description of work that keeps going " * 6
    blocks = [(f"2026-10-{d} {h:02d}:00", f"2026-10-{d} {h:02d}:45", [long, "second line"])
              for d in range(12, 19) for h in range(8, 20, 2)]
    st = week(*blocks)
    out = pdf.render(report.build(st, config.load(), MON), config.load(), tmp_path / "big.pdf")
    assert pages(out) >= 3          # several log pages + the invoice


def test_pdf_for_a_tiny_week_is_two_pages(tmp_path):
    st = week(("2026-10-14 09:00", "2026-10-14 09:05", ["quick fix"]))
    out = pdf.render(report.build(st, config.load(), MON), config.load(), tmp_path / "small.pdf", preview=True)
    assert pages(out) == 2
