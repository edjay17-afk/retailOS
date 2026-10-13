"""Purchase order logic: which POs genuinely count as incoming stock."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import settings_service as cfg
from ..models import PurchaseOrder
from ..timeutil import now_utc

OPEN_STATUSES = ("open", "partiallyReceived")


def po_reference_date(po: PurchaseOrder) -> datetime | None:
    return po.expected_at or po.created_sh


def classify_po(po: PurchaseOrder, stale_days: int, now: datetime | None = None) -> str:
    """incoming | stale | closed | deleted"""
    now = now or now_utc()
    if po.missing_from_api:
        return "deleted"
    if po.status not in OPEN_STATUSES:
        return "closed"
    ref = po_reference_date(po)
    if ref is None or ref + timedelta(days=stale_days) < now:
        return "stale"
    return "incoming"


def open_po_lines_remaining(po: PurchaseOrder) -> list[tuple[int | None, float]]:
    """(product_id, qty still to arrive). StoreHub omits receivedQuantity when nothing has been received (treated as 0)."""
    return [(ln.product_id, max(0.0, ln.ordered_qty - ln.received_qty)) for ln in po.lines]


def incoming_map(db: Session) -> tuple[dict[tuple[int, int], float], list[PurchaseOrder]]:
    """Incoming quantity per (location, product), counting only non-stale open POs. Also returns the stale POs."""
    stale_days = int(cfg.get(db, "po.stale_days"))
    now = now_utc()
    inc: dict[tuple[int, int], float] = {}
    stale: list[PurchaseOrder] = []
    q = select(PurchaseOrder).where(PurchaseOrder.status.in_(OPEN_STATUSES), PurchaseOrder.missing_from_api.is_(False)) \
        .options(selectinload(PurchaseOrder.lines))
    for po in db.scalars(q):
        kind = classify_po(po, stale_days, now)
        if kind == "stale":
            stale.append(po)
            continue
        if kind != "incoming" or po.location_id is None:
            continue
        for pid, rem in open_po_lines_remaining(po):
            if pid is not None and rem > 0:
                inc[(po.location_id, pid)] = inc.get((po.location_id, pid), 0.0) + rem
    return inc, stale


def open_po_detail(db: Session) -> dict[tuple[int, int], dict]:
    """Everything still to arrive per (location, product) from POs that are not completed or cancelled, for display.
    Unlike incoming_map (used by replenishment) this also includes overdue POs, flagged as late, so nothing open is hidden."""
    stale_days = int(cfg.get(db, "po.stale_days"))
    now = now_utc()
    out: dict[tuple[int, int], dict] = {}
    q = select(PurchaseOrder).where(PurchaseOrder.status.in_(OPEN_STATUSES), PurchaseOrder.missing_from_api.is_(False)).options(selectinload(PurchaseOrder.lines))
    for po in db.scalars(q):
        if po.location_id is None:
            continue
        late = classify_po(po, stale_days, now) == "stale"
        ref = po_reference_date(po)
        for pid, rem in open_po_lines_remaining(po):
            if pid is None or rem <= 0:
                continue
            d = out.setdefault((po.location_id, pid), {"qty": 0.0, "late_qty": 0.0, "pos": []})
            d["qty"] += rem
            if late:
                d["late_qty"] += rem
            d["pos"].append({"no": po.sh_po_id, "status": po.status, "qty": rem, "late": late, "expected": ref,
                             "days_late": max((now - ref).days, 0) if ref else None})
    return out
