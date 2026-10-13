"""V2.1 inventory event trace (spec sections 3 and 4).

An event records what a person saw or did (counted a delivery, dispatched a transfer, counted a shelf...). It never changes the StoreHub
stock number and never becomes a second ledger. Each event starts `pending` and is reconciled only when a later StoreHub state supports it.
Recording is idempotent: the same event_uid is stored once, however many times a job or a page submit repeats it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import InventoryEvent, User
from ..timeutil import now_utc

EVENT_TYPES = ("supplier_receiving", "receiving_variance", "transfer_dispatch", "store_receiving", "transfer_variance", "return", "damage",
               "write_off", "cycle_count", "stock_adjustment", "manual_correction")
STATUSES = ("pending", "matched", "mismatch", "needs_review", "rejected", "cancelled")
UNRESOLVED = ("pending", "mismatch", "needs_review")


class EventError(Exception):
    pass


def record(db: Session, *, uid: str, event_type: str, user: User | None, product_id: int | None, location_id: int | None, quantity: float,
           direction: str = "+", occurred_at: datetime | None = None, reason_code: str | None = None, reason_text: str | None = None,
           evidence_path: str | None = None, source_system: str = "retailos", storehub_reference: str | None = None,
           related_po_id: int | None = None, related_transfer_id: int | None = None, status: str = "pending") -> InventoryEvent:
    """Store one event. If the uid already exists the stored event is returned unchanged (idempotent)."""
    if event_type not in EVENT_TYPES:
        raise EventError(f"Unknown event type {event_type}")
    if direction not in ("+", "-"):
        raise EventError("direction must be + or -")
    if status not in STATUSES:
        raise EventError(f"Unknown reconciliation status {status}")
    existing = db.scalar(select(InventoryEvent).where(InventoryEvent.event_uid == uid))
    if existing is not None:
        return existing
    ev = InventoryEvent(event_uid=uid, event_type=event_type, product_id=product_id, location_id=location_id, quantity=abs(quantity or 0.0), direction=direction,
                        actor_user_id=user.id if user else None, actor_role=user.role if user else None, occurred_at=occurred_at or now_utc(),
                        reason_code=reason_code, reason_text=(reason_text or None) and reason_text[:300], evidence_path=evidence_path,
                        source_system=source_system, storehub_reference=storehub_reference, related_po_id=related_po_id,
                        related_transfer_id=related_transfer_id, reconciliation_status=status)
    db.add(ev)
    db.flush()
    a = audit.log(db, actor_type="human" if user else "system", actor_id=user.id if user else "system", action="inventory_event.record",
                  entity_type="inventory_event", entity_id=ev.id,
                  after={"uid": uid, "type": event_type, "product_id": product_id, "location_id": location_id, "qty": ev.quantity, "dir": direction})
    ev.audit_event_id = a.id
    return ev


def resolve(db: Session, ev: InventoryEvent, status: str, note: str | None = None, actor: User | None = None) -> InventoryEvent:
    """Move an event to a reconciliation status; every move is audited. Resolving to the same status again does nothing."""
    if status not in STATUSES:
        raise EventError(f"Unknown reconciliation status {status}")
    if ev.reconciliation_status == status:
        return ev
    before = ev.reconciliation_status
    ev.reconciliation_status, ev.reconciliation_note = status, (note or None) and note[:300]
    ev.reconciled_at = now_utc() if status in ("matched", "rejected", "cancelled") else None
    audit.log(db, actor_type="human" if actor else "system", actor_id=actor.id if actor else "reconcile", action="inventory_event.reconcile",
              entity_type="inventory_event", entity_id=ev.id, before={"status": before}, after={"status": status}, reason=note)
    return ev


def kpis(db: Session, days: int = 30, location_ids: list[int] | None = None) -> dict:
    """Spec section 9 rates over events recorded in the period. A rate is None while there is nothing to divide."""
    since = now_utc() - timedelta(days=days)
    q = select(InventoryEvent.event_type, InventoryEvent.reconciliation_status, func.count()).where(InventoryEvent.recorded_at >= since)
    if location_ids is not None:
        q = q.where(InventoryEvent.location_id.in_(location_ids or [0]))
    counts: dict[tuple[str, str], int] = {}
    for t, st, n in db.execute(q.group_by(InventoryEvent.event_type, InventoryEvent.reconciliation_status)):
        counts[(t, st)] = n

    def total(types, statuses=None) -> int:
        return sum(n for (t, st), n in counts.items() if t in types and (statuses is None or st in statuses))

    def rate(a, b):
        return (a / b) if b else None
    rcv = total(("supplier_receiving",))
    trf = total(("transfer_dispatch",))
    allv = total(EVENT_TYPES)
    return {"days": days, "events": allv,
            "receiving_events": rcv, "receiving_reconciled_rate": rate(total(("supplier_receiving",), ("matched",)), rcv),
            "receiving_variance_rate": rate(total(("receiving_variance",)), rcv),
            "unresolved": total(EVENT_TYPES, UNRESOLVED), "mismatch": total(EVENT_TYPES, ("mismatch",)),
            "transfer_events": trf}
