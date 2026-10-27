"""Atomic per-account sync of catalog and minimal receipt data, using GET only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from threading import Lock
from sqlalchemy import select
from ..config import get_config
from ..db import SessionLocal
from ..models import LoyverseAccount, LoyverseStore, LoyverseVariant, LoyverseReceipt
from .. import settings_service
from ..timeutil import now_utc, today_local
from .client import Client, LoyverseError, accounts

_lock = Lock()

def moment(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None: raise ValueError("Timezone required")
    return value.astimezone(timezone.utc).replace(tzinfo=None)

def stamp(value):
    return value.isoformat(timespec="milliseconds") + "Z"

def receipt_values(row):
    if row["receipt_type"] not in ("SALE", "REFUND"):
        raise ValueError("Unexpected receipt type")
    date = (moment(row["receipt_date"]) + timedelta(hours=8)).date()
    lines = []
    for line in row.get("line_items", []):
        qty = Decimal(str(line["quantity"]))
        if not qty.is_finite() or qty < 0: raise ValueError("Invalid receipt quantity")
        # Store no customer, staff, payment or receipt-note data.
        lines.append({"variant_id": line.get("variant_id"), "name": line.get("item_name") or "Unknown item", "quantity": str(qty)})
    return dict(store_id=row["store_id"], business_date=date, receipt_type=row["receipt_type"],
                cancelled=bool(row.get("cancelled_at")), lines=lines, updated_at=moment(row["updated_at"]))

def sync_account(key, token, factory=Client):
    began = now_utc()
    with SessionLocal() as db:
        state = db.get(LoyverseAccount, key)
        lookback = int(settings_service.get(db, "repl.lookback_days"))
        start = today_local() - timedelta(days=max(lookback, 14) + 2)
        full = not state or not state.history_from or state.history_from > start
        since = datetime.combine(start, datetime.min.time()) - timedelta(hours=8) if full else state.last_success_at - timedelta(minutes=3) if state.last_success_at else datetime.combine(start, datetime.min.time()) - timedelta(hours=8)
        catalog_due = not state or not state.catalog_at or began - state.catalog_at >= timedelta(minutes=15)
    client = factory(token)
    try:
        stores = list(client.pages("stores")) if catalog_due else None
        items = list(client.pages("items")) if catalog_due else None
        receipts = list(client.pages("receipts", {"updated_at_min": stamp(since), "updated_at_max": stamp(began)}))
        parsed = [(r["receipt_number"], receipt_values(r)) for r in receipts]
        with SessionLocal() as db:
            state = db.get(LoyverseAccount, key)
            if not state:
                state = LoyverseAccount(key=key); db.add(state)
            if stores is not None:
                known = {s.store_id:s for s in db.scalars(select(LoyverseStore).where(LoyverseStore.account_key==key))}
                for s in known.values(): s.active = False
                for row in stores:
                    obj = known.get(row["id"])
                    if obj is None:
                        obj = LoyverseStore(account_key=key,store_id=row["id"]); db.add(obj)
                    obj.name = row["name"]; obj.active = not bool(row.get("deleted_at"))
                known_items = {v.variant_id:v for v in db.scalars(select(LoyverseVariant).where(LoyverseVariant.account_key==key))}
                for v in known_items.values(): v.active=False
                for item in items:
                    for variant in item.get("variants", []):
                        obj = known_items.get(variant["variant_id"])
                        if obj is None:
                            obj=LoyverseVariant(account_key=key,variant_id=variant["variant_id"]); db.add(obj)
                        options = [variant.get(f"option{i}_value") for i in (1,2,3) if variant.get(f"option{i}_value")]
                        obj.item_id=item["id"]; obj.name=item["item_name"] + (" / " + " / ".join(options) if options else "")
                        obj.sku=variant.get("sku");obj.barcode=variant.get("barcode")
                        obj.active=not (item.get("deleted_at") or variant.get("deleted_at"))
                        obj.sold_by_weight=bool(item.get("sold_by_weight"));obj.composite=bool(item.get("is_composite"))
                state.catalog_at=began
            for number, values in parsed:
                obj=db.get(LoyverseReceipt,(key,number))
                if obj and obj.updated_at > values["updated_at"]: continue
                if obj is None:
                    obj=LoyverseReceipt(account_key=key,receipt_number=number);db.add(obj)
                for name,value in values.items():setattr(obj,name,value)
            state.last_success_at=began;state.last_error=None
            state.history_from=min(state.history_from or start,start)
            db.commit()
        return {"account":key,"receipts":len(parsed),"status":"ok"}
    except Exception as error:
        # Only our deliberately sanitized errors may reach logs or the UI.
        message = str(error) if isinstance(error,LoyverseError) else "Loyverse data could not be processed; previous data was retained"
        with SessionLocal() as db:
            state=db.get(LoyverseAccount,key)
            if not state:state=LoyverseAccount(key=key);db.add(state)
            state.last_error=message;db.commit()
        return {"account":key,"status":"failed","error":message}
    finally:
        client.close()

def sync_all(factory=Client):
    if not _lock.acquire(blocking=False): return []
    try:
        return [sync_account(k,t,factory) for k,t in accounts(get_config().loyverse_tokens_file).items()]
    finally:
        _lock.release()
