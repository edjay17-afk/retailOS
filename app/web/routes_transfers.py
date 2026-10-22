"""Stock-transfer export mirror, independent of physical dispatch/receiving workflows."""
from __future__ import annotations

import csv
import io
import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .. import settings_service as cfg
from ..models import StockTransfer, StockTransferImport, StockTransferLine, StockTransferReceipt, StockTransferReceiptLine
from ..services import stock_transfers, transfer_inbox, transfers
from ..services import order_documents
from ..timeutil import now_utc, fmt_local
from .deps import Ctx, need, parse_float, redirect, render

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
def transfer_list(q: str = "", status: str = "", loc: int = 0, page: int = 1, export: str = "", c: Ctx = Depends(need("transfer.view"))):
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
    if export == "csv":
        rows = c.db.scalars(stmt.order_by(StockTransfer.created_sh.desc(), StockTransfer.id.desc())
                            .options(selectinload(StockTransfer.lines), selectinload(StockTransfer.last_import)))
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Transfer ID", "From", "To", "StoreHub status", "Created (Philippine time)",
                         "Items", "Total quantity", "Export total (PHP)", "Imported (Philippine time)"])
        def cell(value):
            # Keep imported names/references from becoming spreadsheet formulas.
            return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
        for row in rows:
            writer.writerow([cell(row.reference), cell(row.source_name), cell(row.target_name), row.status,
                             fmt_local(row.created_sh), len(row.lines), sum(line.quantity for line in row.lines),
                             f"{row.total:.2f}" if row.total is not None else "", fmt_local(row.last_import.imported_at)])
        return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="stock-transfers.csv"'})
    total = c.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = min(max(page, 1), max(1, (total + PAGE - 1) // PAGE))
    rows = list(c.db.scalars(stmt.order_by(StockTransfer.created_sh.desc(), StockTransfer.id.desc()).offset((page-1)*PAGE).limit(PAGE)
                            .options(selectinload(StockTransfer.lines), selectinload(StockTransfer.last_import))))
    counts = dict(c.db.execute(select(StockTransfer.status, func.count()).where(scope).group_by(StockTransfer.status)).all())
    receipts = {r.transfer_id: r for r in c.db.scalars(select(StockTransferReceipt).where(StockTransferReceipt.transfer_id.in_([t.id for t in rows] or [0])))}
    last_import = transfer_inbox.last_import_at(c.db)
    return render(c, "stock_transfers.html", rows=rows, q=q, status=status, loc=loc, page=page, total=total, per=PAGE,
                  locations=c.locations(), statuses=stock_transfers.STATUSES, counts=counts, receipts=receipts,
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
    receipt = c.db.scalar(select(StockTransferReceipt).where(StockTransferReceipt.transfer_id == rec.id)
                          .options(selectinload(StockTransferReceipt.lines).selectinload(StockTransferReceiptLine.transfer_line),
                                   selectinload(StockTransferReceipt.counter)))
    can_confirm = (receipt is None and rec.status != "Cancelled" and rec.lines
                   and c.can("transfer.confirm") and rec.target_location_id in (c.location_ids or []))
    return render(c, "stock_transfer_detail.html", rec=rec, receipt=receipt, can_confirm=can_confirm,
                  document=order_documents.document(c.db, rec, "transfer"),
                  original_document=order_documents.calculate(order_documents.initial(c.db, rec, "transfer")),
                  document_base=f"/stock-transfers/{rec.id}", kind="transfer", exact=order_documents.display)


@router.post("/stock-transfers/{transfer_id}/confirm")
async def transfer_confirm(transfer_id: int, request: Request, c: Ctx = Depends(need("transfer.confirm"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    rec = c.db.scalar(select(StockTransfer).where(StockTransfer.id == transfer_id, visible(c)).options(selectinload(StockTransfer.lines)))
    if rec is None:
        raise HTTPException(404)
    if not rec.target_location_id or rec.target_location_id not in (c.location_ids or []):
        raise HTTPException(403, "Only the receiving store can confirm this transfer")
    counts = {l.id: parse_float(form.get(f"q_{l.id}")) for l in rec.lines}
    try:
        receipt = transfers.confirm_store_receipt(c.db, c.user, rec, counts, str(form.get("note", "")) or None)
        c.db.commit()
    except transfers.TransferError as e:
        c.db.rollback()
        return redirect(f"/stock-transfers/{transfer_id}", err=str(e))
    ok = c.t("Receipt confirmed — matches what was sent.") if receipt.differences == 0 else \
        c.t("Receipt confirmed with {n} difference(s); the warehouse has been notified.", n=receipt.differences)
    return redirect(f"/stock-transfers/{transfer_id}", ok=ok)
