"""Push a saved new-product list straight into StoreHub (partner API: POST /products).

Only products with a selling price can go: StoreHub will not create a fixed-price product without one. Items without a
price stay "pending" and follow the Excel/RPA path as before. A created product is upserted into the saved product list
at once, so the next scan sees it without waiting for the scheduled sync. A retry after a lost response recovers by
matching the barcode in StoreHub instead of creating a duplicate.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import NewProductItem, NewProductList, Product, ProductCostHistory, StoreHubPush, User
from ..storehub.client import ApiError, StoreHubClient
from ..timeutil import now_utc
from .new_products import barcode_variants


class PushError(Exception):
    pass


def _client(client: StoreHubClient | None) -> StoreHubClient:
    return client if client is not None else StoreHubClient()


def _payload(item: NewProductItem) -> dict:
    # the doc says "Fixed", but the live API rejects it: "Expected type PriceType, found Fixed; Did you mean the enum value fixed?"
    p = {"name": item.name, "priceType": "fixed", "unitPrice": item.price, "trackStockLevel": True,
         "barcode": item.barcode, "category": item.category}
    if item.cost is not None:
        p["cost"] = item.cost
    return p


def _upsert_product(db: Session, d: dict) -> Product:
    """One product from the API (the create response or a list entry) into the saved product list — same shape as the sync."""
    sid = d["id"]
    p = db.scalar(select(Product).where(Product.storehub_id == sid))
    vals = dict(name=d.get("name") or sid, sku=d.get("sku") or None, barcode=d.get("barcode") or None,
                category=d.get("category") or None, unit_price=d.get("unitPrice"), cost=d.get("cost"),
                track_stock=bool(d.get("trackStockLevel")), is_parent=bool(d.get("isParentProduct")), tags=d.get("tags") or None)
    if p is None:
        p = Product(storehub_id=sid, **vals)
        db.add(p)
        db.flush()
        db.add(ProductCostHistory(product_id=p.id, cost=vals["cost"], unit_price=vals["unit_price"]))
    else:
        for k, v in vals.items():
            if getattr(p, k) != v:
                setattr(p, k, v)
        p.in_api = True
    p.last_seen = now_utc()
    db.flush()
    return p


def _find_in_storehub(client: StoreHubClient, barcode: str) -> dict | None:
    """The raw /products list entry whose barcode (possibly comma-separated) matches, else None."""
    want = barcode_variants(barcode)
    for d in client.products():
        tokens = {t.strip() for t in (d.get("barcode") or "").split(",") if t.strip()}
        if want & tokens:
            return d
    return None


def _record(db: Session, item: NewProductItem, user: User | None, origin: str) -> None:
    """One line in the push registry for a product that just landed in StoreHub. Idempotent per StoreHub product id, so a
    list pushed twice (or two lists recovering to the same product) leaves exactly one record."""
    if item.storehub_product_id and db.scalar(select(StoreHubPush.id).where(StoreHubPush.storehub_product_id == item.storehub_product_id)):
        return
    db.add(StoreHubPush(storehub_product_id=item.storehub_product_id or "", barcode=item.barcode, name=item.name,
                        category=item.category, price=item.price, cost=item.cost, origin=origin,
                        list_id=item.list_id or None, pushed_by=user.id if user else None, pushed_at=now_utc()))


def push_item(db: Session, item: NewProductItem, client: StoreHubClient | None = None) -> NewProductItem:
    """Create one list item in StoreHub and link it. Never raises for StoreHub problems: the item keeps the error instead."""
    if item.status == "created" and item.storehub_product_id:
        return item
    if item.price is None:
        raise PushError(f"{item.name}: no selling price — StoreHub cannot create the product without one")
    client = _client(client)
    try:
        created = client.create_product(_payload(item))
        if not isinstance(created, dict) or not created.get("id"):
            raise ApiError(f"POST /products: unexpected answer {str(created)[:200]}")
        _upsert_product(db, created)
        item.status, item.storehub_product_id, item.push_error = "created", created["id"], None
        return item
    except Exception as e:
        # the create may have gone through with the answer lost, or StoreHub refused a duplicate: look before failing
        # (an auth failure fails the recovery lookup too, and the item simply keeps the error)
        try:
            twin = _find_in_storehub(client, item.barcode)
        except Exception:
            twin = None
        if twin is not None:
            _upsert_product(db, twin)
            item.status, item.storehub_product_id, item.push_error = "created", twin["id"], None
            return item
        item.status, item.push_error = "failed", str(e)[:300]
        return item


def push_list(db: Session, user: User, lst: NewProductList, client: StoreHubClient | None = None) -> dict:
    """Push every pending/failed item that has a price. Each item commits on its own, so a stopped run keeps its progress."""
    client = _client(client)
    made = failed = skipped = 0
    for item in list(lst.items):
        if item.status == "created" or item.price is None:
            skipped += 1
            continue
        push_item(db, item, client)
        if item.status == "created":
            _record(db, item, user, "list")
        db.commit()
        made += item.status == "created"
        failed += item.status == "failed"
    out = {"created": made, "failed": failed, "skipped": skipped}
    audit.log(db, actor_type="human", actor_id=user.id, action="new_product_list.push", entity_type="new_product_list", entity_id=lst.id, after=out)
    db.commit()
    return out


def quick_create(db: Session, user: User, *, barcode: str, name: str, category: str = "", price=None, cost=None,
                 origin: str = "quick", client: StoreHubClient | None = None) -> Product:
    """One product created in StoreHub on the spot (the purchase-order page needs it before it can be ordered).
    No list, no Excel: validate -> create in StoreHub -> saved product list -> push registry. Raises PushError when anything refuses."""
    from .new_products import lookup

    barcode, name, category = (barcode or "").strip(), " ".join((name or "").split()), (category or "").strip()
    if not barcode:
        raise PushError("Scan or enter the barcode")
    if not name:
        raise PushError("Enter the product name")
    if price is None or str(price).strip() == "":
        raise PushError("Enter the selling price — StoreHub cannot create the product without one")
    try:
        price = float(str(price).strip().replace(",", ""))
        cost = None if cost is None or str(cost).strip() == "" else float(str(cost).strip().replace(",", ""))
    except (ValueError, AttributeError):
        raise PushError("Enter the prices as numbers") from None
    if price < 0 or (cost is not None and cost < 0):
        raise PushError("A price cannot be negative")
    if cost is not None and price < cost:
        raise PushError(f"The selling price ({price:g}) is below the cost ({cost:g}) — raise the price")
    known = lookup(db, barcode)
    if known is not None:
        raise PushError(f"Already in StoreHub: {known.name}")
    item = NewProductItem(list_id=0, barcode=barcode[:80], name=name[:300], category=category[:120], qty=0, price=price, cost=cost)
    push_item(db, item, client)
    if item.status != "created":
        raise PushError(item.push_error or "StoreHub refused the product")
    _record(db, item, user, origin)
    p = db.scalar(select(Product).where(Product.storehub_id == item.storehub_product_id))
    audit.log(db, actor_type="human", actor_id=user.id, action="product.quick_create", entity_type="product", entity_id=p.id,
              after={"barcode": barcode, "name": name, "category": category, "price": price, "cost": cost,
                     "storehub_id": item.storehub_product_id})
    db.commit()
    return p
