"""Working out (and fixing) blocks whose times overlap."""
from __future__ import annotations

from datetime import datetime

from .store import Session, Store

FAR = datetime.max


def _end(s: Session) -> datetime:
    return s.end or FAR


def overlapping(store: Store, st: datetime, en: datetime | None, exclude: int | None) -> list[Session]:
    en = en or FAR
    return sorted((s for s in store.sessions if s.id != exclude and st < _end(s) and s.start < en),
                  key=lambda s: s.start)


def free_slot(store: Store, st: datetime, en: datetime, exclude: int | None) -> tuple[datetime, datetime] | None:
    """The longest stretch inside st..en that no other block covers."""
    best, cur = None, st
    for o in overlapping(store, st, en, exclude):
        if o.start > cur and (best is None or o.start - cur > best[1] - best[0]):
            best = (cur, o.start)
        cur = max(cur, _end(o))
    if cur < en and (best is None or en - cur > best[1] - best[0]):
        best = (cur, en)
    return best if best and best[1] > best[0] else None


def options(store: Store, st: datetime, en: datetime, exclude: int | None) -> dict[str, str]:
    """Which fixes make sense here → a short description of what each would do."""
    from .timeutil import fmt_range

    others = overlapping(store, st, en, exclude)
    opts: dict[str, str] = {}
    lo = min([st] + [o.start for o in others])
    running = any(o.running for o in others)
    hi = None if running else max([en] + [_end(o) for o in others])
    opts["merge"] = f"→ {fmt_range(lo, hi)}, items combined"
    slot = free_slot(store, st, en, exclude)
    if slot:
        opts["trim"] = f"→ this one {fmt_range(*slot)}"
    inside = [o for o in others if o.start >= st and _end(o) <= en]
    around = [o for o in others if o.start < st and _end(o) > en]
    if not inside and not around:
        if len(others) == 1:
            o = others[0]
            new = (o.start, st) if o.start < st else (en, o.end)
            opts["shorten"] = f"→ other one {fmt_range(*new)}"
        else:
            opts["shorten"] = "→ the others trimmed to fit"
    if not running:
        opts["replace"] = "→ delete the other block" + ("s" if len(others) > 1 else "")
    return opts


def apply(store: Store, how: str, st: datetime, en: datetime, notes: list[str], sid: int | None) -> int:
    """Fix the overlap and save this block. Returns the id of the block that holds the result."""
    others = overlapping(store, st, en, sid)
    me = store.get(sid) if sid else None

    if how == "merge":
        group = sorted(([me] if me else []) + others, key=lambda s: s.start)
        target = me or group[0]
        lo = min([st] + [o.start for o in others])
        hi = None if any(o.running for o in others) else max([en] + [_end(o) for o in others])
        merged: list[str] = []
        timeline = sorted([(o.start, o.notes) for o in others] + [(st, notes)], key=lambda x: x[0])
        for _, ns in timeline:
            merged += [n for n in ns if n not in merged]
        target.start, target.end, target.notes = lo, hi, merged
        for o in others:
            if o.id != target.id:
                store.remove(o.id)
        store.save()
        return target.id

    if how == "trim":
        slot = free_slot(store, st, en, sid)
        st, en = slot
    elif how == "shorten":
        for o in others:
            if o.start < st:
                o.end = st
            else:
                o.start = en
    elif how == "replace":
        for o in others:
            store.remove(o.id)

    if me:
        me.start, me.end, me.notes = st, en, list(notes)
        rid = me.id
    else:
        rid = store.add(st, en, list(notes)).id
    store.save()
    return rid
