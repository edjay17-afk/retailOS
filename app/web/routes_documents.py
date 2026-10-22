"""Saved edits, print layouts and verified StoreHub item imports."""
import json
import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ..models import Product, PurchaseOrder, StockTransfer
from ..services import order_documents as docs
from .deps import Ctx, need, render
from .routes_transfers import visible

router = APIRouter()


def load(c, kind, doc_id):
    if kind == "po":
        source = c.db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == doc_id).options(selectinload(PurchaseOrder.lines)))
        if source:
            c.check_loc(source.location_id)
    else:
        source = c.db.scalar(select(StockTransfer).where(StockTransfer.id == doc_id, visible(c)).options(
            selectinload(StockTransfer.lines), selectinload(StockTransfer.last_import)))
    if not source:
        raise HTTPException(404)
    return source


def register(kind, prefix, view_permission, edit_permission):
    def editor(doc_id: int, c: Ctx = Depends(need(edit_permission))):
        source = load(c, kind, doc_id)
        return render(c, "order_document_edit.html", doc=docs.document(c.db, source, kind), kind=kind,
                      base=f"{prefix}/{doc_id}", exact=docs.display)

    async def save(doc_id: int, request: Request, c: Ctx = Depends(need(edit_permission))):
        source = load(c, kind, doc_id)
        # Bound body before JSON parsing; individual fields/rows are bounded in the service.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2_000_000:
                raise HTTPException(413, c.t("This document is too large."))
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, c.t("Check the document fields and try again."))
        if not isinstance(payload, dict):
            raise HTTPException(400)
        c.check_csrf(payload.get("csrf", "") if isinstance(payload.get("csrf", ""), str) else "")
        revision = payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or not 0 <= revision < 2**31:
            raise HTTPException(400)
        try:
            version = docs.save(c.db, source, kind, payload, revision, c.user)
        except docs.Invalid as e:
            raise HTTPException(400, c.t(str(e)))
        except docs.Conflict as e:
            raise HTTPException(409, c.t(str(e)))
        return {"revision": version, "message": c.t("Changes saved. You can now print or export.")}

    def print_document(doc_id: int, c: Ctx = Depends(need(view_permission))):
        source = load(c, kind, doc_id)
        return render(c, "order_document_print.html", doc=docs.document(c.db, source, kind), kind=kind,
                      base=f"{prefix}/{doc_id}", exact=docs.display, cents=docs.cents, six=docs.six_decimals, print_date=docs.print_date)

    def export_document(doc_id: int, mode: str = "storehub", c: Ctx = Depends(need(view_permission))):
        if mode not in ("storehub", "report"):
            raise HTTPException(400)
        source = load(c, kind, doc_id)
        d = docs.document(c.db, source, kind)
        try:
            raw = docs.export(d, kind, mode)
        except docs.Invalid as e:
            raise HTTPException(400, c.t(str(e)))
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", d["header"]["reference"]) or "document"
        return Response(raw, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}_{mode}.csv"'})

    def products(doc_id: int, q: str = "", c: Ctx = Depends(need(edit_permission))):
        load(c, kind, doc_id)
        q = q.strip()[:100]
        if len(q) < 2:
            return []
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = c.db.scalars(select(Product).where(Product.in_api.is_(True), Product.is_parent.is_(False),
                         or_(Product.name.ilike(pattern, escape="\\"), Product.sku.ilike(pattern, escape="\\"),
                             Product.storehub_id.ilike(pattern, escape="\\"))).order_by(Product.name).limit(20))
        return [dict(name=p.name, sku=p.sku or "", product_id=p.storehub_id, cost=docs.plain(Decimal(str(p.cost or 0)))) for p in rows]

    for suffix, endpoint, methods in (("edit", editor, ["GET"]), ("edit", save, ["POST"]),
                                       ("print", print_document, ["GET"]), ("export", export_document, ["GET"]),
                                       ("products", products, ["GET"])):
        router.add_api_route(f"{prefix}/{{doc_id}}/{suffix}", endpoint, methods=methods, name=f"{kind}_{endpoint.__name__}")


register("po", "/purchase-orders", "po.view", "po.view")
register("transfer", "/stock-transfers", "transfer.view", "transfer.import")
