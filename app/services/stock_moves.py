"""Stock compare, 'Move from warehouse': for one store, the products the warehouse has that the store is short of, with a suggested quantity to send."""
from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from .. import settings_service as cfg
from ..models import InventoryCurrent, Location, Product, SalesDayProduct
from ..timeutil import today_local

DAYS = 14   # how far back the selling rate looks


def cover_days(db: Session) -> float:
    try:
        return float(cfg.get(db, "repl.sales_only_cover_days") or 3)
    except (TypeError, ValueError):
        return 3.0


def stores(locations) -> list[Location]:
    """The stores that can be restocked from the warehouse: StoreHub stores that are open."""
    return sorted([l for l in locations if l.type == "store" and l.source == "storehub" and not l.closed], key=lambda l: l.name)


def candidates(db: Session, warehouse_id: int, store_id: int, *, q: str = "") -> list[dict]:
    """Products sold at the store in the last 14 days, in stock at the warehouse, and short at the store (less than the cover days of sales). Best sellers first.
    send = what the store needs for the cover days minus what it has (nothing below zero counts), but never more than the warehouse has."""
    cover = cover_days(db)
    since = today_local() - timedelta(days=DAYS)
    sold = (select(SalesDayProduct.product_id.label("pid"), (func.sum(SalesDayProduct.units) / float(DAYS)).label("ads"))
            .where(SalesDayProduct.location_id == store_id, SalesDayProduct.business_date >= since, SalesDayProduct.units > 0)
            .group_by(SalesDayProduct.product_id).subquery())
    w, s = aliased(InventoryCurrent), aliased(InventoryCurrent)
    stmt = (select(Product, w.on_hand, s.on_hand, sold.c.ads)
            .join(w, (w.product_id == Product.id) & (w.location_id == warehouse_id))
            .join(s, (s.product_id == Product.id) & (s.location_id == store_id))
            .join(sold, sold.c.pid == Product.id)
            .where(Product.in_api.is_(True), Product.source == "storehub", w.on_hand > 0, s.on_hand < sold.c.ads * cover))
    for word in (q or "").strip()[:100].split():
        stmt = stmt.where(Product.name.icontains(word, autoescape=True) | Product.barcode.icontains(word, autoescape=True) | Product.sku.icontains(word, autoescape=True))
    out = []
    for p, wq, sq, ads in db.execute(stmt.order_by(sold.c.ads.desc(), Product.name)):
        need = math.ceil(ads * cover) - max(sq or 0, 0)
        send = int(min(need, math.floor(wq)))
        if send >= 1:
            out.append({"p": p, "wh": wq, "store": sq, "ads": round(ads, 1), "send": send})
    return out


def short_count(db: Session, store_id: int) -> int:
    """Products at the store that are zero or negative (in StoreHub's list)."""
    return db.scalar(select(func.count()).select_from(InventoryCurrent).join(Product, Product.id == InventoryCurrent.product_id)
                     .where(InventoryCurrent.location_id == store_id, InventoryCurrent.on_hand <= 0, Product.in_api.is_(True), Product.source == "storehub")) or 0
