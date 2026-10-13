"""Receiving integrity: PO -> physical count -> compare -> difference becomes an issue -> the next sync verifies StoreHub."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..models import ExpiryBatch, PurchaseOrder, Receiving, ReceivingLine, User
from ..timeutil import now_utc, today_local
from . import issues, purchasing


class ReceivingError(Exception):
    pass


def start(db: Session, user: User, po: PurchaseOrder) -> Receiving:
    open_rec = db.scalar(select(Receiving).where(Receiving.po_id == po.id, Receiving.status == "open"))
    if open_rec:
        return open_rec
    if po.missing_from_api or po.status == "cancelled":
        raise ReceivingError("This PO is cancelled or no longer exists in StoreHub")
    rec = Receiving(po_id=po.id, location_id=po.location_id, created_by=user.id)
    for ln in po.lines:
        rec.lines.append(ReceivingLine(product_id=ln.product_id, ordered_qty=ln.ordered_qty, storehub_received_qty=ln.received_qty))
    db.add(rec)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="receiving.start", entity_type="receiving", entity_id=rec.id,
              after={"po": po.sh_po_id, "lines": len(rec.lines)})
    return rec


def save_counts(db: Session, rec: Receiving, user: User, rows: list[dict]) -> None:
    by_id = {l.id: l for l in rec.lines}
    for r in rows:
        line = by_id.get(r["line_id"])
        if line is None:
            continue
        for k in ("counted", "damaged"):
            if r.get(k) is not None and r[k] < 0:
                raise ReceivingError("Quantities cannot be negative")
        line.counted_qty = r.get("counted")
        line.damaged_qty = r.get("damaged") or 0.0
        line.expiry_date = r.get("expiry")
        line.batch_no = (r.get("batch") or None)
        line.note = (r.get("note") or None)


def complete(db: Session, rec: Receiving, user: User) -> Receiving:
    if rec.status != "open":
        raise ReceivingError("Receiving is already completed")
    missing = [l for l in rec.lines if l.counted_qty is None]
    if missing:
        raise ReceivingError(f"{len(missing)} line(s) have no counted quantity. Enter 0 if nothing arrived.")
    mism = []
    for l in rec.lines:
        short = l.ordered_qty - l.counted_qty
        if abs(short) > 1e-9 or l.damaged_qty > 0:
            mism.append(l)
    rec.completed_at = now_utc()
    rec.status = "mismatch" if mism else "completed"
    for l in rec.lines:
        good = (l.counted_qty or 0) - (l.damaged_qty or 0)
        if l.expiry_date and good > 0 and l.product_id and rec.location_id:
            db.add(ExpiryBatch(location_id=rec.location_id, product_id=l.product_id, qty=good, expiry_date=l.expiry_date,
                               batch_no=l.batch_no, received_date=today_local(), source="receiving"))
    if mism:
        lines = []
        for l in mism[:12]:
            name = l.product.name if l.product else "?"
            lines.append(f"{name}: ordered {l.ordered_qty:g}, counted {l.counted_qty:g}, damaged {l.damaged_qty:g}")
        extra = f" (+{len(mism) - 12} more)" if len(mism) > 12 else ""
        sev = "P1" if any(l.counted_qty == 0 and l.ordered_qty > 0 for l in mism) else "P2"
        issues.create_issue(db, type="receiving", severity=sev, location_id=rec.location_id, source="receiving", source_ref=f"receiving:{rec.id}",
                            created_by=user.id, title=f"Receiving difference on PO #{rec.po.sh_po_id}: {len(mism)} line(s)",
                            description="; ".join(lines) + extra, dedupe_key=f"recv:{rec.id}")
    audit.log(db, actor_type="human", actor_id=user.id, action="receiving.complete", entity_type="receiving", entity_id=rec.id,
              after={"status": rec.status, "mismatched_lines": len(mism)})
    return rec


def verify_with_storehub(db: Session) -> dict:
    """After each sync: refresh StoreHub's received quantity, and flag POs that were counted but not received in StoreHub."""
    flagged = refreshed = 0
    q = select(Receiving).where(Receiving.status.in_(("completed", "mismatch")), Receiving.completed_at >= now_utc() - timedelta(days=5)) \
        .options(selectinload(Receiving.lines), selectinload(Receiving.po).selectinload(PurchaseOrder.lines))
    for rec in db.scalars(q):
        po_lines = {l.product_id: l for l in rec.po.lines}
        for l in rec.lines:
            pl = po_lines.get(l.product_id)
            if pl is not None and abs(pl.received_qty - l.storehub_received_qty) > 1e-9:
                l.storehub_received_qty = pl.received_qty
                refreshed += 1
        age_h = (now_utc() - rec.completed_at).total_seconds() / 3600
        if age_h >= 24 and rec.po.status in purchasing.OPEN_STATUSES and not rec.po.missing_from_api:
            issues.create_issue(db, type="receiving", severity="P2", location_id=rec.location_id, source="receiving", source_ref=f"receiving:{rec.id}",
                                title=f"PO #{rec.po.sh_po_id} was counted but is still open in StoreHub",
                                description="Goods were counted more than 24 hours ago. Receive the PO in the StoreHub back office so stock updates.",
                                dedupe_key=f"recv_open:{rec.id}")
            flagged += 1
        elif rec.po.status == "completed":
            for l in rec.lines:
                good = (l.counted_qty or 0) - (l.damaged_qty or 0)
                if l.product_id in po_lines and abs(po_lines[l.product_id].received_qty - good) > 1e-9 and abs(po_lines[l.product_id].received_qty - (l.counted_qty or 0)) > 1e-9:
                    issues.create_issue(db, type="receiving", severity="P2", location_id=rec.location_id, source="receiving", source_ref=f"receiving:{rec.id}",
                                        title=f"PO #{rec.po.sh_po_id}: StoreHub received quantity differs from the count",
                                        description=f"{l.product.name if l.product else l.product_id}: counted {l.counted_qty:g} (damaged {l.damaged_qty:g}), StoreHub received {po_lines[l.product_id].received_qty:g}",
                                        dedupe_key=f"recv_diff:{rec.id}:{l.product_id}")
                    flagged += 1
    return {"refreshed": refreshed, "flagged": flagged}


def candidates(db: Session, location_ids: list[int]) -> list[PurchaseOrder]:
    """POs that can be received: open or partially received, for the user's locations."""
    q = select(PurchaseOrder).where(PurchaseOrder.status.in_(purchasing.OPEN_STATUSES), PurchaseOrder.missing_from_api.is_(False),
                                    (PurchaseOrder.location_id.in_(location_ids)) | (PurchaseOrder.location_id.is_(None))) \
        .options(selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.location)).order_by(PurchaseOrder.expected_at)
    return list(db.scalars(q))
