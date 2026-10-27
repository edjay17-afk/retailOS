"""Read-only autocomplete over the synced StoreHub catalog."""
from sqlalchemy import select, or_, case
from ..models import Product


def search(db, q, location=None):
    term = q.strip()[:150]
    if not term:
        return []
    stmt = select(Product).where(Product.in_api.is_(True))
    if location is not None:
        stmt = stmt.where(Product.source == location.source, Product.source_account == location.source_account)
    for word in term.split():
        stmt = stmt.where(or_(Product.name.icontains(word, autoescape=True),
                             Product.barcode.icontains(word, autoescape=True),
                             Product.sku.icontains(word, autoescape=True)))
    rows = db.scalars(stmt.order_by(case((or_(Product.barcode == term, Product.sku == term), 0), else_=1), Product.name, Product.id).limit(20))
    return [{"id": p.id, "name": p.name, "barcode": p.barcode or "", "sku": p.sku or ""} for p in rows]
