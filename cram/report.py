"""Everything the PDF (and the review screens) need to know about one week."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .store import Session, Store
from .timeutil import add_business_days, billed_minutes, today


@dataclass
class DayBlock:
    day: date
    sessions: list[Session]
    minutes: int


@dataclass
class Report:
    monday: date
    days: list[DayBlock]
    total_minutes: int
    billed_minutes: int
    approved: bool
    invoice_number: int
    issued: date
    due: date
    summary: str
    rate: float

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def amount(self) -> float:
        return round(self.rate * self.billed_minutes / 60, 2)

    @property
    def first_day(self) -> date:
        return self.days[0].day if self.days else self.monday

    @property
    def last_day(self) -> date:
        """End of the date range on the log page (the example uses Sunday)."""
        return max(self.sunday, self.days[-1].day) if self.days else self.sunday


def build(store: Store, cfg: dict, monday: date, at: datetime | None = None) -> Report:
    inv = cfg["invoice"]
    sessions = store.in_week(monday)
    days: dict[date, list[Session]] = {}
    for s in sessions:
        days.setdefault(s.day, []).append(s)
    blocks = [DayBlock(d, ss, sum(s.minutes(at) for s in ss)) for d, ss in sorted(days.items())]
    total = sum(b.minutes for b in blocks)

    rec = store.approval(monday)
    if rec:
        return Report(
            monday, blocks, total,
            billed_minutes=rec["billed_minutes"],
            approved=True,
            invoice_number=rec["invoice_number"],
            issued=date.fromisoformat(rec["issued"]),
            due=date.fromisoformat(rec["due"]),
            summary=rec.get("summary", ""),
            rate=rec["rate"],
        )
    issued = today()
    return Report(
        monday, blocks, total,
        billed_minutes=billed_minutes(total, inv["billing"]),
        approved=False,
        invoice_number=store.next_invoice_number(int(inv["first_number"])),
        issued=issued,
        due=add_business_days(issued, int(inv["due_business_days"])),
        summary=store.last_summary(),
        rate=float(inv["rate"]),
    )


# ── export file names ────────────────────────────────────────────────────────

NAMING_PRESETS = [
    "logsweek{start}-{end}.pdf",
    "{start_iso}_to_{end_iso}.pdf",
    "invoice-{number}_{start_iso}.pdf",
    "{name}_week_{start}-{end}.pdf",
]


def export_filename(cfg: dict, r: Report, pattern: str | None = None) -> str:
    """Fill in a naming pattern. Placeholders: {start} {end} (10_12), {start_iso} {end_iso}
    (2026-10-12), {number} (invoice #), {name} (your name), {year}."""
    import re
    name = re.sub(r"[^A-Za-z0-9]+", "-", cfg["you"].get("name", "")).strip("-") or "me"
    values = {
        "start": f"{r.monday.month}_{r.monday.day}", "end": f"{r.sunday.month}_{r.sunday.day}",
        "start_iso": r.monday.isoformat(), "end_iso": r.sunday.isoformat(),
        "number": r.invoice_number, "name": name, "year": r.monday.year,
    }
    try:
        out = (pattern or cfg["export"]["filename"]).format(**values)
    except (KeyError, IndexError, ValueError):
        out = NAMING_PRESETS[0].format(**values)
    out = out.replace("/", "-")
    return out if out.lower().endswith(".pdf") else out + ".pdf"


def export_path(cfg: dict, r: Report):
    from pathlib import Path
    return Path(cfg["export"]["dir"]).expanduser() / export_filename(cfg, r)
