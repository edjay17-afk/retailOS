"""Append-only audit log with a hash chain for tamper evidence."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent
from .timeutil import now_utc


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def log(db: Session, *, actor_type: str, actor_id: str | int | None, action: str, entity_type: str,
        entity_id: str | int | None = None, before: Any = None, after: Any = None, reason: str | None = None,
        context: dict | None = None) -> AuditEvent:
    """Write one audit event in the caller's transaction, so it commits or rolls back with the change."""
    prev = db.scalar(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1))
    ts = now_utc().replace(microsecond=0)
    ev = AuditEvent(ts=ts, actor_type=actor_type, actor_id=None if actor_id is None else str(actor_id),
                    action=action, entity_type=entity_type, entity_id=None if entity_id is None else str(entity_id),
                    before=before, after=after, reason=reason, context=context, prev_hash=prev)
    payload = _canon([prev, ts.isoformat(), ev.actor_type, ev.actor_id, action, entity_type, ev.entity_id,
                      before, after, reason, context])
    ev.hash = hashlib.sha256(payload.encode()).hexdigest()
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session) -> tuple[bool, int, int | None]:
    """Return (ok, rows_checked, first_bad_id)."""
    prev = None
    n = 0
    for ev in db.scalars(select(AuditEvent).order_by(AuditEvent.id)):
        payload = _canon([prev, ev.ts.isoformat(), ev.actor_type, ev.actor_id, ev.action, ev.entity_type,
                          ev.entity_id, ev.before, ev.after, ev.reason, ev.context])
        if ev.prev_hash != prev or ev.hash != hashlib.sha256(payload.encode()).hexdigest():
            return False, n, ev.id
        prev = ev.hash
        n += 1
    return True, n, None
