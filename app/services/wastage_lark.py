"""Wastage that staff typed straight into the Lark 损耗记录 Wastage Record table, copied into Retail OS wastage. Only reads Lark; nothing in Lark is changed or deleted.

Each Lark row becomes one wastage entry (store, product, quantity, reason as written, the day it was created). The Lark record id is kept in the Lark link table, so a row is
never imported twice and the Lark mirror never sends it back to Lark."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_config
from ..models import LarkLink, Location, Product, Setting, User, WastageEntry
from ..timeutil import to_local

ENTITY = "wastage"


class ImportError_(Exception):
    pass


def _text(v) -> str:
    """A Lark text or dropdown value as plain text."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        return _text(v.get("text") or v.get("name") or "")
    if isinstance(v, list):
        return ", ".join(x for x in (_text(i) for i in v) if x)
    return str(v).strip()


def _key(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return s.replace("thedailycentro", "")


def _pick(fields: dict, prefix: str):
    """The value of the Lark column that starts with this English name (people add a Chinese label after it)."""
    for k, v in fields.items():
        if k.strip().lower().startswith(prefix.lower()):
            return v
    return None


def _records(client, table_id: str) -> list[dict]:
    out, token = [], None
    while True:
        d = client.call("GET", f"{client._base()}/tables/{table_id}/records", params={"page_size": 100, **({"page_token": token} if token else {})})
        out += d.get("items", [])
        if not d.get("has_more"):
            return out
        token = d.get("page_token")


def _table_id(db: Session, client) -> str:
    row = db.get(Setting, "lark.table.wastage")
    if row and row.value:
        return str(row.value).strip('"')
    for name, tid in client.tables().items():
        if "wastage" in name.lower() or "损耗" in name:
            return tid
    raise ImportError_("The Wastage Record table was not found in Lark")


def run(db: Session, user: User, client=None, auto: bool = False) -> dict:
    """Copy the new Lark rows into wastage. Returns {added, already, skipped: [reasons]}. The caller commits. auto=True is the background job: the audit entry names the system, not a person."""
    from ..lark import LarkClient, _clean, _hash, _ms, _store
    client = client or LarkClient(get_config())
    if not client.configured:
        raise ImportError_("Lark is not set up")
    tid = _table_id(db, client)
    records = _records(client, tid)
    known = {l.lark_record_id for l in db.scalars(select(LarkLink).where(LarkLink.entity_type == ENTITY, LarkLink.lark_table == tid))}
    stores = {_key(l.name): l for l in db.scalars(select(Location).where(Location.type == "store", Location.source == "storehub"))}
    by_name: dict[str, list[Product]] = {}
    for p in db.scalars(select(Product).where(Product.source == "storehub")):   # a product StoreHub no longer lists still counts for old waste
        by_name.setdefault(re.sub(r"\s+", " ", p.name.strip().lower()), []).append(p)
    names = {"Store 店铺": 0, "Date Created 创建日期": 0, "Product 产品": 0, "Quantity 数量": 0, "Reason 原因": 0}
    fm = client.field_map(tid, set(names))   # our column names -> the Lark columns that carry them
    added, already, skipped = 0, 0, []
    for rec in records:
        rid = rec.get("record_id") or ""
        if rid in known:
            already += 1
            continue
        f = rec.get("fields") or {}
        store_text, prod_text, reason = _text(_pick(f, "Store")), _text(_pick(f, "Product")), _text(_pick(f, "Reason"))
        try:
            qty = float(_pick(f, "Quantity"))
        except (TypeError, ValueError):
            qty = 0.0
        made = _pick(f, "Date Created")
        loc = stores.get(_key(store_text))
        prods = by_name.get(re.sub(r"\s+", " ", prod_text.lower()), [])
        prods = [x for x in prods if x.in_api] or prods   # a current product first; an old one only when it is the only one with that name
        if loc is None:
            skipped.append(f"{prod_text or '?'}: store '{store_text}' not found")
            continue
        if len(prods) != 1:
            skipped.append(f"{prod_text or '?'}: " + ("no product with this name" if not prods else "several products share this name"))
            continue
        if qty <= 0:
            skipped.append(f"{prod_text}: quantity missing")
            continue
        when = datetime(1970, 1, 1) + timedelta(milliseconds=made) if isinstance(made, (int, float)) else None
        p = prods[0]
        e = WastageEntry(location_id=loc.id, product_id=p.id, entry_date=(to_local(when).date() if when else to_local(datetime.utcnow()).date()), qty=qty, reason=(reason or "Other")[:80],
                         category=p.category or "", supplier_name=p.supplier.name if p.supplier else "", created_by=user.id, created_at=when or datetime.utcnow())
        db.add(e)
        db.flush()
        # the Lark link keeps the record id and a hash of what the mirror would send, so the mirror leaves this Lark row alone
        sent = {"Store 店铺": _store(loc.name), "Date Created 创建日期": _ms(e.entry_date), "Product 产品": p.name, "Category 类别": e.category or None,
                "Supplier 供应商": e.supplier_name or None, "Quantity 数量": e.qty, "Reason 原因": e.reason}   # as lark.rows_wastage would send it
        mine = {k: v for k, v in sent.items() if k in fm}
        db.add(LarkLink(entity_type=ENTITY, entity_id=str(e.id), lark_table=tid, lark_record_id=rid, content_hash=_hash(_clean(mine))))
        audit.log(db, actor_type="system" if auto else "human", actor_id="lark-sync" if auto else user.id, action="wastage.import_lark", entity_type="wastage_entry", entity_id=e.id,
                  after={"lark_record": rid, "store": loc.name, "product": p.name, "qty": qty, "reason": reason})
        added += 1
    db.flush()
    from . import stock_movements
    stock_movements.from_wastage(db)
    return {"added": added, "already": already, "skipped": skipped, "total": len(records)}

