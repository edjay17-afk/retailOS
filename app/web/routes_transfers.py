"""Stock-transfer export mirror, independent of physical dispatch/receiving workflows."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .. import settings_service as cfg
from ..models import StockTransfer, StockTransferImport, StockTransferLine
from ..services import stock_transfers, transfer_inbox
from ..services import order_documents
from ..timeutil import now_utc
from .deps import Ctx, need, redirect, render

router = APIRouter()
PAGE = 50


def import_error(c, error):
    values = dict(error.values)
    if "column" in values:
        values["column"] = c.t(values["column"])
    return c.t(error.key, **values)


def visible(c):
    return or_(StockTransfer.source_location_id.in_(c.location_ids or [0]), StockTransfer.target_location_id.in_(c.location_ids or [0]))


@router.get("/stock-transfers")
def transfer_list(q: str = "", status: str = "", loc: int = 0, page: int = 1, c: Ctx = Depends(need("transfer.view"))):
    scope = visible(c)
    stmt = select(StockTransfer).where(scope)
    if status:
        if status not in stock_transfers.STATUSES:
            raise HTTPException(400, "Invalid transfer status")
        stmt = stmt.where(StockTransfer.status == status)
    if loc:
        c.check_loc(loc)
        stmt = stmt.where(or_(StockTransfer.source_location_id == loc, StockTransfer.target_location_id == loc))
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(StockTransfer.reference.ilike(pattern), StockTransfer.source_name.ilike(pattern), StockTransfer.target_name.ilike(pattern),
                              StockTransfer.lines.any(or_(StockTransferLine.product_name.ilike(pattern), StockTransferLine.sku.ilike(pattern)))))
    total = c.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = min(max(page, 1), max(1, (total + PAGE - 1) // PAGE))
    rows = list(c.db.scalars(stmt.order_by(StockTransfer.created_sh.desc(), StockTransfer.id.desc()).offset((page-1)*PAGE).limit(PAGE)
                            .options(selectinload(StockTransfer.lines), selectinload(StockTransfer.last_import))))
    counts = dict(c.db.execute(select(StockTransfer.status, func.count()).where(scope).group_by(StockTransfer.status)).all())
    last_import = transfer_inbox.last_import_at(c.db)
    return render(c, "stock_transfers.html", rows=rows, q=q, status=status, loc=loc, page=page, total=total, per=PAGE,
                  locations=c.locations(), statuses=stock_transfers.STATUSES, counts=counts,
                  last_import=last_import, import_stale=bool(last_import and now_utc() - last_import > timedelta(hours=float(cfg.get(c.db, "transfers.stale_hours")))))


@router.post("/stock-transfers/import/preview")
async def transfer_preview(file: UploadFile = File(...), csrf: str = Form(""), c: Ctx = Depends(need("transfer.import"))):
    c.check_csrf(csrf)
    filename = (file.filename or "").replace("\\", "/").split("/")[-1][:240]
    if not filename.lower().endswith(".csv"):
        return redirect("/stock-transfers", err=c.t("Choose the StoreHub stock-transfer CSV export."))
    raw = await file.read(stock_transfers.MAX_BYTES + 1)
    try:
        groups, counts = stock_transfers.plan(c.db, raw, c.location_ids)
    except stock_transfers.ImportError as e:
        return redirect("/stock-transfers", err=import_error(c, e))
    source = StockTransferImport(token=secrets.token_urlsafe(32), filename=filename, sha256=hashlib.sha256(raw).hexdigest(), raw_csv=raw, created_by=c.user.id)
    c.db.add(source)
    c.db.flush()
    return render(c, "stock_transfer_preview.html", source=source, groups=groups[:100], counts=counts, transfer_count=len(groups))


@router.post("/stock-transfers/import/{token}")
def transfer_import(token: str, csrf: str = Form(""), c: Ctx = Depends(need("transfer.import"))):
    c.check_csrf(csrf)
    source = c.db.scalar(select(StockTransferImport).where(StockTransferImport.token == token, StockTransferImport.created_by == c.user.id))
    if source is None:
        raise HTTPException(404)
    try:
        counts = stock_transfers.apply(c.db, source, c.user, c.location_ids)
        c.db.commit()  # include unique-constraint races in the handled transaction
    except stock_transfers.ImportError as e:
        c.db.rollback()
        return redirect("/stock-transfers", err=import_error(c, e))
    except IntegrityError:
        c.db.rollback()
        return redirect("/stock-transfers", err=c.t("Another import changed these transfers. Preview the export again."))
    return redirect("/stock-transfers", ok=c.t("Transfers imported: {new} new, {updated} updated, {unchanged} unchanged, {skipped} older snapshots skipped.", **counts))


@router.get("/stock-transfers/{transfer_id}")
def transfer_detail(transfer_id: int, c: Ctx = Depends(need("transfer.view"))):
    rec = c.db.scalar(select(StockTransfer).where(StockTransfer.id == transfer_id, visible(c))
                      .options(selectinload(StockTransfer.lines), selectinload(StockTransfer.last_import)))
    if rec is None:
        raise HTTPException(404)
    return render(c, "stock_transfer_detail.html", rec=rec, document=order_documents.document(c.db, rec, "transfer"),
                  original_document=order_documents.calculate(order_documents.initial(c.db, rec, "transfer")),
                  document_base=f"/stock-transfers/{rec.id}", kind="transfer", exact=order_documents.display)
