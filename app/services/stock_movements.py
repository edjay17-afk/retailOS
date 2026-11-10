"""Stock changes as StoreHub's back-office Audit Trail records them: every change to a product's quantity in a store, with who and what (Sale, receiving, stock take ...).

StoreHub's API only gives the quantity on hand. Its back-office Audit Trail has the history, one product and store at a time, so the robot asks only for the
products whose quantity was seen to change since they were last read. It only looks (GET requests); nothing is ever written to StoreHub."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import InventoryChange, LarkLink, Location, Product, Setting, StockMovement, StockMovementRead, StockTransfer, StockTransferLine, User, WastageEntry
from ..timeutil import now_utc, to_local, today_local

log = logging.getLogger("retailos.stock_movements")
EVERY_MINUTES = 30       # when nothing is waiting
CATCH_UP_MINUTES = 5     # while products with newer changes are still waiting
RETRY_MINUTES = 15
PER_RUN = 120            # products asked for in one run (every product on a recent stock transfer is read first)
FIRST_DAYS = 35          # how far back the first read of a product goes


def _num(text) -> float:
    try:
        return float(str(text).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _when(row: dict) -> datetime | None:
    for text, fmt in ((row.get("exportTimeText") or row.get("exportTime"), "%Y-%m-%d %H:%M"), (row.get("0"), "%b %d, %Y, %I:%M %p")):
        try:
            return datetime.strptime(str(text or "").strip(), fmt)
        except ValueError:
            continue
    return None


def pending(db: Session, limit: int | None = None) -> list[tuple[int, int, str, str, datetime | None]]:
    """Products (with their StoreHub ids) whose quantity changed after they were last read, newest change first: (location id, product id, store sh id, product sh id, read through)."""
    last = (select(InventoryChange.location_id.label("lid"), InventoryChange.product_id.label("pid"), func.max(InventoryChange.observed_at).label("seen"))
            .group_by(InventoryChange.location_id, InventoryChange.product_id).subquery())
    q = (select(last.c.lid, last.c.pid, Location.storehub_id, Product.storehub_id, StockMovementRead.read_through, last.c.seen)
         .join(Location, Location.id == last.c.lid).join(Product, Product.id == last.c.pid)
         .outerjoin(StockMovementRead, (StockMovementRead.location_id == last.c.lid) & (StockMovementRead.product_id == last.c.pid))
         .where(Location.source == "storehub", Product.source == "storehub", ((StockMovementRead.read_through.is_(None)) | (last.c.seen > StockMovementRead.read_through)))
         .order_by(last.c.seen.desc()))
    rows = [(lid, pid, lsh, psh, rt) for lid, pid, lsh, psh, rt, _seen in db.execute(q)]
    first = _transfer_pairs(db)
    rows.sort(key=lambda r: (r[0], r[1]) not in first)   # products on a recent stock transfer are read first (stable: newest change first otherwise)
    return rows[:limit] if limit else rows


def _transfer_pairs(db: Session, days: int = 3) -> set[tuple[int, int]]:
    """(store, product) pairs that a stock transfer shipped or received in the last few days touched, on either side of the transfer."""
    since = now_utc().replace(tzinfo=None) - timedelta(days=days)
    out: set[tuple[int, int]] = set()
    for t_src, t_dst, pid in db.execute(select(StockTransfer.source_location_id, StockTransfer.target_location_id, StockTransferLine.product_id)
                                        .join(StockTransferLine, StockTransferLine.transfer_id == StockTransfer.id)
                                        .where((StockTransfer.received_at >= since) | (StockTransfer.shipped_at >= since), StockTransferLine.product_id.is_not(None))):
        for lid in (t_src, t_dst):
            if lid:
                out.add((lid, pid))
    return out


DIRECT = "transferDirect"   # a row built from the imported stock transfer itself; replaced by StoreHub's own Audit Trail row once that is read
MATCH_MINUTES = 5


def from_transfers(db: Session, days: int = 14) -> int:
    """Stock changes straight from the imported stock transfers: 'Stock Transfer Sent' (warehouse) and 'Stock Transfer Received' (store), one row per product line,
    so a transfer shows up as soon as it is imported instead of after every product's Audit Trail has been read. Returns how many rows were added."""
    since = now_utc() - timedelta(days=days)
    added = 0
    transfers = db.scalars(select(StockTransfer).where(StockTransfer.cancelled_at.is_(None), (StockTransfer.received_at >= since) | (StockTransfer.shipped_at >= since)))
    for t in transfers:
        for loc_id, when, action, sign, who in ((t.source_location_id, t.shipped_at, "Stock Transfer Sent", -1, t.sent_by), (t.target_location_id, t.received_at, "Stock Transfer Received", 1, t.received_by)):
            if not loc_id or not when:
                continue
            at = to_local(when)
            loc = db.get(Location, loc_id)
            if not loc or loc.source != "storehub":
                continue
            for line in t.lines:
                if not line.product_id or not line.quantity:
                    continue
                change = sign * line.quantity
                near = db.scalar(select(StockMovement.id).where(
                    StockMovement.location_id == loc_id, StockMovement.product_id == line.product_id, StockMovement.action == action, StockMovement.change == change,
                    StockMovement.occurred_at >= at - timedelta(minutes=MATCH_MINUTES), StockMovement.occurred_at <= at + timedelta(minutes=MATCH_MINUTES)))
                if near:
                    continue
                prev = db.scalar(select(StockMovement.qty_after).where(StockMovement.location_id == loc_id, StockMovement.product_id == line.product_id, StockMovement.occurred_at <= at)
                                 .order_by(StockMovement.occurred_at.desc(), StockMovement.id.desc()).limit(1)) or 0.0
                db.add(StockMovement(location_id=loc_id, product_id=line.product_id, occurred_at=at, employee=(who or "")[:120], action=action, kind=DIRECT,
                                     qty_before=prev, change=change, qty_after=prev + change, link_id=f"ST:{t.reference}"[:40]))
                added += 1
    if added:
        db.flush()
    return added


WASTE = "wastageRecord"   # a row built from a Retail OS wastage record (typed here or copied from Lark)


def from_wastage(db: Session) -> int:
    """Every wastage record shows in Stock changes as a 'Wastage' row that takes its quantity off (a minus). It is a record of what staff reported; StoreHub's own stock
    is not touched. Returns how many rows were added."""
    have = {x for x in db.scalars(select(StockMovement.link_id).where(StockMovement.kind == WASTE))}
    names = {u.id: (u.full_name or u.username) for u in db.scalars(select(User))}
    from_lark = {x for x in db.scalars(select(LarkLink.entity_id).where(LarkLink.entity_type == "wastage"))}   # typed in Lark by staff: the Lark row carries no name
    added = 0
    for e in db.scalars(select(WastageEntry).options(selectinload(WastageEntry.location)).order_by(WastageEntry.id)):
        link = f"W:{e.id}"
        if link in have or e.location is None or e.location.source != "storehub":
            continue
        at = to_local(e.created_at).replace(second=0, microsecond=0)
        prev = db.scalar(select(StockMovement.qty_after).where(StockMovement.location_id == e.location_id, StockMovement.product_id == e.product_id, StockMovement.occurred_at <= at)
                         .order_by(StockMovement.occurred_at.desc(), StockMovement.id.desc()).limit(1)) or 0.0
        db.add(StockMovement(location_id=e.location_id, product_id=e.product_id, occurred_at=at, employee=("Lark" if str(e.id) in from_lark else names.get(e.created_by, ""))[:120], action="Wastage", kind=WASTE,
                             qty_before=prev, change=-abs(e.qty), qty_after=prev - abs(e.qty), link_id=link))
        added += 1
    if added:
        db.flush()
    return added


def _mark(db: Session, key: str) -> None:
    row = db.get(Setting, key) or Setting(key=key, value="")
    row.value = now_utc().isoformat()
    db.add(row)
    db.flush()


def due(db: Session) -> bool:
    now = now_utc()
    gap_ok = CATCH_UP_MINUTES if pending(db, 1) else EVERY_MINUTES
    for key, gap in (("stock_movements.last_ok", gap_ok), ("stock_movements.last_attempt", RETRY_MINUTES)):
        row = db.get(Setting, key)
        if row and row.value:
            try:
                if now - datetime.fromisoformat(str(row.value)) < timedelta(minutes=gap):
                    return False
            except ValueError:
                pass
    return True


def apply(db: Session, pair: tuple[int, int], rows: list[dict], read_at: datetime) -> int:
    """Save one product's Audit Trail rows that are not saved yet. Returns how many are new."""
    lid, pid = pair
    have = {(m.occurred_at, m.qty_before, m.change, m.action) for m in db.scalars(select(StockMovement).where(StockMovement.location_id == lid, StockMovement.product_id == pid))}
    new = 0
    for r in rows:
        at = _when(r)
        if at is None:
            continue
        before, change, after = _num(r.get("3")), _num(r.get("4")), _num(r.get("5"))
        action = str(r.get("2") or "").strip()[:80]
        key = (at, before, change, action)
        if key in have:
            continue
        have.add(key)
        for d in db.scalars(select(StockMovement).where(StockMovement.location_id == lid, StockMovement.product_id == pid, StockMovement.kind == DIRECT, StockMovement.action == action,
                                                       StockMovement.change == change, StockMovement.occurred_at >= at - timedelta(minutes=MATCH_MINUTES),
                                                       StockMovement.occurred_at <= at + timedelta(minutes=MATCH_MINUTES))):
            db.delete(d)
            db.flush()
        db.add(StockMovement(location_id=lid, product_id=pid, occurred_at=at, employee=str(r.get("1") or "").strip()[:120], action=action, kind=str(r.get("type") or "")[:40],
                             qty_before=before, change=change, qty_after=after, link_id=(str(r["linkId"])[:40] if r.get("linkId") else None), read_at=read_at))
        new += 1
    mark = db.get(StockMovementRead, (lid, pid)) or StockMovementRead(location_id=lid, product_id=pid, read_through=read_at)
    mark.read_through = read_at
    db.add(mark)
    return new


def run(db: Session, fetch=None) -> dict:
    """Read the Audit Trail of the products that changed. A failure is logged and retried later; it never stops the other jobs."""
    from .. import robot
    if fetch is None and not robot.configured():
        return {"ran": False}
    fetch = fetch or robot.fetch_audit_trails
    _mark(db, "stock_movements.last_attempt")
    from_transfers(db)
    from_wastage(db)
    db.commit()
    allp = pending(db)
    first = _transfer_pairs(db)
    todo = allp[:max(PER_RUN, sum(1 for r in allp if (r[0], r[1]) in first))]   # all products on recent stock transfers in one run
    if not todo:
        _mark(db, "stock_movements.last_ok")
        db.commit()
        return {"ran": True, "ok": True, "asked": 0, "new": 0, "waiting": 0}
    today = today_local()
    start = min((rt.date() - timedelta(days=1)) if rt else today - timedelta(days=FIRST_DAYS) for _l, _p, _s, _q, rt in todo)
    began = now_utc()
    try:
        got = fetch([(lsh, psh) for _l, _p, lsh, psh, _rt in todo], start, today)
    except (robot.RobotLoginFailed, robot.RobotNotConfigured, robot.RobotError) as e:
        log.error("audit trail read failed: %s", e)
        return {"ran": True, "ok": False, "error": str(e)}
    new = asked = 0
    for lid, pid, lsh, psh, _rt in todo:
        rows = got.get((lsh, psh))
        if rows is None:
            continue                  # not read this time: stays waiting
        asked += 1
        new += apply(db, (lid, pid), rows, began)
    _mark(db, "stock_movements.last_ok")
    db.commit()
    return {"ran": True, "ok": True, "asked": asked, "new": new, "waiting": len(pending(db))}


# ------------------------------------------------------------------------------------------------ reading them back
def movements(db: Session, loc_ids: list[int], *, start=None, end=None, product_id: int | None = None, search: str = "", action: str = "", limit: int = 100, offset: int = 0):
    q = select(StockMovement, Product, Location).join(Product, Product.id == StockMovement.product_id).join(Location, Location.id == StockMovement.location_id)\
        .where(StockMovement.location_id.in_(loc_ids or [0]))
    if start:
        q = q.where(StockMovement.occurred_at >= datetime.combine(start, datetime.min.time()))
    if end:
        q = q.where(StockMovement.occurred_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
    if product_id:
        q = q.where(StockMovement.product_id == product_id)
    if search.strip():
        like = f"%{search.strip()[:100]}%"
        q = q.where(Product.name.ilike(like) | Product.barcode.ilike(like) | Product.sku.ilike(like))
    if action:
        q = q.where(StockMovement.action == action)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(StockMovement.occurred_at.desc(), StockMovement.id.desc()).limit(limit).offset(offset)).all()
    return rows, total


def actions(db: Session, loc_ids: list[int]) -> list[str]:
    return [a for a in db.scalars(select(StockMovement.action).where(StockMovement.location_id.in_(loc_ids or [0])).distinct().order_by(StockMovement.action)) if a]
