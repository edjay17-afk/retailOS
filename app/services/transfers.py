"""Warehouse-to-store transfers (proposal flow E): shelf low -> approved -> keyed in StoreHub -> picked and dispatched (FIFO) -> store counts and confirms.

StoreHub keeps the stock. This adds the physical confirmation steps around it, and any difference between what left the warehouse and
what the store counted becomes an issue, so stock does not quietly disappear in transit.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import OrderBatch, Recommendation, User
from ..timeutil import now_utc
from . import issues, notifications


class TransferError(Exception):
    pass


def dispatch(db: Session, user: User, batch: OrderBatch) -> OrderBatch:
    """The warehouse confirms the goods were picked (FIFO, oldest first) and sent."""
    if batch.source_kind != "transfer":
        raise TransferError("Only warehouse transfers are dispatched")
    if batch.status != "entered":
        raise TransferError("Record the transfer in StoreHub first, then dispatch it" if batch.status == "approved" else f"This transfer is already {batch.status}")
    batch.status, batch.dispatched_by, batch.dispatched_at = "dispatched", user.id, now_utc()
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer.dispatch", entity_type="order_batch", entity_id=batch.id, after={"lines": len(batch.lines)})
    notifications.notify(db, role="store_manager", location_id=batch.location_id, title=f"Transfer #{batch.id} is on its way to {batch.location.name}",
                         link="/replenishment/batches", dedupe_key=f"transfer:{batch.id}:dispatched")
    return batch


def receive(db: Session, user: User, batch: OrderBatch, counts: dict[int, float]) -> OrderBatch:
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
    if diffs:
        shown = [f"{l.product.name}: sent {l.approved_qty:g}, counted {l.received_qty:g}" for l in diffs[:12]]
        short = any((l.received_qty or 0) < (l.approved_qty or 0) for l in diffs)
        issues.create_issue(db, type="receiving", severity="P2" if short else "P3", location_id=batch.location_id, source="receiving", created_by=user.id,
                            source_ref=f"transfer:{batch.id}", title=f"Transfer #{batch.id} to {batch.location.name}: {len(diffs)} line(s) differ from what was sent",
                            description="; ".join(shown) + (f" (+{len(diffs) - 12} more)" if len(diffs) > 12 else "") +
                            ". Check the warehouse pick, then correct stock with a stock take or a transfer adjustment in StoreHub.", dedupe_key=f"transfer:{batch.id}")
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer.receive", entity_type="order_batch", entity_id=batch.id, after={"differences": len(diffs)})
    return batch
