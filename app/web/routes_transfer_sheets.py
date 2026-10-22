"""Warehouse transfer sheet: scan a product, type the quantity, save — instead of writing the list in a notebook and retyping it into StoreHub."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_config
from ..models import Product, TransferSheet, TransferSheetLine
from ..services import new_product_lists as npl
from ..services import product_push
from ..services import transfer_sheets as ts
from .deps import Ctx, need, redirect, render

router = APIRouter()
PERM = "transfer.sheet"


def _sheet(c: Ctx, sid: int) -> TransferSheet:
    s = c.db.scalar(select(TransferSheet).where(TransferSheet.id == sid).options(
        selectinload(TransferSheet.lines).selectinload(TransferSheetLine.product), selectinload(TransferSheet.source),
        selectinload(TransferSheet.target), selectinload(TransferSheet.creator)))
    if s is None:
        raise HTTPException(404)
    return s


def _lines(c: Ctx, s: TransferSheet) -> dict:
    stock = ts.on_hand(c.db, s.source_location_id, [l.product_id for l in s.lines if l.product_id])
    sm = ts.summary(s)
    return {"lines": [{"id": l.id, "product_id": l.product_id, "name": ts.line_name(l), "barcode": ts.line_barcode(l), "sku": (l.product.sku or "") if l.product else "",
                       "qty": l.qty, "on_hand": stock.get(l.product_id)}
                      for l in sorted(s.lines, key=lambda x: -x.id)],
            "total_lines": sm["lines"], "total_qty": sm["qty"]}


@router.get("/transfer-sheets")
def sheets_page(c: Ctx = Depends(need(PERM))):
    rows = list(c.db.scalars(select(TransferSheet).where(TransferSheet.status != "cancelled").order_by(TransferSheet.id.desc()).limit(60).options(
        selectinload(TransferSheet.lines), selectinload(TransferSheet.target), selectinload(TransferSheet.creator))))
    return render(c, "transfer_sheets.html", rows=rows, stores=ts.stores(c.db), warehouse=ts.warehouse(c.db))


@router.post("/transfer-sheets/new")
def sheet_new(target_id: int = Form(0), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    try:
        s = ts.create(c.db, c.user, target_id)
        c.db.commit()
    except ts.SheetError as e:
        c.db.rollback()
        return redirect("/transfer-sheets", err=c.t(str(e)))
    return redirect(f"/transfer-sheets/{s.id}")


@router.get("/transfer-sheets/catalog.json")
def catalog(c: Ctx = Depends(need(PERM))):
    """All StoreHub products in one list: the page keeps it in memory and answers every scan on the spot, without asking the server."""
    wh = ts.warehouse(c.db)
    return JSONResponse(ts.catalog(c.db, wh.id if wh else None), headers={"Cache-Control": "private, max-age=120"})


@router.get("/transfer-sheets/{sid}")
def sheet_page(sid: int, c: Ctx = Depends(need(PERM))):
    s = _sheet(c, sid)
    return render(c, "transfer_sheet.html", sheet=s, data=_lines(c, s), summary=ts.summary(s), file_stem=ts.file_stem(s),
                  categories=npl.categories(c.db), crumb=f"TS-{s.id:05d}")


@router.post("/transfer-sheets/{sid}/quick-product")
def sheet_quick_product(sid: int, barcode: str = Form(""), name: str = Form(""), category: str = Form(""), price: str = Form(""), cost: str = Form(""),
                        csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    """The scanned product is not in StoreHub yet: create it there on the spot, so it can go straight onto the sheet.
    Everyone who writes transfer sheets also holds the product.push right, so no extra check is needed."""
    c.check_csrf(csrf)
    _sheet(c, sid)
    try:
        p = product_push.quick_create(c.db, c.user, barcode=barcode, name=name, category=category, price=price or None, cost=cost or None,
                                      origin="transfer_sheet")
    except product_push.PushError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e).split(":")[0]) + (":" + str(e).split(":", 1)[1] if ":" in str(e) else "")}, status_code=400)
    return JSONResponse({"ok": True, "product": [p.id, p.name, p.sku or "", [b.strip() for b in (p.barcode or "").split(",") if b.strip()], None]})


@router.get("/transfer-sheets/{sid}/find")
def sheet_find(sid: int, code: str = "", c: Ctx = Depends(need(PERM))):
    """The scan, asked of the server (the page normally answers from its own copy of the product list): one exact product, or a short list for a typed name."""
    s = _sheet(c, sid)
    exact, matches = ts.find(c.db, code)
    pick = [exact] if exact else matches
    stock = ts.on_hand(c.db, s.source_location_id, [p.id for p in pick])
    on_sheet = {l.product_id: l.qty for l in s.lines}
    card = lambda p: {"id": p.id, "name": p.name, "barcode": p.barcode or "", "sku": p.sku or "", "on_hand": stock.get(p.id), "on_sheet": on_sheet.get(p.id, 0)}
    return JSONResponse({"exact": card(exact) if exact else None, "matches": [card(p) for p in matches], "code": code.strip()})


@router.post("/transfer-sheets/{sid}/add")
def sheet_add(sid: int, product_id: int = Form(0), qty: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    """Add a StoreHub product. A transfer cannot hold a product StoreHub does not have."""
    c.check_csrf(csrf)
    s = _sheet(c, sid)
    try:
        p = c.db.get(Product, product_id)
        if p is None:
            raise ts.SheetError("Scan the product first")
        ts.add_line(c.db, s, p, qty)
        c.db.commit()
    except ts.SheetError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e))}, status_code=400)
    return JSONResponse({"ok": True, **_lines(c, _sheet(c, sid))})


@router.post("/transfer-sheets/{sid}/line/{lid}")
def sheet_line(sid: int, lid: int, qty: str = Form(""), remove: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    s = _sheet(c, sid)
    try:
        if remove:
            ts.remove_line(c.db, s, lid)
        else:
            ts.set_qty(c.db, s, lid, qty)
        c.db.commit()
    except ts.SheetError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e))}, status_code=400)
    return JSONResponse({"ok": True, **_lines(c, _sheet(c, sid))})


@router.post("/transfer-sheets/{sid}/submit")
def sheet_submit(sid: int, csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    s = _sheet(c, sid)
    try:
        ts.submit(c.db, c.user, s)
        c.db.commit()
    except ts.SheetError as e:
        c.db.rollback()
        return redirect(f"/transfer-sheets/{sid}", err=c.t(str(e)))
    return redirect(f"/transfer-sheets/{sid}", ok=c.t("Saved. Download the StoreHub import file below."))


@router.post("/transfer-sheets/{sid}/cancel")
def sheet_cancel(sid: int, csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    s = _sheet(c, sid)
    try:
        ts.cancel(c.db, c.user, s)
        c.db.commit()
    except ts.SheetError as e:
        c.db.rollback()
        return redirect(f"/transfer-sheets/{sid}", err=c.t(str(e)))
    return redirect("/transfer-sheets", ok=c.t("Sheet discarded."))


@router.post("/transfer-sheets/{sid}/delete")
def sheet_delete(sid: int, csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    s = _sheet(c, sid)
    name = f"TS-{s.id:05d}"
    ts.delete(c.db, c.user, s)
    c.db.commit()
    return redirect("/transfer-sheets", ok=c.t("Deleted") + " " + name)


@router.post("/transfer-sheets/{sid}/storehub")
def sheet_storehub(sid: int, reference: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    s = _sheet(c, sid)
    try:
        ts.mark_in_storehub(c.db, c.user, s, reference)
        c.db.commit()
    except ts.SheetError as e:
        c.db.rollback()
        return redirect(f"/transfer-sheets/{sid}", err=c.t(str(e)))
    return redirect(f"/transfer-sheets/{sid}", ok=c.t("Marked as entered in StoreHub."))


def _file(c: Ctx, sid: int) -> TransferSheet:
    s = _sheet(c, sid)
    if s.status == "draft":
        raise HTTPException(404)
    return s


def _csv(text: str, name: str) -> Response:
    return Response(text.encode("utf-8"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/transfer-sheets/{sid}/download.csv")
def sheet_csv(sid: int, c: Ctx = Depends(need(PERM))):
    """StoreHub's own stock-transfer import template, filled in."""
    s = _file(c, sid)
    return _csv(ts.csv_text(s), ts.file_stem(s) + ".csv")


@router.get("/transfer-sheets/{sid}/download.xlsx")
def sheet_xlsx(sid: int, c: Ctx = Depends(need(PERM))):
    s = _file(c, sid)
    if not s.excel_path:
        raise HTTPException(404)
    path = (get_config().data_dir / s.excel_path).resolve()
    if not path.is_file() or path.parent != (get_config().data_dir / ts.RPA_DIR).resolve():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name)
