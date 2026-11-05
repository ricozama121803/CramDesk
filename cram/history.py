"""Undo / redo for the study log.

Every save records the file before and after. Undo puts the "before" back, redo the "after".
Each entry gets a plain-English label worked out from what actually changed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from .paths import data_dir, data_file
from .timeutil import fmt_md, fmt_range, fmt_time

LIMIT = 100


def _dir(kind: str):
    d = data_dir() / kind
    d.mkdir(exist_ok=True)
    return d


def _entries(kind: str):
    return sorted(_dir(kind).glob("*.json"))


def _write_atomic(path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _push(kind: str, before: str | None, after: str, label: str) -> None:
    entries = _entries(kind)
    n = int(entries[-1].stem) + 1 if entries else 1
    _write_atomic(_dir(kind) / f"{n:06d}.json",
                  json.dumps({"before": before, "after": after, "label": label,
                              "at": datetime.now().isoformat(timespec="seconds")}))
    for old in _entries(kind)[:-LIMIT]:
        old.unlink(missing_ok=True)


def record(before: str | None, after: str) -> None:
    """Called by Store.save for every normal change."""
    _push("undo", before, after, describe(before, after))
    for e in _entries("redo"):  # a fresh change ends the redo chain
        e.unlink(missing_ok=True)


def _restore(text: str | None) -> None:
    path = data_file()
    if text is None:
        path.unlink(missing_ok=True)
    else:
        _write_atomic(path, text)


def _step(src: str, dst: str, field: str) -> str | None:
    entries = _entries(src)
    if not entries:
        return None
    e = json.loads(entries[-1].read_text())
    _restore(e[field])
    entries[-1].unlink()
    _push(dst, e["before"], e["after"], e["label"])
    return e["label"]


def undo() -> str | None:
    """Undo the last change. Returns what was undone, or None if there's nothing."""
    return _step("undo", "redo", "before")


def redo() -> str | None:
    return _step("redo", "undo", "after")


def peek(kind: str = "undo") -> str | None:
    entries = _entries(kind)
    return json.loads(entries[-1].read_text())["label"] if entries else None


# ── describing a change ──────────────────────────────────────────────────────

def _sessions(text: str | None) -> dict[int, dict]:
    if not text:
        return {}
    return {s["id"]: s for s in json.loads(text).get("sessions", [])}


def _block(s: dict) -> str:
    st = datetime.fromisoformat(s["start"])
    en = datetime.fromisoformat(s["end"]) if s.get("end") else None
    return f"{fmt_md(st.date())} {fmt_range(st, en)}"


def _short(t: str, n: int = 28) -> str:
    return t if len(t) <= n else t[: n - 1] + "…"


def describe(before: str | None, after: str | None) -> str:
    a, b = _sessions(before), _sessions(after)
    changes: list[str] = []
    for sid in b.keys() - a.keys():
        s = b[sid]
        changes.append(f"start studying at {fmt_time(datetime.fromisoformat(s['start']))}" if not s.get("end")
                       else f"add session {_block(s)}")
    for sid in a.keys() - b.keys():
        changes.append(f"delete session {_block(a[sid])}")
    for sid in a.keys() & b.keys():
        old, new = a[sid], b[sid]
        if old.get("end") is None and new.get("end"):
            changes.append(f"stop ({_block(new)})")
        elif (old["start"], old.get("end")) != (new["start"], new.get("end")):
            changes.append(f"change times to {_block(new)}")
        if old.get("class") != new.get("class"):
            changes.append(f"set class to {new.get('class') or 'none'} on {_block(new)}")
        on, nn = old.get("notes", []), new.get("notes", [])
        if on != nn:
            added = [x for x in nn if x not in on]
            removed = [x for x in on if x not in nn]
            if added and not removed:
                changes.append(f"add “{_short(added[0])}”" + (f" +{len(added) - 1}" if len(added) > 1 else ""))
            elif removed and not added:
                changes.append(f"remove “{_short(removed[0])}”")
            else:
                changes.append(f"edit description on {_block(new)}")
    ta = {t["id"]: t for t in (json.loads(before).get("todos", []) if before else [])}
    tb = {t["id"]: t for t in (json.loads(after).get("todos", []) if after else [])}
    for tid in tb.keys() - ta.keys():
        changes.append(f"add memo “{_short(tb[tid]['text'])}”")
    for tid in ta.keys() - tb.keys():
        changes.append(f"remove memo “{_short(ta[tid]['text'])}”")
    for tid in ta.keys() & tb.keys():
        if ta[tid].get("done") != tb[tid].get("done"):
            changes.append(("check off" if tb[tid].get("done") else "uncheck") + f" “{_short(tb[tid]['text'])}”")
        elif ta[tid] != tb[tid]:
            changes.append(f"edit memo “{_short(tb[tid]['text'])}”")
    na = {n["id"]: n for n in (json.loads(before).get("log", []) if before else [])}
    nb = {n["id"]: n for n in (json.loads(after).get("log", []) if after else [])}
    for nid in nb.keys() - na.keys():
        changes.append(f"log for {nb[nid]['class']} “{_short(nb[nid]['text'])}”")
    for nid in na.keys() - nb.keys():
        changes.append(f"remove {na[nid]['class']} log entry “{_short(na[nid]['text'])}”")
    ca = json.loads(before).get("classes", []) if before else []
    cb = json.loads(after).get("classes", []) if after else []
    added, gone = [c for c in cb if c not in ca], [c for c in ca if c not in cb]
    if len(added) == 1 and len(gone) == 1:
        changes.append(f"rename class {gone[0]} to {added[0]}")
    else:
        changes += [f"add class {c}" for c in added] + [f"remove class {c}" for c in gone]
    if not changes:
        return "change"
    return changes[0] + (f" (+{len(changes) - 1} more)" if len(changes) > 1 else "")
