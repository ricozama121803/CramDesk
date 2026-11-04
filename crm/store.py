"""Sessions + approved weeks, kept in one JSON file."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime

from .paths import data_file
from .timeutil import week_start


@dataclass
class Session:
    id: int
    start: datetime
    end: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.end is None

    @property
    def day(self) -> date:
        return self.start.date()

    @property
    def week(self) -> date:
        return week_start(self.day)

    def seconds(self, at: datetime | None = None) -> int:
        end = self.end or at or datetime.now()
        return max(0, int((end - self.start).total_seconds()))

    def minutes(self, at: datetime | None = None) -> int:
        return self.seconds(at) // 60

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": self.start.isoformat(timespec="minutes"),
            "end": self.end.isoformat(timespec="minutes") if self.end else None,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d["id"],
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]) if d.get("end") else None,
            notes=list(d.get("notes") or []),
        )


class DataError(Exception):
    """The data file can't be read."""


class Store:
    def __init__(self, sessions: list[Session], weeks: dict[str, dict], next_id: int):
        self.sessions = sessions
        self.weeks = weeks  # "YYYY-MM-DD" (monday) -> approval record
        self.next_id = next_id

    # ── io ──
    @classmethod
    def load(cls) -> "Store":
        path = data_file()
        if not path.exists():
            return cls([], {}, 1)
        try:
            raw = json.loads(path.read_text())
            sessions = [Session.from_dict(s) for s in raw.get("sessions", [])]
        except (ValueError, KeyError, TypeError) as e:
            # a hand-edited or half-written file: fall back to the last good copy
            bak = path.with_suffix(".json.bak")
            try:
                raw = json.loads(bak.read_text())
                sessions = [Session.from_dict(s) for s in raw.get("sessions", [])]
            except (OSError, ValueError, KeyError, TypeError):
                raise DataError(f"{path} is damaged and there's no usable backup ({e}).") from e
            broken = path.with_suffix(".json.broken")
            path.replace(broken)
            bak.replace(path)
            import sys
            print(f"crm: {path.name} was damaged, restored the backup (damaged copy kept as {broken.name})",
                  file=sys.stderr)
        next_id = raw.get("next_id") or (max((s.id for s in sessions), default=0) + 1)
        return cls(sessions, raw.get("weeks", {}), next_id)

    def save(self, record: bool = True) -> None:
        path = data_file()
        payload = {
            "version": 1,
            "next_id": self.next_id,
            "sessions": [s.to_dict() for s in sorted(self.sessions, key=lambda s: s.start)],
            "weeks": self.weeks,
        }
        text = json.dumps(payload, indent=2)
        before = path.read_text() if path.exists() else None
        if before == text:
            return
        if path.exists():
            shutil.copy2(path, path.with_suffix(".json.bak"))
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".data.", suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
        if record:
            from . import history
            history.record(before, text)

    # ── sessions ──
    def running(self) -> Session | None:
        return next((s for s in self.sessions if s.running), None)

    def get(self, sid: int) -> Session | None:
        return next((s for s in self.sessions if s.id == sid), None)

    def last(self) -> Session | None:
        return max(self.sessions, key=lambda s: s.start, default=None)

    def in_week(self, monday: date) -> list[Session]:
        return sorted((s for s in self.sessions if s.week == monday), key=lambda s: s.start)

    def on_day(self, d: date) -> list[Session]:
        return sorted((s for s in self.sessions if s.day == d), key=lambda s: s.start)

    def add(self, start: datetime, end: datetime | None, notes: list[str]) -> Session:
        s = Session(self.next_id, start, end, notes)
        self.next_id += 1
        self.sessions.append(s)
        return s

    def remove(self, sid: int) -> None:
        self.sessions = [s for s in self.sessions if s.id != sid]

    def overlap(self, start: datetime, end: datetime | None, exclude: int | None = None) -> Session | None:
        end = end or datetime.max
        for s in self.sessions:
            if s.id == exclude:
                continue
            s_end = s.end or datetime.max
            if start < s_end and s.start < end:
                return s
        return None

    # ── weeks ──
    def approval(self, monday: date) -> dict | None:
        return self.weeks.get(monday.isoformat())

    def is_approved(self, monday: date) -> bool:
        return monday.isoformat() in self.weeks

    def next_invoice_number(self, first: int) -> int:
        used = [w["invoice_number"] for w in self.weeks.values() if "invoice_number" in w]
        return max(used) + 1 if used else first

    def last_summary(self) -> str:
        if not self.weeks:
            return ""
        return self.weeks[max(self.weeks)].get("summary", "")
