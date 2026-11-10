"""Estimated stock, only inside Retail OS: StoreHub's stock plus everything on the way, as if every open purchase order and transfer were completed.
estimated = on hand + still to arrive on open purchase orders + transfers not yet completed (added where they go; taken off where they leave, unless already shipped).
StoreHub is never changed."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import StockTransfer
from . import purchasing

OPEN_TRANSFER = ("Created", "Shipped")   # not Completed, not Cancelled


def adjustments(db: Session) -> dict[tuple[int, int], dict]:
    """(location id, product id) -> {"po": still to arrive on open purchase orders, "in": arriving by transfer, "out": leaving by transfer (a shipped one has already left)}."""
    out: dict[tuple[int, int], dict] = {}

    def slot(lid: int, pid: int) -> dict:
        return out.setdefault((lid, pid), {"po": 0.0, "in": 0.0, "out": 0.0})

    for (lid, pid), d in purchasing.open_po_detail(db).items():
        slot(lid, pid)["po"] += d["qty"]
    for t in db.scalars(select(StockTransfer).where(StockTransfer.status.in_(OPEN_TRANSFER)).options(selectinload(StockTransfer.lines))):
        for l in t.lines:
            if l.product_id is None or not l.quantity:
                continue
            if t.target_location_id:
                slot(t.target_location_id, l.product_id)["in"] += l.quantity
            if t.source_location_id and t.status == "Created":      # StoreHub takes the stock off the sender when it ships, not before
                slot(t.source_location_id, l.product_id)["out"] += l.quantity
    return out


def estimate(on_hand: float, a: dict | None) -> float:
    return on_hand + ((a["po"] + a["in"] - a["out"]) if a else 0.0)


def note(a: dict | None) -> str:
    """The pieces, for a tooltip."""
    if not a:
        return ""
    bits = []
    if a["po"]:
        bits.append(f"+{a['po']:g} purchase orders")
    if a["in"]:
        bits.append(f"+{a['in']:g} transfers in")
    if a["out"]:
        bits.append(f"-{a['out']:g} transfers out")
    return " · ".join(bits)
