"""Transient progress notes from automation (the n8n image flow, robots), shown on /audit/live.

In-memory ring buffer only — these are heartbeats, not audit data, so they are never written to the database.
"""
from collections import deque

from ..timeutil import now_utc

_notes: deque = deque(maxlen=200)
_seq = 0


def note(msg: str, source: str = "n8n") -> int:
    global _seq
    _seq += 1
    _notes.append({"id": _seq, "ts": str(now_utc()), "source": source, "msg": msg[:300]})
    return _seq


def feed(limit: int = 50) -> list[dict]:
    return list(_notes)[-limit:]
