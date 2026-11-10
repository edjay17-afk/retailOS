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
    ends = term.isdigit() and len(term) >= 4   # the last digits of a barcode find it too (the full barcode is still shown and still works)
    rank = case((or_(Product.barcode == term, Product.sku == term), 0),
                (Product.barcode.like("%" + term, escape="\\") if ends else Product.id < 0, 1), else_=2)
    rows = db.scalars(stmt.order_by(rank, Product.name, Product.id).limit(20))
    return [{"id": p.id, "name": p.name, "barcode": p.barcode or "", "sku": p.sku or ""} for p in rows]


def only_one(matches, q):
    """The product a scan or typed code means: the only match, else the one whose barcode/SKU is exactly the code, else the one whose barcode ends with these digits."""
    term = (q or "").strip()
    if len(matches) == 1:
        return matches[0]
    parts = lambda m: [b.strip() for b in (m["barcode"] or "").split(",") if b.strip()]
    exact = [m for m in matches if term and (term in parts(m) or m["sku"] == term)]
    if len(exact) == 1:
        return exact[0]
    if term.isdigit() and len(term) >= 4:
        ending = [m for m in matches if any(b.endswith(term) for b in parts(m))]
        if len(ending) == 1:
            return ending[0]
    return None
