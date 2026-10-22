"""StoreHub -> local database synchronisation.

Every job is idempotent: re-running it never duplicates rows, because each record is upserted on StoreHub's own key
(transaction refId, purchaseOrderId, product id, ...). Every run is logged, and a failing domain is marked stale.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import audit
from .. import settings_service as cfgsvc
from ..db import SessionLocal
from ..models import (ApiErrorLog, Employee, InventoryChange, InventoryCurrent, InventorySnapshot, JobLock, Location,
                      Payment, PoLine, Product, ProductCostHistory, PurchaseOrder, SalesDay, SalesDayProduct, SalesLine,
                      SalesTx, Supplier, SyncRun, SyncState, Timesheet, DqEvent)
from ..timeutil import business_date, local_iso, now_utc, parse_iso, today_local
from .client import AuthError, StoreHubClient

log = logging.getLogger("retailos.sync")

DOMAIN_OF = {"stores": "people", "employees": "people", "timesheets": "people", "products": "products",
             "inventory": "inventory", "transactions": "sales", "purchase_orders": "purchase_orders"}


def _hash(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ====================================================================== job framework
class SyncService:
    def __init__(self, client_factory: Callable[[Callable[[dict], None]], StoreHubClient] | None = None):
        self.client_factory = client_factory or (lambda sink: StoreHubClient(error_sink=sink))
        self.owner = uuid.uuid4().hex[:8]

    # -- locking: a lease row per job so two runs of the same job never overlap
    def _acquire(self, name: str, minutes: int = 30) -> bool:
        with SessionLocal() as db:
            row = db.get(JobLock, name)
            now = now_utc()
            if row and row.locked_until > now:
                return False
            if row:
                row.locked_until = now + timedelta(minutes=minutes)
                row.owner = self.owner
            else:
                db.add(JobLock(name=name, locked_until=now + timedelta(minutes=minutes), owner=self.owner))
            db.commit()
            return True

    def _release(self, name: str) -> None:
        with SessionLocal() as db:
            row = db.get(JobLock, name)
            if row and row.owner == self.owner:
                row.locked_until = now_utc()
                db.commit()

    @staticmethod
    def _flush_errors(buf: list[dict]) -> None:
        """API errors are buffered during a run and written afterwards, so logging never competes with the job's own database lock."""
        if not buf:
            return
        with SessionLocal() as db:
            for entry in buf[:200]:
                db.add(ApiErrorLog(**entry))
            db.commit()

    def run(self, job: str, fn: Callable[[Session, StoreHubClient], dict], target: str | None = None) -> dict | None:
        """Run one job under a lock, record the run, update freshness state."""
        domain = DOMAIN_OF.get(job, job)
        if not self._acquire(f"job:{job}"):
            log.info("job %s already running, skipped", job)
            return None
        with SessionLocal() as db:
            run = SyncRun(job=job, target=target)
            db.add(run)
            st = db.get(SyncState, domain) or SyncState(domain=domain)
            db.add(st)
            st.last_attempt_at = now_utc()
            db.commit()
            run_id = run.id
        err_buf: list[dict] = []
        client = self.client_factory(err_buf.append)
        stats: dict = {}
        try:
            with SessionLocal() as db:
                stats = fn(db, client) or {}
                db.commit()
            status = "ok" if not stats.get("errors") else "partial"
            self._finish(run_id, domain, status, stats, client, None)
            return stats
        except Exception as e:  # noqa: BLE001
            log.exception("job %s failed", job)
            self._finish(run_id, domain, "failed", stats, client, f"{type(e).__name__}: {e}")
            if isinstance(e, AuthError):
                raise
            return {"error": str(e)}
        finally:
            client.close()
            try:
                self._flush_errors(err_buf)
            except Exception:  # noqa: BLE001
                log.exception("could not store API error log")
            self._release(f"job:{job}")

    def _finish(self, run_id: int, domain: str, status: str, stats: dict, client: StoreHubClient, err: str | None) -> None:
        with SessionLocal() as db:
            run = db.get(SyncRun, run_id)
            run.finished_at = now_utc()
            run.status = status
            run.api_calls = client.calls
            run.retries = client.retries
            run.received = int(stats.get("received", 0))
            run.inserted = int(stats.get("inserted", 0))
            run.updated = int(stats.get("updated", 0))
            run.errors = int(stats.get("errors", 0)) + (1 if err else 0)
            run.message = err or stats.get("message")
            run.cursor = stats.get("cursor")
            st = db.get(SyncState, domain)
            if status == "ok":
                st.last_success_at = now_utc()
                st.consecutive_failures = 0
                st.last_error = None
                if stats.get("cursor"):
                    st.cursor = stats["cursor"]
                key = f"sync_failure:{domain}"
                ev = db.scalar(select(DqEvent).where(DqEvent.dedupe_key == key))
                if ev and ev.resolved_at is None:
                    ev.resolved_at = now_utc()
            else:
                st.consecutive_failures += 1
                err = err or f"partial sync: {stats.get('errors', 0)} errors. {stats.get('message') or ''}".strip()
                st.last_error = err
                key = f"sync_failure:{domain}"
                ev = db.scalar(select(DqEvent).where(DqEvent.dedupe_key == key))
                msg = f"Sync of {domain} is failing: {err}"
                if ev is None:
                    db.add(DqEvent(rule="sync_failure", severity="critical", entity_type="sync", entity_id=domain,
                                   message=msg, dedupe_key=key))
                else:
                    ev.message = msg
                    ev.last_seen = now_utc()
                    ev.resolved_at = None
            db.commit()

    # ---------------------------------------------------------------- public job entry points
    def sync_stores(self): return self.run("stores", self._stores)
    def sync_employees(self): return self.run("employees", self._employees)
    def sync_products(self): return self.run("products", self._products)
    def sync_inventory(self): return self.run("inventory", self._inventory)
    def sync_purchase_orders(self): return self.run("purchase_orders", self._purchase_orders)
    def sync_timesheets(self): return self.run("timesheets", self._timesheets)

    def sync_transactions(self, start: date | None = None, end: date | None = None):
        return self.run("transactions", lambda db, c: self._transactions(db, c, start, end))

    def reconcile(self):
        return self.run("reconcile", self._reconcile)

    def snapshot_inventory(self):
        return self.run("snapshot", self._snapshot)

    def full_sync(self) -> dict:
        """First-run / on-demand order: reference data first, then facts."""
        out = {}
        for name, fn in [("stores", self.sync_stores), ("employees", self.sync_employees), ("products", self.sync_products),
                         ("inventory", self.sync_inventory), ("purchase_orders", self.sync_purchase_orders),
                         ("transactions", self.sync_transactions), ("timesheets", self.sync_timesheets)]:
            out[name] = fn()
        return out

    # ---------------------------------------------------------------- stores / employees
    def _stores(self, db: Session, client: StoreHubClient) -> dict:
        data = client.stores()
        ins = upd = 0
        for s in data:
            loc = db.scalar(select(Location).where(Location.storehub_id == s["id"]))
            name = s.get("name") or s["id"]
            if loc is None:
                low = name.lower().replace(" ", "")
                loc = Location(storehub_id=s["id"], name=name, city=s.get("city"),
                               type="warehouse" if "warehouse" in low else "store",
                               monitored=("dailycentro" in low))
                db.add(loc)
                ins += 1
                audit.log(db, actor_type="system", actor_id="sync", action="location.discovered", entity_type="location",
                          entity_id=s["id"], after={"name": name, "monitored": loc.monitored, "type": loc.type})
            else:
                if loc.name != name or loc.city != s.get("city"):
                    loc.name, loc.city = name, s.get("city")
                    upd += 1
        return {"received": len(data), "inserted": ins, "updated": upd}

    def _employees(self, db: Session, client: StoreHubClient) -> dict:
        data = client.employees()
        ins = upd = 0
        for e in data:
            row = db.scalar(select(Employee).where(Employee.storehub_id == e["id"]))
            vals = dict(first_name=e.get("firstName") or "", last_name=e.get("lastName") or "", email=e.get("email"), phone=e.get("phone"))
            if row is None:
                db.add(Employee(storehub_id=e["id"], **vals))
                ins += 1
            elif any(getattr(row, k) != v for k, v in vals.items()):
                for k, v in vals.items():
                    setattr(row, k, v)
                upd += 1
        return {"received": len(data), "inserted": ins, "updated": upd}

    # ---------------------------------------------------------------- products
    def _products(self, db: Session, client: StoreHubClient) -> dict:
        data = client.products()
        now = now_utc()
        existing = {p.storehub_id: p for p in db.scalars(select(Product))}
        seen: set[str] = set()
        ins = upd = 0
        for d in data:
            sid = d["id"]
            seen.add(sid)
            vals = dict(name=d.get("name") or sid, sku=d.get("sku") or None, barcode=d.get("barcode") or None,
                        category=d.get("category") or None, unit_price=d.get("unitPrice"), cost=d.get("cost"),
                        track_stock=bool(d.get("trackStockLevel")), is_parent=bool(d.get("isParentProduct")),
                        tags=d.get("tags") or None)
            p = existing.get(sid)
            if p is None:
                p = Product(storehub_id=sid, **vals)
                db.add(p)
                db.flush()
                db.add(ProductCostHistory(product_id=p.id, cost=vals["cost"], unit_price=vals["unit_price"]))
                ins += 1
            else:
                changed = False
                for k, v in vals.items():
                    if getattr(p, k) != v:
                        if k in ("cost", "unit_price"):
                            db.add(ProductCostHistory(product_id=p.id, cost=vals["cost"], unit_price=vals["unit_price"]))
                        setattr(p, k, v)
                        changed = True
                if not p.in_api:
                    p.in_api = True
                    changed = True
                p.last_seen = now
                upd += 1 if changed else 0
            p.last_seen = now
        gone = 0
        for sid, p in existing.items():
            if sid not in seen and p.in_api:
                p.in_api = False  # deleted in StoreHub: it disappears from the API
                gone += 1
        return {"received": len(data), "inserted": ins, "updated": upd, "message": f"{gone} products no longer in StoreHub" if gone else None}

    # ---------------------------------------------------------------- inventory
    def _inventory(self, db: Session, client: StoreHubClient) -> dict:
        locs = list(db.scalars(select(Location).where(Location.monitored.is_(True))))
        prod_ids = {sid: pid for sid, pid in db.execute(select(Product.storehub_id, Product.id))}
        recv = ins = upd = errors = unknown = 0
        now = now_utc()
        for loc in locs:
            try:
                rows = client.inventory(loc.storehub_id)
            except AuthError:
                raise
            except Exception as e:  # noqa: BLE001  one location failing must not hide the others
                log.warning("inventory %s failed: %s", loc.name, e)
                errors += 1
                continue
            recv += len(rows)
            cur = {r.product_id: r for r in db.scalars(select(InventoryCurrent).where(InventoryCurrent.location_id == loc.id))}
            first_load = not cur
            for r in rows:
                pid = prod_ids.get(r["productId"])
                if pid is None:
                    unknown += 1
                    continue
                qty = _f(r.get("quantityOnHand"))
                row = cur.get(pid)
                if row is None:
                    db.add(InventoryCurrent(location_id=loc.id, product_id=pid, on_hand=qty, warning_stock=r.get("warningStock"),
                                            ideal_stock=r.get("idealStock"), synced_at=now))
                    if not first_load:
                        db.add(InventoryChange(location_id=loc.id, product_id=pid, old_qty=None, new_qty=qty, delta=qty, observed_at=now))
                    ins += 1
                else:
                    if abs(row.on_hand - qty) > 1e-9:
                        db.add(InventoryChange(location_id=loc.id, product_id=pid, old_qty=row.on_hand, new_qty=qty,
                                               delta=qty - row.on_hand, observed_at=now))
                        row.on_hand = qty
                        row.changed_at = now
                        upd += 1
                    row.warning_stock = r.get("warningStock")
                    row.ideal_stock = r.get("idealStock")
                    row.synced_at = now
            db.flush()
        msg = f"{unknown} inventory rows for unknown products" if unknown else None
        return {"received": recv, "inserted": ins, "updated": upd, "errors": errors, "message": msg}

    def _snapshot(self, db: Session, client: StoreHubClient) -> dict:
        d = today_local()
        n = db.scalar(select(func.count()).select_from(InventorySnapshot).where(InventorySnapshot.snap_date == d)) or 0
        if n:
            return {"message": "snapshot for today already exists"}
        rows = db.execute(select(InventoryCurrent.location_id, InventoryCurrent.product_id, InventoryCurrent.on_hand))
        db.add_all([InventorySnapshot(snap_date=d, location_id=a, product_id=b, on_hand=c) for a, b, c in rows])
        return {"inserted": db.scalar(select(func.count()).select_from(InventoryCurrent)) or 0}

    # ---------------------------------------------------------------- transactions
    def _transactions(self, db: Session, client: StoreHubClient, start: date | None, end: date | None) -> dict:
        today = today_local()
        overlap = int(cfgsvc.get(db, "sync.tx_overlap_days"))
        st = db.get(SyncState, "sales")
        if start is None:
            if st is not None and st.cursor:
                start = min(date.fromisoformat(st.cursor), today - timedelta(days=overlap))
            else:
                start = date.fromisoformat(cfgsvc.get(db, "sync.backfill_start"))
        end = end or today
        locs = {l.storehub_id: l for l in db.scalars(select(Location))}
        prods = {sid: pid for sid, pid in db.execute(select(Product.storehub_id, Product.id))}
        recv = ins = upd = skipped = errors = 0
        touched: set[tuple[int, date]] = set()
        d = start
        while d <= end:
            try:
                rows = self._fetch_day(client, d)
            except AuthError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("transactions %s failed: %s", d, e)
                errors += 1
                d += timedelta(days=1)
                continue
            recv += len(rows)
            for tx in rows:
                loc = locs.get(tx.get("storeId"))
                if loc is None:
                    self._dq_unmapped_store(db, tx.get("storeId"))
                    skipped += 1
                    continue
                if not loc.monitored:
                    skipped += 1
                    continue
                r = self._upsert_tx(db, tx, loc, prods)
                if r == "new":
                    ins += 1
                elif r == "changed":
                    upd += 1
                if r != "same":
                    touched.add((loc.id, business_date(parse_iso(tx["transactionTime"]))))
            db.flush()
            d += timedelta(days=1)
        for loc_id, bd in touched:
            recompute_sales_day(db, loc_id, bd)
        # a finished sync advances the cursor so the next run re-reads only the overlap window
        msg = f"{skipped} transactions skipped (unmonitored or unmapped stores)" if skipped else None
        out = {"received": recv, "inserted": ins, "updated": upd, "errors": errors, "message": msg}
        if errors == 0:  # only advance the cursor when every day was read, so failed days are retried
            out["cursor"] = end.isoformat()
        return out

    def _fetch_day(self, client: StoreHubClient, d: date) -> list[dict]:
        rows = client.transactions(local_iso(d), local_iso(d, end=True))
        if len(rows) >= 5000:  # API cap reached: split the day into hours so nothing is silently cut off
            rows = []
            for h in range(24):
                a = f"{d.isoformat()}T{h:02d}:00:00{local_iso(d)[-6:]}"
                b = f"{d.isoformat()}T{h:02d}:59:59.999{local_iso(d)[-6:]}"
                part = client.transactions(a, b)
                if len(part) >= 5000:
                    raise RuntimeError(f"more than 5000 transactions in one hour on {d}")
                rows.extend(part)
        return rows

    @staticmethod
    def _dq_unmapped_store(db: Session, store_id: str | None) -> None:
        key = f"missing_store_mapping:{store_id}"
        if db.scalar(select(DqEvent).where(DqEvent.dedupe_key == key)) is None:
            db.add(DqEvent(rule="missing_store_mapping", severity="warning", entity_type="store", entity_id=store_id,
                           message=f"Transactions refer to a store id that is not in the store list: {store_id}", dedupe_key=key))

    def _upsert_tx(self, db: Session, tx: dict, loc: Location, prods: dict[str, int]) -> str:
        refid = tx["refId"]
        h = _hash(tx)
        row = db.scalar(select(SalesTx).where(SalesTx.refid == refid))
        now = now_utc()
        if row is not None and row.raw_hash == h:
            row.last_seen = now
            return "same"
        t = parse_iso(tx["transactionTime"])
        vals = dict(invoice_no=tx.get("invoiceNumber"), location_id=loc.id, employee_sh_id=tx.get("employeeId"),
                    terminal=tx.get("terminalNumber"), tx_type=tx.get("transactionType") or "Sale", tx_time=t,
                    business_date=business_date(t), total=_f(tx.get("total")), subtotal=_f(tx.get("subTotal")), tax=_f(tx.get("tax")),
                    discount=_f(tx.get("discount")), rounded=_f(tx.get("roundedAmount")), service_charge=_f(tx.get("serviceCharge")),
                    cancelled=bool(tx.get("isCancelled")), cancelled_time=parse_iso(tx.get("cancelledTime")),
                    return_reason=(tx.get("returnReason") or None), sale_invoice_no=tx.get("saleInvoiceNumber"),
                    channel=tx.get("channel"), raw_hash=h, last_seen=now)
        kind = "changed"
        if row is None:
            row = SalesTx(refid=refid, first_seen=now, **vals)
            db.add(row)
            kind = "new"
        else:
            for k, v in vals.items():
                setattr(row, k, v)
            row.lines.clear()
            row.payments.clear()
        for i, it in enumerate(tx.get("items") or []):
            psid = it.get("productId")
            row.lines.append(SalesLine(line_no=i, product_id=prods.get(psid), product_sh_id=psid, item_type=it.get("itemType") or "Item",
                                       qty=_f(it.get("quantity")), unit_price=it.get("unitPrice"), subtotal=_f(it.get("subTotal")),
                                       tax=_f(it.get("tax")), discount=_f(it.get("discount")), total=_f(it.get("total"))))
        for p in tx.get("payments") or []:
            row.payments.append(Payment(method=(p.get("paymentMethod") or "Unknown"), amount=_f(p.get("amount"))))
        return kind

    # ---------------------------------------------------------------- purchase orders
    def _purchase_orders(self, db: Session, client: StoreHubClient) -> dict:
        since = date.fromisoformat(cfgsvc.get(db, "sync.po_since"))
        data = client.purchase_orders(local_iso(since), local_iso(today_local() + timedelta(days=1), end=True))
        locs = {l.storehub_id: l.id for l in db.scalars(select(Location))}
        prods = {sid: pid for sid, pid in db.execute(select(Product.storehub_id, Product.id))}
        sups = {s.storehub_id: s for s in db.scalars(select(Supplier)) if s.storehub_id}
        existing = {p.sh_po_id: p for p in db.scalars(select(PurchaseOrder))}
        now = now_utc()
        ins = upd = 0
        seen: set[int] = set()
        for po in data:
            pid = int(po["purchaseOrderId"])
            seen.add(pid)
            sid = po.get("supplierId")
            sup = None
            if sid:
                sup = sups.get(sid)
                if sup is None:
                    sup = Supplier(storehub_id=sid, name=f"Supplier {sid[-6:]}", name_source="auto")
                    db.add(sup)
                    db.flush()
                    sups[sid] = sup
            h = _hash(po)
            row = existing.get(pid)
            if row is not None and row.raw_hash == h and not row.missing_from_api:
                row.last_seen = now
                continue
            vals = dict(supplier_id=sup.id if sup else None, location_id=locs.get(po.get("targetStoreId")), status=po.get("status") or "unknown",
                        created_sh=parse_iso(po.get("createdTime")), modified_sh=parse_iso(po.get("modifiedTime")),
                        expected_at=parse_iso(po.get("expectedArrivalDate")), subtotal=_f(po.get("subTotal")), tax=_f(po.get("tax")),
                        discount=_f(po.get("discount")), total=_f(po.get("total")), notes=po.get("notes") or None,
                        completed_by=po.get("completedBy"), missing_from_api=False, raw_hash=h, last_seen=now)
            kept: list = []
            if row is None:
                row = PurchaseOrder(sh_po_id=pid, first_seen=now, **vals)
                db.add(row)
                ins += 1
            else:
                for k, v in vals.items():
                    setattr(row, k, v)
                kept = [((l.product_sh_id, None if l.supplier_price is None else round(l.supplier_price, 2)), l.price_exact) for l in row.lines if l.price_exact]
                row.lines.clear()
                upd += 1
            for it in po.get("orderedItems") or []:
                present = "receivedQuantity" in it and it["receivedQuantity"] is not None
                api_price = it.get("supplierPrice")
                exact = None  # the exact price (read from StoreHub's PO page) survives a re-sync while the rounded price StoreHub sends is the same
                for k, (kp, kv) in enumerate(kept):
                    if kp == (it.get("productId"), None if api_price is None else round(float(api_price), 2)):
                        exact = kept.pop(k)[1]
                        break
                row.lines.append(PoLine(product_id=prods.get(it.get("productId")), product_sh_id=it.get("productId") or "",
                                        ordered_qty=_f(it.get("orderedQuantity")), received_qty=_f(it.get("receivedQuantity")),
                                        received_present=present, supplier_price=float(exact) if exact else api_price, price_exact=exact, subtotal=_f(it.get("subTotal"))))
        # POs we have seen before that the API no longer returns were deleted in StoreHub
        gone = 0
        for pid, row in existing.items():
            if pid not in seen and not row.missing_from_api:
                row.missing_from_api = True
                gone += 1
        db.flush()
        derive_suppliers(db)
        msg = f"{gone} POs no longer returned by StoreHub (deleted)" if gone else None
        return {"received": len(data), "inserted": ins, "updated": upd, "message": msg}

    # ---------------------------------------------------------------- timesheets
    def _timesheets(self, db: Session, client: StoreHubClient) -> dict:
        now = now_utc()
        frm = local_iso(today_local() - timedelta(days=3))
        to = local_iso(today_local() + timedelta(days=1), end=True)
        data = client.timesheets(frm, to)
        locs = {l.storehub_id: l.id for l in db.scalars(select(Location))}
        ins = upd = 0
        for t in data:
            ci = parse_iso(t.get("clockInTime"))
            if ci is None or not t.get("employeeId"):
                continue
            row = db.scalar(select(Timesheet).where(Timesheet.employee_sh_id == t["employeeId"], Timesheet.clock_in == ci))
            co = parse_iso(t.get("clockOutTime"))
            if row is None:
                db.add(Timesheet(employee_sh_id=t["employeeId"], location_id=locs.get(t.get("storeId")), clock_in=ci, clock_out=co, seen_at=now))
                ins += 1
            elif row.clock_out != co:
                row.clock_out = co
                upd += 1
        return {"received": len(data), "inserted": ins, "updated": upd}

    # ---------------------------------------------------------------- nightly reconciliation
    def _reconcile(self, db: Session, client: StoreHubClient) -> dict:
        """Compare StoreHub with our copy for the last 7 days. Any difference becomes a data-quality event."""
        today = today_local()
        locs = {l.storehub_id: l for l in db.scalars(select(Location).where(Location.monitored.is_(True)))}
        mismatches = 0
        checked = 0
        for back in range(7):
            d = today - timedelta(days=back)
            rows = self._fetch_day(client, d)
            api: dict[str, list] = {}
            for tx in rows:
                api.setdefault(tx.get("storeId"), []).append(tx)
            for sid, loc in locs.items():
                txs = api.get(sid, [])
                a_cnt = len(txs)
                a_sum = round(sum(_f(t.get("total")) for t in txs if not t.get("isCancelled") and (t.get("transactionType") or "Sale") == "Sale"), 2)
                a_canc = sum(1 for t in txs if t.get("isCancelled"))
                row = db.get(SalesDay, (loc.id, d))
                d_cnt = (row.tx_count + row.cancelled_count) if row else 0
                d_sum = round(row.gross_sales, 2) if row else 0.0
                checked += 1
                key = f"reconciliation:sales:{loc.id}:{d.isoformat()}"
                bad = a_cnt != d_cnt or abs(a_sum - d_sum) > 0.05
                self._set_dq(db, key, "reconciliation_mismatch", "critical", "location", str(loc.id), loc.id, None,
                             f"Sales on {d} at {loc.name} differ from StoreHub: {d_cnt} vs {a_cnt} transactions, {d_sum:.2f} vs {a_sum:.2f}"
                             if bad else "", {"date": d.isoformat(), "db_count": d_cnt, "api_count": a_cnt, "db_total": d_sum, "api_total": a_sum,
                                               "api_cancelled": a_canc}, active=bad)
                mismatches += 1 if bad else 0
        # inventory item counts per location
        for sid, loc in locs.items():
            api_n = len(client.inventory(sid))
            db_n = db.scalar(select(func.count()).select_from(InventoryCurrent).where(InventoryCurrent.location_id == loc.id)) or 0
            bad = api_n != db_n
            self._set_dq(db, f"reconciliation:inventory:{loc.id}", "reconciliation_mismatch", "warning", "location", str(loc.id), loc.id, None,
                         f"Inventory rows at {loc.name} differ: {db_n} stored vs {api_n} in StoreHub" if bad else "",
                         {"db": db_n, "api": api_n}, active=bad)
            mismatches += 1 if bad else 0
            checked += 1
        return {"received": checked, "errors": 0, "message": f"{checked} checks, {mismatches} mismatches"}

    @staticmethod
    def _set_dq(db: Session, key: str, rule: str, severity: str, etype: str, eid: str | None, loc_id: int | None, prod_id: int | None,
                message: str, detail: dict | None, active: bool) -> None:
        ev = db.scalar(select(DqEvent).where(DqEvent.dedupe_key == key))
        now = now_utc()
        if active:
            if ev is None:
                db.add(DqEvent(rule=rule, severity=severity, entity_type=etype, entity_id=eid, location_id=loc_id, product_id=prod_id,
                               message=message, detail=detail, dedupe_key=key))
            else:
                ev.message, ev.detail, ev.last_seen, ev.resolved_at = message, detail, now, None
        elif ev is not None and ev.resolved_at is None:
            ev.resolved_at = now


# ====================================================================== derived data
def recompute_sales_day(db: Session, location_id: int, d: date) -> None:
    """Rebuild the per-day totals for one location and business date from the stored transactions."""
    txs = list(db.scalars(select(SalesTx).where(SalesTx.location_id == location_id, SalesTx.business_date == d)))
    live = [t for t in txs if not t.cancelled]
    sales = [t for t in live if t.tx_type != "Return"]
    rets = [t for t in live if t.tx_type == "Return"]
    gross = sum(t.total for t in sales)
    ret_total = sum(t.total for t in rets)
    cash = other = 0.0
    for t in live:
        sign = -1 if t.tx_type == "Return" else 1
        for p in t.payments:
            amt = sign * p.amount
            if p.method.lower() == "cash":
                cash += amt
            else:
                other += amt
    day = db.get(SalesDay, (location_id, d)) or SalesDay(location_id=location_id, business_date=d)
    day.tx_count = len(sales)
    day.cancelled_count = len([t for t in txs if t.cancelled])
    day.return_count = len(rets)
    day.gross_sales = round(gross, 2)
    day.returns_total = round(ret_total, 2)
    day.net_sales = round(gross - ret_total, 2)
    day.tax = round(sum(t.tax for t in sales) - sum(t.tax for t in rets), 2)
    day.cash_sales = round(cash, 2)
    day.other_sales = round(other, 2)
    day.updated_at = now_utc()
    db.add(day)
    db.execute(delete(SalesDayProduct).where(SalesDayProduct.location_id == location_id, SalesDayProduct.business_date == d))
    agg: dict[int, list[float]] = {}
    for t in live:
        sign = -1 if t.tx_type == "Return" else 1
        for ln in t.lines:
            if ln.item_type != "Item" or ln.product_id is None:
                continue
            a = agg.setdefault(ln.product_id, [0.0, 0.0])
            a[0] += sign * abs(ln.qty)
            a[1] += sign * ln.total
    for pid, (units, amt) in agg.items():
        db.add(SalesDayProduct(location_id=location_id, business_date=d, product_id=pid, units=round(units, 4), net_sales=round(amt, 2)))
    db.flush()


def derive_suppliers(db: Session) -> int:
    """StoreHub has no supplier per product. Infer it from the most recent PO that contained the product."""
    rows = db.execute(
        select(PoLine.product_id, PurchaseOrder.supplier_id, PurchaseOrder.created_sh)
        .join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
        .where(PoLine.product_id.is_not(None), PurchaseOrder.supplier_id.is_not(None), PurchaseOrder.missing_from_api.is_(False))
        .order_by(PurchaseOrder.created_sh.desc()))
    latest: dict[int, int] = {}
    for pid, sid, _ in rows:
        latest.setdefault(pid, sid)
    n = 0
    for p in db.scalars(select(Product).where(Product.id.in_(list(latest) or [0]))):
        if p.supplier_source == "manual":
            continue
        if p.supplier_id != latest[p.id]:
            p.supplier_id = latest[p.id]
            p.supplier_source = "po_history"
            n += 1
    return n
