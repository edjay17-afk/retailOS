"""The exact supplier price of every purchase order line, as StoreHub itself holds it.

StoreHub's API sends a supplier price rounded to 2 decimals (254.46), but the purchase order keeps up to 6 (254.464285) and prints them. The API subtotal cannot give the real price back, so
the robot reads each purchase order's own page in the back office (read only, as a person would) and the exact text is saved on the line (`price_exact`). Everything that needs the price
(the "last paid" price of a new order, the purchase order page, the printouts) uses it. Nothing is written to StoreHub.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import PoLine, PurchaseOrder, Setting
from ..timeutil import now_utc, to_local, today_local
from . import order_documents as docs

log = logging.getLogger("retailos.po_prices")
EVERY_MINUTES = 60
RETRY_MINUTES = 30


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def pending(db: Session) -> list[PurchaseOrder]:
    """Purchase orders with a priced line whose exact price has not been read yet."""
    q = (select(PurchaseOrder).join(PoLine, PoLine.po_id == PurchaseOrder.id)
         .where(PoLine.price_exact.is_(None), PoLine.supplier_price.is_not(None), PurchaseOrder.missing_from_api.is_(False))
         .options(selectinload(PurchaseOrder.lines).selectinload(PoLine.product)).distinct().order_by(PurchaseOrder.sh_po_id))
    return list(db.scalars(q))


def due(db: Session) -> bool:
    now = now_utc()
    for key, gap in (("po_prices.last_ok", EVERY_MINUTES), ("po_prices.last_attempt", RETRY_MINUTES)):
        row = db.get(Setting, key)
        if row and row.value:
            try:
                if now - datetime.fromisoformat(str(row.value)) < timedelta(minutes=gap):
                    return False
            except ValueError:
                pass
    return bool(pending(db))


def _mark(db: Session, key: str) -> None:
    row = db.get(Setting, key) or Setting(key=key, value="")
    row.value = now_utc().isoformat()
    db.add(row)
    db.flush()


def clean(text: str) -> str | None:
    """The price exactly as StoreHub shows it ('1,234.5000' -> '1234.5'); None when it is not a number."""
    try:
        d = Decimal(str(text).replace(",", "").strip())
    except InvalidOperation:
        return None
    return docs.plain(d) if d >= 0 else None


def apply_po(po: PurchaseOrder, rows: list[tuple[str, str]]) -> int:
    """Put StoreHub's own prices on this order's lines. Lines are matched by product name (each page row used once). Returns how many lines were set."""
    free = [(_norm(n), clean(p)) for n, p in rows]
    n = 0
    for ln in sorted(po.lines, key=lambda x: x.id):
        name = _norm(ln.product.name) if ln.product else None
        hit = next((i for i, (rn, rp) in enumerate(free) if rp is not None and rn == name), None)
        if hit is None:
            continue
        price = free.pop(hit)[1]
        ln.price_exact = price
        ln.supplier_price = float(Decimal(price))
        n += 1
    return n


def run(db: Session, fetch=None) -> dict:
    """Read the exact prices of the purchase orders still missing them. A failure is logged and retried later; it never stops the other jobs."""
    from .. import robot
    if not robot.configured():
        return {"ran": False}
    fetch = fetch or robot.fetch_po_prices
    _mark(db, "po_prices.last_attempt")
    db.commit()
    todo = pending(db)
    if not todo:
        _mark(db, "po_prices.last_ok")
        db.commit()
        return {"ran": True, "ok": True, "pos": 0, "lines": 0}
    earliest = min(to_local(p.created_sh).date() for p in todo if p.created_sh) if any(p.created_sh for p in todo) else today_local() - timedelta(days=30)
    try:
        got = fetch({p.sh_po_id for p in todo}, earliest - timedelta(days=1), today_local())
    except (robot.RobotLoginFailed, robot.RobotNotConfigured, robot.RobotError) as e:
        log.error("purchase order prices failed: %s", e)
        return {"ran": True, "ok": False, "error": str(e)}
    pos = lines = 0
    for po in todo:
        rows = got.get(po.sh_po_id)
        if rows:
            k = apply_po(po, rows)
            lines += k
            pos += 1 if k else 0
    if not pending(db):
        _mark(db, "po_prices.last_ok")
    db.commit()
    return {"ran": True, "ok": True, "pos": pos, "lines": lines, "still_missing": len(pending(db))}
