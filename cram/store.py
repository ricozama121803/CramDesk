"""Study sessions, kept in one JSON file."""
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
    course: str | None = None  # the class this session was for

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
            "class": self.course,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d["id"],
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]) if d.get("end") else None,
            notes=list(d.get("notes") or []),
            course=d.get("class") or None,
        )


@dataclass
class Todo:
    """A note to self for a week. No reminders: just something to do later."""
    id: int
    text: str
    week: date                      # the Monday of the week it's for
    done: bool = False
    course: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "week": self.week.isoformat(), "done": self.done,
                "class": self.course}

    @classmethod
    def from_dict(cls, d: dict) -> "Todo":
        return cls(id=d["id"], text=d["text"], week=date.fromisoformat(d["week"]),
                   done=bool(d.get("done")), course=d.get("class") or None)


@dataclass
class Note:
    """A log entry for a class: what you did, what's still shaky, anything."""
    id: int
    course: str
    text: str
    at: datetime

    def to_dict(self) -> dict:
        return {"id": self.id, "class": self.course, "text": self.text, "at": self.at.isoformat(timespec="minutes")}

    @classmethod
    def from_dict(cls, d: dict) -> "Note":
        return cls(id=d["id"], course=d["class"], text=d["text"], at=datetime.fromisoformat(d["at"]))


class DataError(Exception):
    """The data file can't be read."""


class Store:
    def __init__(self, sessions: list[Session], next_id: int, classes: list[str] | None = None,
                 todos: list[Todo] | None = None, notes: list[Note] | None = None):
        self.sessions = sessions
        self.next_id = next_id
        self.classes: list[str] = classes or []
        self.todos: list[Todo] = todos or []    # the memo board
        self.notes: list[Note] = notes or []    # the activity log, per class

    # ── io ──
    @classmethod
    def load(cls) -> "Store":
        path = data_file()
        if not path.exists():
            return cls([], 1)
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
            print(f"cram: {path.name} was damaged, restored the backup (damaged copy kept as {broken.name})",
                  file=sys.stderr)
        next_id = raw.get("next_id") or (max((s.id for s in sessions), default=0) + 1)
        try:
            todos = [Todo.from_dict(t) for t in raw.get("todos", [])]
        except (ValueError, KeyError, TypeError):
            todos = []
        try:
            notes = [Note.from_dict(n) for n in raw.get("log", raw.get("notes", []))]
        except (ValueError, KeyError, TypeError):
            notes = []
        return cls(sessions, next_id, [c for c in raw.get("classes", []) if isinstance(c, str)], todos, notes)

    def save(self, record: bool = True) -> None:
        path = data_file()
        payload = {
            "version": 1,
            "next_id": self.next_id,
            "classes": self.classes,
            "todos": [t.to_dict() for t in self.todos],
            "log": [n.to_dict() for n in self.notes],
            "sessions": [s.to_dict() for s in sorted(self.sessions, key=lambda s: s.start)],
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

    def add(self, start: datetime, end: datetime | None, notes: list[str], course: str | None = None) -> Session:
        s = Session(self.next_id, start, end, notes, course)
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

    # ── todos ──
    def split_class_tag(self, text: str) -> tuple[str | None, str]:
        """'statics: review slides' -> ('Statics', 'review slides'). Without a 'class:' in front -> (None, text)."""
        head, colon, rest = text.partition(":")
        if colon and head.strip() and rest.strip():
            course, left = self.match_class(head)
            if course and not left:
                return course, rest.strip()
        return None, text

    def add_todo(self, text: str, week: date, course: str | None = None) -> Todo:
        """A memo. Tag it with a class by passing course, or by starting the text with 'class:'."""
        if course is None:
            course, text = self.split_class_tag(text)
        text = " ".join(text.split())
        if not text:
            raise ValueError("a memo needs some text")
        t = Todo(max((x.id for x in self.todos), default=0) + 1, text, week_start(week), course=course)
        self.todos.append(t)
        return t

    def get_todo(self, tid: int) -> Todo | None:
        return next((t for t in self.todos if t.id == tid), None)

    def remove_todo(self, tid: int) -> None:
        self.todos = [t for t in self.todos if t.id != tid]

    def todos_for(self, monday: date) -> tuple[list[Todo], list[Todo]]:
        """(this week's to-dos, unfinished ones from earlier weeks that are still open)."""
        mine = [t for t in self.todos if t.week == monday]
        older = [t for t in self.todos if t.week < monday and not t.done]
        key = lambda t: (t.done, t.id)
        return sorted(mine, key=key), sorted(older, key=lambda t: (t.week, t.id))

    # ── class notes ──
    def add_note(self, course: str, text: str, at: datetime | None = None) -> Note:
        text = " ".join(text.split())
        if not text:
            raise ValueError("a log entry needs some text")
        n = Note(max((x.id for x in self.notes), default=0) + 1, course, text,
                 (at or datetime.now()).replace(second=0, microsecond=0))
        self.notes.append(n)
        return n

    def notes_for(self, course: str | None = None) -> list[Note]:
        """Newest first; all classes when course is None."""
        return sorted((n for n in self.notes if course is None or n.course == course), key=lambda n: (n.at, n.id),
                      reverse=True)

    def remove_note(self, nid: int) -> None:
        self.notes = [n for n in self.notes if n.id != nid]

    # ── classes ──
    def find_class(self, name: str) -> str | None:
        """The saved spelling of a class, matched without caring about case or extra spaces."""
        key = " ".join(name.split()).lower()
        return next((c for c in self.classes if c.lower() == key), None)

    def add_class(self, name: str) -> str:
        """Add a class (or return the existing one with that name)."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("a class needs a name")
        found = self.find_class(name)
        if found:
            return found
        self.classes.append(name)
        return name

    def remove_class(self, name: str) -> str | None:
        """Take a class off your list. Past sessions keep their class so the hours aren't lost."""
        found = self.find_class(name)
        if found:
            self.classes.remove(found)
        return found

    def rename_class(self, old: str, new: str) -> str:
        found = self.find_class(old)
        if not found:
            raise ValueError(f"no class called '{old}'")
        new = " ".join(new.split())
        other = self.find_class(new)
        if not new or (other and other != found):
            raise ValueError(f"'{new}' is already a class" if new else "a class needs a name")
        self.classes[self.classes.index(found)] = new
        for s in self.sessions:
            if s.course == found:
                s.course = new
        for n in self.notes:
            if n.course == found:
                n.course = new
        return new

    def match_class(self, text: str) -> tuple[str | None, str]:
        """Split typed text into (class, the rest).

        'linear algebra eigenvectors' -> ('Linear Algebra', 'eigenvectors') when that class exists.
        A short form works too when it fits only one class: the start of the name ('linear', 'chem')
        or its initials ('la'). Anything else -> (None, text)."""
        return match_class(self.classes, text)


def _initials(name: str) -> str:
    return "".join(w[0] for w in name.split()).lower()


def match_class(classes: list[str], text: str) -> tuple[str | None, str]:
    words = text.split()
    if not words or not classes:
        return None, " ".join(words)
    low = [w.lower() for w in words]
    # 1. the full class name at the start (the longest one wins: "Chem Lab" over "Chem")
    best = None
    for c in classes:
        cw = c.lower().split()
        if low[: len(cw)] == cw and (best is None or len(cw) > len(best.split())):
            best = c
    if best:
        return best, " ".join(words[len(best.split()):])
    # 2. a short form of one class in the first word
    first = low[0]
    hits = [c for c in classes
            if (len(first) >= 3 and c.lower().startswith(first))
            or (len(c.split()) > 1 and _initials(c) == first)]
    if len(hits) == 1:
        return hits[0], " ".join(words[1:])
    return None, " ".join(words)
