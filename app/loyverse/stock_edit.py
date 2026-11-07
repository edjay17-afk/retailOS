"""Edit Loyverse stock from Retail OS. The only place that writes to Loyverse, kept apart from the read-only sync client.
Every change is re-read from Loyverse first (so nobody overwrites a number they have not seen), written as an exact level, then read back."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from sqlalchemy.orm import Session
from .. import audit
from ..models import InventoryChange, InventoryCurrent, Location, Product, now_utc
from .client import Client, LoyverseError, accounts

MAX_CHANGES = 100
MAX_QTY = Decimal("9999999")


def parse_qty(text) -> float | None:
    """A stock number: zero or more, up to 3 decimals. Anything else is refused."""
    try:
        d = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d < 0 or d > MAX_QTY or d != d.quantize(Decimal("0.001")):
        return None
    return float(d)


def client_for(cfg, location: Location) -> Client:
    tokens = accounts(cfg.loyverse_tokens_file)
    token = tokens.get(location.source_account or "")
    if not token:
        raise LoyverseError("No access token for this Loyverse account")
    return Client(token)


def read_levels(client: Client, store_id: str, variant_ids: list[str]) -> dict[str, float]:
    """Live stock for these variants in one store. A variant with no level is left out."""
    out: dict[str, float] = {}
    for i in range(0, len(variant_ids), 40):
        chunk = variant_ids[i:i + 40]
        for row in client.pages("inventory", {"store_id": store_id, "variant_ids": ",".join(chunk)}):
            if row.get("store_id") == store_id and row.get("variant_id") in chunk and row.get("in_stock") is not None:
                out[row["variant_id"]] = float(row["in_stock"])
    return out


def write_levels(client: Client, store_id: str, levels: list[tuple[str, float]]) -> None:
    client.sleep(.55)
    body = {"inventory_levels": [{"variant_id": v, "store_id": store_id, "stock_after": q} for v, q in levels]}
    r = client.http.post("inventory", json=body)
    if r.status_code in (401, 403):
        raise LoyverseError("Loyverse did not allow this token to change stock", r.status_code, "inventory")
    if r.status_code >= 300:
        raise LoyverseError(f"Loyverse refused the stock change (HTTP {r.status_code})", r.status_code, "inventory")


def apply_changes(db: Session, cfg, user, location: Location, changes: list[dict], reason: str) -> dict:
    """changes: [{product_id, old, new}]. Returns {done: [...], skipped: [(name, why)], error: str|None}.
    A row is skipped, not written, when Loyverse no longer shows the number the person saw."""
    result = {"done": [], "skipped": [], "error": None}
    if not changes:
        return result
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([c["product_id"] for c in changes])))}
    client = client_for(cfg, location)
    try:
        live = read_levels(client, location.external_id, [products[c["product_id"]].external_id for c in changes if c["product_id"] in products])
        todo = []
        for c in changes:
            p = products.get(c["product_id"])
            if p is None or not p.external_id:
                result["skipped"].append(((p.name if p else str(c["product_id"])), "not a Loyverse item")); continue
            now = live.get(p.external_id)
            if now is None:
                result["skipped"].append((p.name, "Loyverse shows no stock level for it")); continue
            if abs(now - c["old"]) > 0.0005:
                result["skipped"].append((p.name, f"stock in Loyverse is now {now:g}, not {c['old']:g}")); continue
            todo.append((p, now, c["new"]))
        if not todo:
            return result
        write_levels(client, location.external_id, [(p.external_id, new) for p, _o, new in todo])
        after = read_levels(client, location.external_id, [p.external_id for p, _o, _n in todo])
        for p, old, new in todo:
            got = after.get(p.external_id)
            if got is None or abs(got - new) > 0.0005:
                result["skipped"].append((p.name, f"Loyverse shows {got if got is None else format(got, 'g')} after the change, expected {new:g}")); continue
            inv = db.get(InventoryCurrent, (location.id, p.id))
            if inv is None:
                inv = InventoryCurrent(location_id=location.id, product_id=p.id, on_hand=got); db.add(inv)
            else:
                inv.on_hand = got
            inv.synced_at = inv.changed_at = now_utc()
            db.add(InventoryChange(location_id=location.id, product_id=p.id, old_qty=old, new_qty=got, delta=got - old))
            audit.log(db, actor_type="user", actor_id=user.id, action="loyverse_stock_edit", entity_type="inventory", entity_id=f"{location.id}:{p.id}",
                      before={"on_hand": old}, after={"on_hand": got}, reason=reason or None, context={"location": location.name, "product": p.name})
            result["done"].append((p.name, old, got))
        db.commit()
    except LoyverseError as e:
        db.rollback()
        result["error"] = str(e)
    finally:
        client.close()
    return result
