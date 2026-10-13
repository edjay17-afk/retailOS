"""Warehouse-to-store transfers (proposal flow E): shelf low -> approved -> keyed in StoreHub -> picked and dispatched (FIFO) -> store counts and confirms.

StoreHub keeps the stock. This adds the physical confirmation steps around it, and any difference between what left the warehouse and
what the store counted becomes an issue, so stock does not quietly disappear in transit.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Location, OrderBatch, Recommendation, User
from ..timeutil import now_utc
from . import inventory_events as ev, issues, notifications


class TransferError(Exception):
    pass


def dispatch(db: Session, user: User, batch: OrderBatch) -> OrderBatch:
    """The warehouse confirms the goods were picked (FIFO, oldest first) and sent."""
    if batch.source_kind != "transfer":
        raise TransferError("Only warehouse transfers are dispatched")
    if batch.status != "entered":
        raise TransferError("Record the transfer in StoreHub first, then dispatch it" if batch.status == "approved" else f"This transfer is already {batch.status}")
    batch.status, batch.dispatched_by, batch.dispatched_at = "dispatched", user.id, now_utc()
    wh = db.scalar(select(Location.id).where(Location.type == "warehouse").order_by(Location.id).limit(1))
    for l in db.scalars(select(Recommendation).where(Recommendation.batch_id == batch.id)):
        ev.record(db, uid=f"trfdsp:{l.id}", event_type="transfer_dispatch", user=user, product_id=l.product_id, location_id=wh or batch.location_id,
                  quantity=l.approved_qty or 0.0, direction="-", occurred_at=batch.dispatched_at, related_transfer_id=batch.id,
                  reason_code="dispatch", reason_text=f"to {batch.location.name}")
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer.dispatch", entity_type="order_batch", entity_id=batch.id, after={"lines": len(batch.lines)})
    notifications.notify(db, role="store_manager", location_id=batch.location_id, title=f"Transfer #{batch.id} is on its way to {batch.location.name}",
                         link="/replenishment/batches", dedupe_key=f"transfer:{batch.id}:dispatched")
    return batch


def receive(db: Session, user: User, batch: OrderBatch, counts: dict[int, float], note: str | None = None) -> OrderBatch:
    """The store counts what arrived. Differences open an issue; the batch is closed either way."""
    if batch.source_kind != "transfer":
        raise TransferError("Only warehouse transfers are received this way")
    if batch.status != "dispatched":
        raise TransferError("The warehouse has not dispatched this transfer yet" if batch.status in ("approved", "entered") else f"This transfer is already {batch.status}")
    lines = list(db.scalars(select(Recommendation).where(Recommendation.batch_id == batch.id)))
    missing = [l for l in lines if l.id not in counts or counts[l.id] is None]
    if missing:
        raise TransferError(f"Enter the counted quantity for every line ({len(missing)} missing). Enter 0 if nothing arrived.")
    diffs = []
    for l in lines:
        q = counts[l.id]
        if q < 0:
            raise TransferError("A count cannot be negative")
        l.received_qty = q
        if abs(q - (l.approved_qty or 0)) > 1e-9:
            diffs.append(l)
    batch.status, batch.received_by, batch.received_at = "received", user.id, now_utc()
    _record_store_events(db, user, batch, lines, note)
    if diffs:
        shown = [f"{l.product.name}: sent {l.approved_qty:g}, counted {l.received_qty:g}" for l in diffs[:12]]
        short = any((l.received_qty or 0) < (l.approved_qty or 0) for l in diffs)
        issues.create_issue(db, type="receiving", severity="P2" if short else "P3", location_id=batch.location_id, source="receiving", created_by=user.id,
                            source_ref=f"transfer:{batch.id}", title=f"Transfer #{batch.id} to {batch.location.name}: {len(diffs)} line(s) differ from what was sent",
                            description="; ".join(shown) + (f" (+{len(diffs) - 12} more)" if len(diffs) > 12 else "") +
                            ". Check the warehouse pick, then correct stock with a stock take or a transfer adjustment in StoreHub.", dedupe_key=f"transfer:{batch.id}")
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer.receive", entity_type="order_batch", entity_id=batch.id, after={"differences": len(diffs)})
    return batch


def _record_store_events(db: Session, user: User, batch: OrderBatch, lines: list[Recommendation], note: str | None) -> None:
    """The store count is the confirmation (spec MUST 2). Each line leaves a store_receiving event; a difference also leaves a transfer_variance event.
    The dispatch event of the line is settled here: matched when the store counted exactly what left, mismatch otherwise."""
    for l in lines:
        sent, got = l.approved_qty or 0.0, l.received_qty or 0.0
        ev.record(db, uid=f"trfrcv:{l.id}", event_type="store_receiving", user=user, product_id=l.product_id, location_id=batch.location_id, quantity=got,
                  direction="+", occurred_at=batch.received_at, related_transfer_id=batch.id, reason_code="store_count", reason_text=note)
        same = abs(sent - got) < 1e-9
        if not same:
            ev.record(db, uid=f"trfvar:{l.id}", event_type="transfer_variance", user=user, product_id=l.product_id, location_id=batch.location_id,
                      quantity=abs(sent - got), direction="-" if got < sent else "+", occurred_at=batch.received_at, related_transfer_id=batch.id,
                      reason_code="short" if got < sent else "over", reason_text=note, status="mismatch")
        verdict, why = ("matched", "the store counted what was dispatched") if same else ("mismatch", f"dispatched {sent:g}, store counted {got:g}")
        for uid in (f"trfdsp:{l.id}", f"trfrcv:{l.id}"):
            e = db.scalar(select(ev.InventoryEvent).where(ev.InventoryEvent.event_uid == uid))
            if e is not None:
                ev.resolve(db, e, verdict, why)
