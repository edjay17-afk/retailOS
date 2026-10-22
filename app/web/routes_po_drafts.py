"""Write a purchase order: supplier and delivery first, then products with quantity and supplier price (exact decimals), save, export for StoreHub.

Registered before the StoreHub purchase-order pages so /purchase-orders/new is not read as a PO number."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import settings_service as cfg
from ..config import get_config
from ..models import PoDraft, PoDraftLine, Product
from ..services import order_documents as docs
from ..services import po_drafts as pod
from ..services import product_push
from .deps import Ctx, need, redirect, render

router = APIRouter()
PERM = "po.create"
BASE = "/purchase-orders/new"


def _warehouse(c: Ctx) -> int:
    """Everything is delivered to the warehouse first and transferred to the stores from there."""
    locs = c.locations()
    w = next((l for l in locs if l.type == "warehouse"), None)
    if w is None:
        raise HTTPException(409, c.t("No warehouse is set up in the system"))
    return w.id


def _draft(c: Ctx, did: int) -> PoDraft:
    d = c.db.scalar(select(PoDraft).where(PoDraft.id == did).options(
        selectinload(PoDraft.lines).selectinload(PoDraftLine.product), selectinload(PoDraft.supplier), selectinload(PoDraft.location), selectinload(PoDraft.creator)))
    if d is None or (d.created_by != c.user.id and not c.can("users.manage")):  # an order is private to the person who wrote it (and the administrators)
        raise HTTPException(404)
    return d


def _data(d: PoDraft) -> dict:
    lines = [{"id": l.id, "product_id": l.product_id, "name": l.product.name, "barcode": l.product.barcode or "", "sku": l.product.sku or "",
              "qty": l.qty, "price": l.price, "total": pod.total_of(l.qty, l.price)} for l in sorted(d.lines, key=lambda x: -x.id)]
    sm = pod.summary(d)
    return {"lines": lines, "total_lines": len(lines), "subtotal": sm["subtotal"], "tax": sm["tax"], "total": sm["total"], "rate": sm["rate"]}


@router.get(BASE)
def page(saved: str = "", c: Ctx = Depends(need(PERM))):
    d = pod.current(c.db, c.user)
    if d is not None:
        d = _draft(c, d.id)
    mine = select(PoDraft).where(PoDraft.status == "saved").order_by(PoDraft.id.desc()).limit(30).options(
        selectinload(PoDraft.lines).selectinload(PoDraftLine.product), selectinload(PoDraft.supplier), selectinload(PoDraft.location))
    if not c.can("users.manage"):
        mine = mine.where(PoDraft.created_by == c.user.id)
    done = list(c.db.scalars(mine))
    wh = next((l for l in c.locations() if l.type == "warehouse"), None)
    from ..services import new_product_lists as npl
    return render(c, "po_draft.html", draft=d, data=_data(d) if d else {"lines": [], "total_lines": 0, "subtotal": "0", "tax": "0", "total": "0", "rate": "12"}, suppliers=pod.suppliers(c.db), warehouse=wh, saved_lists=done, show_saved=bool(saved), ref=pod.reference, show=pod.show, sums={s.id: pod.summary(s) for s in done}, categories=npl.categories(c.db))


@router.post(f"{BASE}/start")
def start(supplier: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    try:
        pod.start(c.db, c.user, supplier, _warehouse(c), [l.id for l in c.locations()], docs.plain(Decimal(str(cfg.get(c.db, "product.vat_rate") or 12))))
        c.db.commit()
    except pod.PoError as e:
        c.db.rollback()
        return redirect(BASE, err=c.t(str(e)))
    return redirect(BASE)


@router.post(f"{BASE}/header")
def header(supplier: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    d = pod.current(c.db, c.user)
    if d is None:
        return redirect(BASE)
    try:
        pod.change(c.db, c.user, d, supplier, _warehouse(c), [l.id for l in c.locations()])
        c.db.commit()
    except pod.PoError as e:
        c.db.rollback()
        return redirect(BASE, err=c.t(str(e)))
    return redirect(BASE)


@router.get(f"{BASE}/catalog.json")
def catalog(c: Ctx = Depends(need(PERM))):
    """All StoreHub products in one list (with the price this supplier was last paid): the page answers every scan and search in the browser."""
    d = pod.current(c.db, c.user)
    if d is None:
        return JSONResponse([])
    return JSONResponse(pod.catalog(c.db, d.supplier_id), headers={"Cache-Control": "no-store"})


def _mine(c: Ctx) -> PoDraft:
    d = pod.current(c.db, c.user)
    if d is None:
        raise HTTPException(404)
    return _draft(c, d.id)


@router.post(f"{BASE}/add")
def add(product_id: int = Form(0), qty: str = Form(""), price: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    d = _mine(c)
    try:
        p = c.db.get(Product, product_id)
        if p is None or not p.in_api:
            raise pod.PoError("Choose a product from StoreHub")
        pod.add_line(c.db, d, p, qty, price)
        c.db.commit()
    except pod.PoError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e))}, status_code=400)
    return JSONResponse({"ok": True, **_data(_draft(c, d.id))})


@router.post(f"{BASE}/quick-product")
def quick_product(barcode: str = Form(""), name: str = Form(""), category: str = Form(""), price: str = Form(""), cost: str = Form(""),
                  csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    """The scanned product is not in StoreHub yet: create it there on the spot, so it can go straight onto the order.
    Everyone who writes purchase orders also holds the product.push right, so no extra check is needed."""
    c.check_csrf(csrf)
    d = pod.current(c.db, c.user)
    try:
        p = product_push.quick_create(c.db, c.user, barcode=barcode, name=name, category=category, price=price or None, cost=cost or None,
                                      origin="purchase_order")
    except product_push.PushError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e).split(":")[0]) + (":" + str(e).split(":", 1)[1] if ":" in str(e) else "")}, status_code=400)
    if d is not None:  # its quantity on this order is split between the stores when the order is saved
        pod.mark_new_product(c.db, d, p.id)
        c.db.commit()
    suggested = "" if p.cost is None else repr(round(p.cost, 6)).rstrip("0").rstrip(".")
    return JSONResponse({"ok": True, "product": [p.id, p.name, p.sku or "", [b.strip() for b in (p.barcode or "").split(",") if b.strip()], suggested, None]})


@router.post(f"{BASE}/line/{{lid}}")
def line(lid: int, qty: str = Form(""), price: str = Form(""), remove: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    d = _mine(c)
    try:
        if remove:
            pod.remove_line(c.db, d, lid)
        else:
            pod.set_line(c.db, d, lid, qty, price)
        c.db.commit()
    except pod.PoError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e)), **_data(_draft(c, d.id))}, status_code=400)
    return JSONResponse({"ok": True, **_data(_draft(c, d.id))})


@router.post(f"{BASE}/save")
def save(csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    d = pod.current(c.db, c.user)
    if d is None:
        return redirect(BASE, err=c.t("Add at least one product before saving the order"))
    d = _draft(c, d.id)
    try:
        pod.save(c.db, c.user, d)
        sheets = list(getattr(d, "new_sheets", []))
        c.db.commit()
    except pod.PoError as e:
        c.db.rollback()
        return redirect(BASE, err=c.t(str(e)))
    msg = c.t("Saved. Download the StoreHub import file below.")
    if sheets:
        msg += " " + c.t("New products split to") + ": " + ", ".join(f"TS-{s.id:05d}" for s in sheets)
    return redirect(f"{BASE}/lists/{d.id}", ok=msg)


@router.post(f"{BASE}/discard")
def discard(csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    d = pod.current(c.db, c.user)
    if d is not None:
        pod.discard(c.db, c.user, _draft(c, d.id))
        c.db.commit()
    return redirect(BASE, ok=c.t("Order discarded."))


def _saved(c: Ctx, did: int) -> PoDraft:
    d = _draft(c, did)
    if d.status != "saved":
        raise HTTPException(404)
    return d


@router.get(f"{BASE}/lists/{{did}}")
def saved_page(did: int, c: Ctx = Depends(need(PERM))):
    d = _saved(c, did)
    return render(c, "po_draft_saved.html", draft=d, ref=pod.reference(d), show=pod.show, data=_data(d), file_stem=pod.file_stem(d), crumb=pod.reference(d))


@router.get(f"{BASE}/lists/{{did}}/download.csv")
def saved_csv(did: int, c: Ctx = Depends(need(PERM))):
    d = _saved(c, did)
    return Response(pod.csv_text(d).encode("utf-8"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{pod.file_stem(d)}.csv"'})


@router.get(f"{BASE}/lists/{{did}}/download.xlsx")
def saved_xlsx(did: int, c: Ctx = Depends(need(PERM))):
    d = _saved(c, did)
    if not d.excel_path:
        raise HTTPException(404)
    path = (get_config().data_dir / d.excel_path).resolve()
    if not path.is_file() or path.parent != (get_config().data_dir / pod.RPA_DIR).resolve():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name)


@router.get(f"{BASE}/lists/{{did}}/print")
def saved_print(did: int, c: Ctx = Depends(need(PERM))):
    d = _saved(c, did)
    return render(c, "order_document_print.html", doc=pod.document(d), kind="po", base=f"{BASE}/lists/{d.id}", exact=docs.display, cents=docs.cents, six=docs.six_decimals, print_date=docs.print_date)


@router.get(f"{BASE}/lists/{{did}}/edit")
def saved_edit(did: int, c: Ctx = Depends(need(PERM))):
    _saved(c, did)
    return RedirectResponse(f"{BASE}/lists/{did}", status_code=303)


@router.post(f"{BASE}/lists/{{did}}/delete")
def saved_delete(did: int, csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    d = _saved(c, did)
    name = pod.reference(d)
    pod.delete(c.db, c.user, d)
    c.db.commit()
    return redirect(f"{BASE}?saved=1", ok=c.t("Deleted") + " " + name)
