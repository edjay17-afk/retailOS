"""Optional one-way mirror of operational records into a Lark Base (Lark is used as storage and for people to browse).

This mirror does not import data. A separate lark_inspections service reads external report links for review. The local database stays the source of truth for inspection results, and StoreHub stays the
source of truth for sales and stock. The mirror is idempotent: each record is linked to its Lark record id, and only records whose
content changed are sent again. It is disabled until LARK_APP_ID, LARK_APP_SECRET and LARK_BASE_TOKEN are set and `lark-setup` has run.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import Config, get_config
from .db import SessionLocal
from .models import (InspectionResult, InspectionTemplate, SalesDayProduct, Employee, ExpiryBatch, InventoryCurrent, Inspection, Issue, LarkLink, Location, Product, PurchaseOrder, StockTransfer, WastageEntry, Receiving, Recommendation,
                     SalesDay, Setting, User)
from .timeutil import now_utc, today_local

log = logging.getLogger("retailos.lark")
HOST = "https://open.larksuite.com"
TEXT, NUMBER, DATE = 1, 2, 5


class LarkError(Exception):
    pass


class LarkClient:
    def __init__(self, cfg: Config | None = None, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg or get_config()
        self.http = httpx.Client(base_url=HOST, timeout=30, transport=transport)
        self._token: tuple[str, float] | None = None
        self.sleep = sleep
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.cfg.lark_app_id and self.cfg.lark_app_secret and self.cfg.lark_base_token)

    def _tenant_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = self.http.post("/open-apis/auth/v3/tenant_access_token/internal", json={"app_id": self.cfg.lark_app_id, "app_secret": self.cfg.lark_app_secret})
        d = r.json()
        if d.get("code") != 0:
            raise LarkError(f"Lark authentication failed: {d.get('msg')}")
        self._token = (d["tenant_access_token"], time.time() + int(d.get("expire", 7200)))
        return self._token[0]

    def call(self, method: str, path: str, **kw) -> dict:
        for attempt in range(1, 5):
            gap = 0.2 - (time.monotonic() - self._last)  # at most 5 calls a second
            if gap > 0:
                self.sleep(gap)
            self._last = time.monotonic()
            r = self.http.request(method, path, headers={"Authorization": f"Bearer {self._tenant_token()}"}, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                self.sleep(2 * attempt)
                continue
            d = r.json()
            if d.get("code") in (0, None) and r.status_code == 200:
                return d.get("data") or {}
            if d.get("code") in (99991663, 99991661):  # token expired
                self._token = None
                continue
            raise LarkError(f"{method} {path}: {d.get('code')} {d.get('msg')}")
        raise LarkError(f"{method} {path}: gave up after retries")

    # ---- Base (bitable) helpers
    def app_token(self) -> str:
        """LARK_BASE_TOKEN may be the Base token, a /base/ link, or a /wiki/ link (a wiki page holding a Base): resolve it to the Base token."""
        if getattr(self, "_app", None):
            return self._app
        raw = self.cfg.lark_base_token.strip()
        m = re.search(r"/(wiki|base)/([A-Za-z0-9]+)", raw)
        kind, tok = (m.group(1), m.group(2)) if m else ("base", raw)
        if kind == "wiki":
            node = self.call("GET", "/open-apis/wiki/v2/spaces/get_node", params={"token": tok}).get("node", {})
            if node.get("obj_type") != "bitable":
                raise LarkError(f"The wiki page is a {node.get('obj_type')}, not a Base")
            tok = node["obj_token"]
        self._app = tok
        return tok

    def _base(self) -> str:
        return f"/open-apis/bitable/v1/apps/{self.app_token()}"

    def tables(self) -> dict[str, str]:
        out, token = {}, None
        while True:
            d = self.call("GET", f"{self._base()}/tables", params={"page_size": 100, **({"page_token": token} if token else {})})
            out.update({t["name"]: t["table_id"] for t in d.get("items", [])})
            if not d.get("has_more"):
                return out
            token = d.get("page_token")

    def writable_fields(self, table_id: str) -> set[str]:
        """Names of the columns we may write: formula, lookup, created-by and similar computed columns are read-only."""
        d = self.call("GET", f"{self._base()}/tables/{table_id}/fields", params={"page_size": 100})
        return {f["field_name"] for f in d.get("items", []) if f.get("type") not in (19, 20, 21, 1001, 1002, 1003, 1004, 1005)}

    def field_map(self, table_id: str, wanted) -> dict[str, str]:
        """Our column name -> the writable column of that name in Lark. People rename columns (add a Chinese label, a double space), so after an exact match
        the closest column by its English words is used. Computed columns (formula, created-by...) are never written."""
        d = self.call("GET", f"{self._base()}/tables/{table_id}/fields", params={"page_size": 100})
        actual = [f["field_name"] for f in d.get("items", []) if f.get("type") not in (19, 20, 21, 1001, 1002, 1003, 1004, 1005)]
        norm = lambda n: re.sub(r"\s+", " ", n.strip().lower())
        words = lambda n: set(re.findall(r"[a-z0-9#]+", n.lower()))
        out: dict[str, str] = {}
        taken: set[str] = set()
        for w in wanted:  # exact (ignoring case and spacing) first
            hit = next((a for a in actual if a not in taken and norm(a) == norm(w)), None)
            if hit:
                out[w] = hit
                taken.add(hit)
        for w in wanted:
            if w in out:
                continue
            ww = words(w)
            scored = sorted(((len(ww & words(a)) / len(ww | words(a)), a) for a in actual if a not in taken and ww & words(a)), reverse=True)
            if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                out[w] = scored[0][1]
                taken.add(scored[0][1])
        return out

    def create_table(self, name: str, fields: list[tuple[str, int]]) -> str:
        d = self.call("POST", f"{self._base()}/tables", json={"table": {"name": name, "default_view_name": "All",
                      "fields": [{"field_name": n, "type": t} for n, t in fields]}})
        return d["table_id"]

    def batch_create(self, table_id: str, rows: list[dict]) -> list[str]:
        d = self.call("POST", f"{self._base()}/tables/{table_id}/records/batch_create", json={"records": [{"fields": r} for r in rows]})
        return [r["record_id"] for r in d.get("records", [])]

    def send_text(self, chat_id: str, text: str) -> None:
        """Post a message to a Lark chat (the app must be a member of that chat)."""
        self.call("POST", "/open-apis/im/v1/messages", params={"receive_id_type": "chat_id"},
                  json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text})})

    def upload_media(self, path: Path, parent_type: str) -> str:
        """Upload a file into the Base's storage and return its file token (an attachment cell is a list of these). Big files go up in parts."""
        n = path.stat().st_size
        base = {"file_name": path.name, "parent_type": parent_type, "parent_node": self.app_token()}
        if n <= 15_000_000:
            r = self._media("/open-apis/drive/v1/medias/upload_all", {**base, "size": str(n)}, {"file": (path.name, path.read_bytes())})
            return r["file_token"]
        prep = self.call("POST", "/open-apis/drive/v1/medias/upload_prepare", json={**base, "size": n})
        size, blocks, uid = int(prep["block_size"]), int(prep["block_num"]), prep["upload_id"]
        with path.open("rb") as fh:
            for seq in range(blocks):
                chunk = fh.read(size)
                self._media("/open-apis/drive/v1/medias/upload_part", {"upload_id": uid, "seq": str(seq), "size": str(len(chunk))}, {"file": (path.name, chunk)})
        return self.call("POST", "/open-apis/drive/v1/medias/upload_finish", json={"upload_id": uid, "block_num": blocks})["file_token"]

    def _media(self, url: str, data: dict, files: dict) -> dict:
        for attempt in range(1, 4):
            r = self.http.post(url, headers={"Authorization": f"Bearer {self._tenant_token()}"}, data=data, files=files, timeout=300)
            if r.status_code == 429 or r.status_code >= 500:
                self.sleep(2 * attempt)
                continue
            d = r.json()
            if d.get("code") == 0:
                return d.get("data") or {}
            raise LarkError(f"upload {url}: {d.get('code')} {d.get('msg')}")
        raise LarkError(f"upload {url}: gave up after retries")

    def add_field(self, table_id: str, name: str, ftype: int) -> None:
        self.call("POST", f"{self._base()}/tables/{table_id}/fields", json={"field_name": name, "type": ftype})

    def field_names(self, table_id: str) -> set[str]:
        d = self.call("GET", f"{self._base()}/tables/{table_id}/fields", params={"page_size": 100})
        return {f["field_name"] for f in d.get("items", [])}

    def batch_delete(self, table_id: str, record_ids: list[str]) -> None:
        self.call("POST", f"{self._base()}/tables/{table_id}/records/batch_delete", json={"records": record_ids})

    def batch_update(self, table_id: str, rows: list[tuple[str, dict]]) -> None:
        self.call("POST", f"{self._base()}/tables/{table_id}/records/batch_update", json={"records": [{"record_id": rid, "fields": f} for rid, f in rows]})


def _ms(d: date | datetime | None) -> int | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class Spec:
    entity: str
    table: str
    fields: list[tuple[str, int]]  # first field is the unique text key (the primary field)
    rows: Callable[[Session], list[tuple[str, dict[str, Any]]]]  # -> [(entity_id, fields)]
    extra: list[tuple[str, int]] = field(default_factory=list)  # columns added to an existing table when it lacks them (e.g. a date)


class Files(list):
    """Relative paths of uploaded photos or videos. They are sent to Lark as attachments (uploaded just before the row is written)."""


VIDEO_EXT = (".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv")


def _resolve_files(client: "LarkClient", fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if isinstance(v, Files):
            toks = []
            for rel in v:
                path = get_config().upload_dir / rel
                if path.is_file():
                    toks.append({"file_token": client.upload_media(path, "bitable_file" if path.suffix.lower() in VIDEO_EXT else "bitable_image")})
            out[k] = toks
        else:
            out[k] = v
    return out


def _names(db: Session):
    return ({l.id: l.name for l in db.scalars(select(Location))}, {u.id: (u.full_name or u.username) for u in db.scalars(select(User))})


def rows_issues(db):
    locs, users = _names(db)
    out = []
    for i in db.scalars(select(Issue)):
        out.append((str(i.id), {"Key": f"issue-{i.id}", "Severity": i.severity, "Status": i.status, "Type": i.type, "Location": locs.get(i.location_id, ""), "Title": i.title,
                                "Owner": users.get(i.owner_id, ""), "Opened": _ms(i.created_at), "Due": _ms(i.due_at), "Closed": _ms(i.closed_at),
                                "Resolution": i.resolution_note or "", "Escalation level": i.escalation_level}))
    return out


def rows_sales(db):
    """Fills the existing 销售量 Sales layout: one row per store per day. GCash is every non-cash payment."""
    locs = _monitored(db)
    return [(f"{x.location_id}:{x.business_date}", {"Store 门店": locs[x.location_id], "Date": _ms(x.business_date), "Cash": x.cash_sales, "GCash": x.other_sales,
                                                  "Daily Sales": x.net_sales, "Transactions": x.tx_count}) for x in db.scalars(select(SalesDay).where(SalesDay.location_id.in_(locs or [0])))]


def rows_products(db):
    """Fills the existing 产品总表 Product List layout: distributor, contact, category, product name."""
    from .models import Supplier
    sups = {x.id: x for x in db.scalars(select(Supplier))}
    out = []
    for p in db.scalars(select(Product)):
        sp = sups.get(p.supplier_id)
        out.append((str(p.id), {"Distributor  经销商": sp.name if sp else "", "Contact No": (sp.contact if sp else None) or None, "Category 类别": p.category or None,
                                "Product Name 产品名称": p.name}))
    return out


def _store(name: str | None) -> str:
    """The store names used in the Lark dropdowns: 'The Daily Centro - Getafe' is 'The Daily Centro Getafe', the warehouse is 'TDC Warehouse'."""
    name = name or ""
    return "TDC Warehouse" if "warehouse" in name.lower() else name.replace(" - ", " ")


def _monitored(db):
    return {l.id: _store(l.name) for l in db.scalars(select(Location).where(Location.monitored.is_(True)))}


def rows_inventory(db):
    """Fills the existing 存货 Inventory layout for the monitored stores and the warehouse."""
    locs = _monitored(db)
    q = select(InventoryCurrent, Product.name, Product.category, Product.barcode).join(Product, Product.id == InventoryCurrent.product_id).where(InventoryCurrent.location_id.in_(locs or [0]))
    return [(f"{i.location_id}:{i.product_id}", {"Product 产品": name, "Category 类别": cat or None, "Store 门店": locs[i.location_id], "Barcode 条形码": barcode or None, "Qty 数量": i.on_hand})
            for i, name, cat, barcode in db.execute(q)]


def rows_purchase_orders(db):
    """Fills the existing 采购订单 Purchase Order layout: one row per product line, then ONE closing row per PO that carries the total amount
    (so the total is not repeated on every product). Rows come out PO by PO so they are created, and shown, in that order."""
    locs = {l.id: _store(l.name) for l in db.scalars(select(Location))}
    names = dict(db.execute(select(Product.id, Product.name)).all())
    q = select(PurchaseOrder).options(selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.lines)).order_by(PurchaseOrder.sh_po_id)
    out = []
    for o in db.scalars(q):
        po_no, sup, dest = f"PO-{o.sh_po_id}", (o.supplier.name if o.supplier else ""), locs.get(o.location_id, "")
        # StoreHub's own total is used; when it reports 0 for a PO that has priced lines (it does for some completed POs), the total is worked out from the lines
        total = o.total or round(sum(l.ordered_qty * (l.supplier_price or 0) for l in o.lines) + (o.tax or 0) - (o.discount or 0), 2)
        for ln in sorted(o.lines, key=lambda x: x.id):
            price = ln.supplier_price or 0
            out.append((str(ln.id), {"PO # 采购订单号": po_no, "Supplier 供应商": sup, "Deliver to 配送至": dest, "Product 产品": names.get(ln.product_id, ln.product_sh_id),
                                     "Quantity 数量": f"{ln.ordered_qty:g}", "Amount 数量": f"{ln.ordered_qty * price:.2f}", "Total Amount 总金额": ""}))
        out.append((f"total:{o.id}", {"PO # 采购订单号": "", "Supplier 供应商": "", "Deliver to 配送至": "", "Product 产品": "", "Total Amount 总金额": f"{total:.2f}"}))  # the closing row carries only the total
    return out


def rows_stock_transfers(db):
    """Fills the 股份转让 Stock Transfer layout (same shape as the purchase orders): one row per product line, then ONE closing row per transfer with its total.
    Supplier 供应商 holds the store/warehouse it left, Deliver to 配送至 the one it went to."""
    names = dict(db.execute(select(Product.id, Product.name)).all())
    q = select(StockTransfer).options(selectinload(StockTransfer.lines)).order_by(StockTransfer.created_sh, StockTransfer.id)
    out = []
    for t in db.scalars(q):
        total = t.total if t.total is not None else round(sum(l.subtotal or 0 for l in t.lines), 2)
        for ln in t.lines:
            out.append((f"{t.id}:{ln.id}", {"ST # 股份转让编号": t.reference, "Supplier 供应商": _store(t.source_name), "Deliver to 配送至": _store(t.target_name),
                                            "Product 产品": names.get(ln.product_id) or ln.product_name, "Quantity 数量": f"{ln.quantity:g}",
                                            "Amount 数量": f"{ln.subtotal or 0:.2f}", "Total Amount 总金额": ""}))
        out.append((f"total:{t.id}", {"ST # 股份转让编号": "", "Supplier 供应商": "", "Deliver to 配送至": "", "Product 产品": "", "Total Amount 总金额": f"{total or 0:.2f}"}))
    return out


def rows_wastage(db):
    """The 损耗记录 Wastage Record table: one row per wasted product."""
    q = select(WastageEntry).options(selectinload(WastageEntry.product), selectinload(WastageEntry.location)).order_by(WastageEntry.id)
    return [(str(e.id), {"Store 店铺": _store(e.location.name), "Date Created 创建日期": _ms(e.entry_date), "Product 产品": e.product.name, "Category 类别": e.category or None,
                         "Supplier 供应商": e.supplier_name or None, "Quantity 数量": e.qty, "Reason 原因": e.reason}) for e in db.scalars(q)]


def rows_dashboard(db):
    """One row per monitored location with the headline numbers, for the charts on the Lark dashboard. Today's sales are partial until the day ends."""
    today = today_local()
    locs = _monitored(db)
    out = []
    selling = set(db.scalars(select(SalesDayProduct.product_id).where(SalesDayProduct.business_date > today - timedelta(days=30), SalesDayProduct.units > 0).distinct()))
    for lid, name in locs.items():
        def day(d):
            return db.get(SalesDay, (lid, d))
        t, y = day(today), day(today - timedelta(days=1))
        week = db.scalar(select(func.coalesce(func.sum(SalesDay.net_sales), 0)).where(SalesDay.location_id == lid, SalesDay.business_date > today - timedelta(days=7), SalesDay.business_date <= today)) or 0
        inv = db.execute(select(InventoryCurrent.product_id, InventoryCurrent.on_hand, InventoryCurrent.warning_stock).join(Product, Product.id == InventoryCurrent.product_id)
                         .where(InventoryCurrent.location_id == lid, Product.track_stock.is_(True), Product.in_api.is_(True))).all()
        out_of_stock = sum(1 for pid, oh, _ in inv if oh <= 0 and pid in selling)  # only products that sold in the last 30 days: zero stock on a dormant item is not news
        low = sum(1 for pid, oh, w in inv if oh > 0 and w and oh <= w)
        exp = db.scalar(select(func.count()).select_from(ExpiryBatch).where(ExpiryBatch.location_id == lid, ExpiryBatch.status == "active",
                                                                              ExpiryBatch.expiry_date <= today + timedelta(days=7))) or 0
        issues = db.scalar(select(func.count()).select_from(Issue).where(Issue.location_id == lid, Issue.status.in_(("open", "acknowledged", "in_progress")))) or 0
        out.append((str(lid), {"Store 门店": name, "Today sales 今日销售": round(t.net_sales, 2) if t else 0.0, "Yesterday sales 昨日销售": round(y.net_sales, 2) if y else 0.0,
                               "Last 7 days sales 近7天销售": round(week, 2), "Transactions today 今日交易": t.tx_count if t else 0, "Out of stock 缺货": out_of_stock,
                               "Low stock 低库存": low, "Expiring in 7 days 7天内到期": exp, "Open issues 未解决问题": issues}))
    return out


_INSPECTION_COLUMNS = (("entrance", "Store Entrance"), ("counter", "Counter Area"), ("restroom", "Restroom"), ("floor", "Floors & Tables"),
                       ("machine", "Machines"), ("shelf", "Shelfs"), ("video", "Video Walkthrough"))


def rows_daily_inspection(db):
    """One row per completed Store Daily Inspection; each photo goes into the column of the checklist item it was taken for."""
    locs = {l.id: _store(l.name) for l in db.scalars(select(Location))}
    q = (select(Inspection).join(InspectionTemplate, InspectionTemplate.id == Inspection.template_id)
         .where(InspectionTemplate.code == "store_daily", Inspection.status.in_(("completed", "late"))).order_by(Inspection.id))
    out = []
    for i in db.scalars(q):
        fields: dict[str, Any] = {"Store 店铺": locs.get(i.location_id, ""), "Date 日期": _ms(i.business_date)}
        for r in db.scalars(select(InspectionResult).where(InspectionResult.inspection_id == i.id).options(selectinload(InspectionResult.item))):
            label = r.item.label.lower()
            col = next((c for key, c in _INSPECTION_COLUMNS if key in label), None)
            files = list(r.videos) if r.item.kind == "video" else list(r.photos)
            if col and files:
                fields[col] = Files(files)
        out.append((str(i.id), fields))
    return out


def rows_staff(db):
    """Fills the existing 员工名录 Staff Directory layout (store and position are not known to StoreHub, so they stay for people to fill in)."""
    return [(str(e.id), {"Name 姓名": e.name, "Phone 电话": e.phone or None, "Email Address  电子邮件": e.email or None, "Status 地位": "Active"}) for e in db.scalars(select(Employee))]


def rows_recs(db):
    locs, users = _names(db)
    q = select(Recommendation).where(Recommendation.status.in_(("approved", "modified", "rejected", "ordered"))).options(selectinload(Recommendation.product), selectinload(Recommendation.supplier))
    return [(str(r.id), {"Key": f"rec-{r.id}", "Date": _ms(r.business_date), "Location": locs.get(r.location_id, ""), "Product": r.product.name,
                         "Supplier": r.supplier.name if r.supplier else "", "Suggested": r.final_qty, "Approved": r.approved_qty or 0, "Status": r.status,
                         "Reason": r.reason_code or "", "Value": r.value, "Confidence": r.confidence, "Decided by": users.get(r.decided_by, ""),
                         "Decided at": _ms(r.decided_at)}) for r in db.scalars(q)]


def rows_inspections(db):
    locs, users = _names(db)
    q = select(Inspection).where(Inspection.status.in_(("completed", "late", "missed"))).options(selectinload(Inspection.template))
    return [(str(i.id), {"Key": f"insp-{i.id}", "Date": _ms(i.business_date), "Location": locs.get(i.location_id, ""), "Checklist": i.template.name, "Status": i.status,
                         "Failed items": i.fail_count, "Completed by": users.get(i.completed_by, ""), "Completed at": _ms(i.completed_at)}) for i in db.scalars(q)]


def rows_receiving(db):
    locs, users = _names(db)
    q = select(Receiving).where(Receiving.status != "open").options(selectinload(Receiving.po), selectinload(Receiving.lines))
    return [(str(r.id), {"Key": f"recv-{r.id}", "PO number": r.po.sh_po_id, "Location": locs.get(r.location_id, ""), "Status": r.status, "Lines": len(r.lines),
                         "Lines with differences": sum(1 for l in r.lines if (l.counted_qty or 0) != l.ordered_qty or l.damaged_qty > 0), "Completed at": _ms(r.completed_at)})
            for r in db.scalars(q)]


def rows_expiry(db):
    q = select(ExpiryBatch).options(selectinload(ExpiryBatch.product), selectinload(ExpiryBatch.location))
    return [(str(b.id), {"Key": f"exp-{b.id}", "Product": b.product.name, "Location": b.location.name, "Quantity": b.qty, "Expiry": _ms(b.expiry_date), "Status": b.status,
                         "Batch": b.batch_no or ""}) for b in db.scalars(q)]


SPECS = [
    Spec("product", "产品总表 Product List", [("Distributor  经销商", TEXT), ("Contact No", TEXT), ("Category 类别", TEXT), ("Product Name 产品名称", TEXT)], rows_products),
    Spec("inventory", "存货 Inventory", [("Product 产品", TEXT), ("Category 类别", TEXT), ("Store 门店", TEXT), ("Barcode 条形码", TEXT), ("Qty 数量", NUMBER)], rows_inventory),
    Spec("sales_day", "销售量 Sales", [("Store 门店", TEXT), ("Date", DATE), ("Cash", NUMBER), ("GCash", NUMBER), ("Daily Sales", NUMBER), ("Transactions", NUMBER)], rows_sales),
    Spec("purchase_order", "采购订单 Purchase Order", [("PO # 采购订单号", TEXT), ("Supplier 供应商", TEXT), ("Deliver to 配送至", TEXT), ("Product 产品", TEXT), ("Quantity 数量", TEXT),
                                                      ("Amount 数量", TEXT), ("Total Amount 总金额", TEXT)], rows_purchase_orders),
    Spec("stock_transfer", "股份转让 Stock Transfer", [("ST # 股份转让编号", TEXT), ("Supplier 供应商", TEXT), ("Deliver to 配送至", TEXT), ("Product 产品", TEXT), ("Quantity 数量", TEXT),
                                                      ("Amount 数量", TEXT), ("Total Amount 总金额", TEXT)], rows_stock_transfers),
    Spec("wastage", "损耗记录 Wastage Record", [("Store 店铺", TEXT), ("Date Created 创建日期", DATE), ("Product 产品", TEXT), ("Category 类别", TEXT), ("Supplier 供应商", TEXT),
                                               ("Quantity 数量", NUMBER), ("Reason 原因", TEXT)], rows_wastage),
    Spec("dashboard", "仪表板数据 Dashboard Data", [("Store 门店", TEXT), ("Today sales 今日销售", NUMBER), ("Yesterday sales 昨日销售", NUMBER), ("Last 7 days sales 近7天销售", NUMBER),
                                                  ("Transactions today 今日交易", NUMBER), ("Out of stock 缺货", NUMBER), ("Low stock 低库存", NUMBER),
                                                  ("Expiring in 7 days 7天内到期", NUMBER), ("Open issues 未解决问题", NUMBER)], rows_dashboard),
    Spec("daily_inspection", "日常检查 Daily Inspection", [("Store 店铺", TEXT), ("Date 日期", DATE)], rows_daily_inspection, extra=[("Date 日期", DATE)]),
    Spec("staff", "员工名录 Staff Directory", [("Name 姓名", TEXT), ("Phone 电话", TEXT), ("Email Address  电子邮件", TEXT), ("Status 地位", TEXT)], rows_staff),
]
# Not synced for now (rows_issues, rows_recs, rows_inspections, rows_receiving, rows_expiry stay available): add a Spec for them when their Lark tables exist.


def _hash(fields: dict) -> str:
    return hashlib.sha1(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


def _clean(fields: dict) -> dict:
    return {k: v for k, v in fields.items() if v is not None}


def setup(client: LarkClient) -> dict[str, str]:
    """Create any missing tables in the Base and remember their ids. Existing tables are left untouched."""
    have = client.tables()
    ids = {}
    with SessionLocal() as db:
        for sp in SPECS:
            try:
                tid = have.get(sp.table) or client.create_table(sp.table, sp.fields)
            except LarkError as e:  # e.g. the app may edit but not manage the Base: the other tables still sync
                log.error("lark cannot create table %s: %s", sp.table, e)
                continue
            ids[sp.entity] = tid
            if sp.extra:
                try:
                    have_fields = client.field_names(tid)
                    for name, ftype in sp.extra:
                        if name not in have_fields:
                            client.add_field(tid, name, ftype)
                except LarkError as e:
                    log.error("lark cannot add columns to %s: %s", sp.table, e)
            row = db.get(Setting, f"lark.table.{sp.entity}") or Setting(key=f"lark.table.{sp.entity}", value=tid)
            row.value = tid
            db.add(row)
        db.commit()
    return ids


def mirror(client: LarkClient | None = None, batch_size: int = 100) -> dict:
    """Send new and changed records. Safe to run repeatedly; returns counts."""
    client = client or LarkClient()
    if not client.configured:
        return {"enabled": False}
    sent_new = sent_upd = errors = 0
    with SessionLocal() as db:
        known = {s.key.split(".", 2)[2] for s in db.scalars(select(Setting).where(Setting.key.like("lark.table.%")))}
    if any(sp.entity not in known for sp in SPECS):  # first run, or a new table was added: create what is missing, no manual step needed
        try:
            setup(client)
        except LarkError as e:
            log.error("lark setup failed: %s", e)
            return {"enabled": True, "created": 0, "updated": 0, "errors": 1}
    with SessionLocal() as db:
        tables = {s.key.split(".", 2)[2]: s.value for s in db.scalars(select(Setting).where(Setting.key.like("lark.table.%")))}
        for sp in SPECS:
            tid = tables.get(sp.entity)
            if not tid:
                continue
            links = {l.entity_id: l for l in db.scalars(select(LarkLink).where(LarkLink.entity_type == sp.entity, LarkLink.lark_table == tid))}
            new, upd = [], []
            all_rows = sp.rows(db)
            try:
                fm = client.field_map(tid, {k for _, f in all_rows for k in f})
            except LarkError as e:
                errors += 1
                log.error("lark fields %s failed: %s", sp.entity, e)
                continue
            for eid, fields in all_rows:
                fields = _clean({k: v for k, v in fields.items() if k in fm})  # hashed under our own names, so a rename in Lark does not look like a change
                h = _hash(fields)
                link = links.get(eid)
                if link is None:
                    new.append((eid, fields, h))
                elif link.content_hash != h:
                    upd.append((link, fields, h))

            def payload(f, fm=fm):
                return _resolve_files(client, {fm[k]: v for k, v in f.items()})

            def create(chunk):
                """Whole batch first; if Lark refuses it, one by one, so a single bad record (say a value its dropdown cannot take) does not hold back the others."""
                try:
                    rids = client.batch_create(tid, [payload(f) for _, f, _ in chunk])
                    if len(rids) != len(chunk):
                        raise LarkError("Lark returned a different number of records than were sent")
                    return list(zip(chunk, rids))
                except LarkError:
                    done = []
                    for item in chunk:
                        try:
                            done.append((item, client.batch_create(tid, [payload(item[1])])[0]))
                        except LarkError as e:
                            log.error("lark mirror %s: record %s skipped: %s", sp.entity, item[0], e)
                    return done

            def update(chunk):
                try:
                    client.batch_update(tid, [(l.lark_record_id, payload(f)) for l, f, _ in chunk])
                    return chunk
                except LarkError:
                    done = []
                    for item in chunk:
                        try:
                            client.batch_update(tid, [(item[0].lark_record_id, payload(item[1]))])
                            done.append(item)
                        except LarkError as e:
                            if "1254043" in str(e):  # RecordIdNotFound: the row was deleted in Lark; write it again instead of failing every 5 minutes
                                try:
                                    item[0].lark_record_id = client.batch_create(tid, [payload(item[1])])[0]
                                    done.append(item)
                                    continue
                                except LarkError as e2:
                                    e = e2
                            log.error("lark mirror %s: record %s skipped: %s", sp.entity, item[0].entity_id, e)
                    return done

            try:
                for i in range(0, len(new), batch_size):
                    chunk = new[i:i + batch_size]
                    made = create(chunk)
                    for (eid, _, h), rid in made:
                        db.add(LarkLink(entity_type=sp.entity, entity_id=eid, lark_table=tid, lark_record_id=rid, content_hash=h))
                    db.commit()  # link rows are saved right after Lark confirms, so a crash cannot lose them
                    sent_new += len(made)
                    errors += 1 if len(made) < len(chunk) else 0
                for i in range(0, len(upd), batch_size):
                    chunk = upd[i:i + batch_size]
                    done = update(chunk)
                    for l, _, h in done:
                        l.content_hash, l.synced_at = h, now_utc()
                    db.commit()
                    sent_upd += len(done)
                    errors += 1 if len(done) < len(chunk) else 0
            except LarkError as e:
                db.rollback()
                errors += 1
                log.error("lark mirror %s failed: %s", sp.entity, e)
    return {"enabled": True, "created": sent_new, "updated": sent_upd, "errors": errors}


def forward_alerts(client: LarkClient | None = None, chat_id: str | None = None, limit: int = 10) -> dict:
    """Send new critical and warning notifications to a Lark chat (LARK_ALERT_CHAT_ID). Each notification is sent once."""
    from .models import Notification
    client = client or LarkClient()
    chat_id = chat_id or get_config().lark_alert_chat_id
    if not (client.cfg.lark_app_id and client.cfg.lark_app_secret and chat_id):
        return {"enabled": False}
    sent = errors = 0
    with SessionLocal() as db:
        rows = list(db.scalars(select(Notification).where(Notification.lark_sent_at.is_(None), Notification.level.in_(("critical", "warning")),
                                                          Notification.created_at >= now_utc().replace(hour=0, minute=0, second=0)).order_by(Notification.id).limit(limit)))
        for n in rows:
            try:
                text = f"[{n.level.upper()}] {n.title}" + ("\n" + n.body if n.body else "")
                base = client.cfg.base_url.rstrip("/")
                if n.link and base:  # a tap in Lark opens the exact screen (design principle: meet people where they work)
                    text += f"\n{base}{n.link}"
                client.send_text(chat_id, text)
                n.lark_sent_at = now_utc()
                db.commit()
                sent += 1
            except LarkError as e:
                db.rollback()
                errors += 1
                log.error("lark alert failed: %s", e)
                break
    return {"enabled": True, "sent": sent, "errors": errors}
