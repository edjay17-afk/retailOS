"""Wastage records entered in Retail OS (one row per wasted product). They are mirrored to the Lark 损耗记录 Wastage Record table."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Location, Product, User, WastageEntry
from ..timeutil import today_local

REASONS = ["Spoiled", "Expired", "Damaged", "Lost / theft", "Supplier return", "Other"]


class WastageError(Exception):
    pass


def find_product(db: Session, text: str) -> Product:
    """Barcode or SKU first, then a name that matches exactly one product (the same pattern the other forms use)."""
    s = (text or "").strip()
    if not s:
        raise WastageError("Choose a product")
    p = db.scalar(select(Product).where(Product.barcode == s)) or db.scalar(select(Product).where(Product.sku == s))
    if p is not None:
        return p
    exact = list(db.scalars(select(Product).where(Product.name == s, Product.in_api.is_(True)).limit(2)))
    if len(exact) == 1:
        return exact[0]
    found = list(db.scalars(select(Product).where(Product.name.like(f"%{s}%"), Product.in_api.is_(True)).limit(6)))
    if len(found) == 1:
        return found[0]
    raise WastageError(f"{len(found)} products match '{s}'. Scan the barcode or type more of the name." if found else f"No product matches '{s}'")


def add(db: Session, user: User, *, location_id: int, product: Product, qty: float, reason: str, on: date | None = None) -> WastageEntry:
    if not qty or qty <= 0:
        raise WastageError("Quantity must be greater than zero")
    if reason not in REASONS:
        raise WastageError("Choose a reason from the list")
    loc = db.get(Location, location_id)
    if loc is None:
        raise WastageError("Choose a store")
    on = on or today_local()
    if on > today_local():
        raise WastageError("The date cannot be in the future")
    e = WastageEntry(location_id=location_id, product_id=product.id, entry_date=on, qty=qty, reason=reason, category=product.category or "",
                     supplier_name=product.supplier.name if product.supplier else "", created_by=user.id)
    db.add(e)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="wastage.add", entity_type="wastage_entry", entity_id=e.id,
              after={"location_id": location_id, "product_id": product.id, "qty": qty, "reason": reason, "date": str(on)})
    return e
