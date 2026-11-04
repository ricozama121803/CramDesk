"""Everything the week views need to know about one week."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .store import Session, Store


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

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)


def build(store: Store, cfg: dict, monday: date, at: datetime | None = None) -> Report:
    days: dict[date, list[Session]] = {}
    for s in store.in_week(monday):
        days.setdefault(s.day, []).append(s)
    blocks = [DayBlock(d, ss, sum(s.minutes(at) for s in ss)) for d, ss in sorted(days.items())]
    return Report(monday, blocks, sum(b.minutes for b in blocks))
