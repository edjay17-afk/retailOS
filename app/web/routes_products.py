"""New products: scan a barcode, type the name, pick supplier and category, give the quantity; many products go on one list, and saving it makes the Excel file for the RPA."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_config
from ..models import NewProductList, StoreHubPush, Product
from ..services import new_product_lists as npl
from ..services import new_products, product_push
from .deps import Ctx, need, redirect, render

router = APIRouter()
PERM = "product.add"


def _list(c: Ctx, lid: int) -> NewProductList:
    lst = c.db.scalar(select(NewProductList).where(NewProductList.id == lid).options(selectinload(NewProductList.items), selectinload(NewProductList.creator)))
    if lst is None or (lst.created_by != c.user.id and not (c.can("users.manage") or c.can("product.push"))):
        raise HTTPException(404)
    return lst


def _data(lst: NewProductList) -> dict:
    sm = npl.summary(lst)
    return {"lines": [{"id": i.id, "barcode": i.barcode, "name": i.name, "supplier": i.supplier_name, "category": i.category, "qty": i.qty,
                       "price": i.price, "cost": i.cost}
                      for i in sorted(lst.items, key=lambda x: -x.id)], "total_lines": sm["items"], "total_qty": sm["qty"]}


@router.get("/new-product")
def new_product_page(barcode: str = "", name: str = "", saved: str = "", c: Ctx = Depends(need(PERM))):
    show_saved = bool(saved)  # the query flag, read before the list below takes the name
    lst = npl.draft(c.db, c.user)
    q = select(NewProductList).where(NewProductList.status == "saved")
    if not c.can("product.push"):  # push roles watch every saved list (staff lists wait for their push); others see their own
        q = q.where(NewProductList.created_by == c.user.id)
    saved = list(c.db.scalars(q.order_by(NewProductList.id.desc()).limit(30).options(selectinload(NewProductList.items))))
    # Hide products confirmed absent by catalog sync, but retain the push audit record.
    removed = select(Product.id).where(Product.storehub_id == StoreHubPush.storehub_product_id,
                                       Product.in_api.is_(False)).exists()
    pq = select(StoreHubPush).where(~removed).options(selectinload(StoreHubPush.pusher))
    if not c.can("product.push"):  # others see what they pushed themselves
        pq = pq.where(StoreHubPush.pushed_by == c.user.id)
    pushes = list(c.db.scalars(pq.order_by(StoreHubPush.id.desc()).limit(50)))
    push_lists = {p.list_id for p in pushes if p.list_id}
    have_lists = set(c.db.scalars(select(NewProductList.id).where(NewProductList.id.in_(push_lists)))) if push_lists else set()
    return render(c, "new_product.html", lst=lst, data=_data(lst), saved=saved, suppliers=npl.suppliers(c.db), categories=npl.categories(c.db),
                  pushes=pushes, have_lists=have_lists,
                  show_saved=show_saved, barcode=barcode.strip(), prefill_name=name.strip()[:300])


@router.get("/new-product/check")
def new_product_check(barcode: str = "", c: Ctx = Depends(need(PERM))):
    """After a scan: is this barcode already a StoreHub product (the saved list, then StoreHub itself)? Not-yet-known barcodes also get a name from the internet, when found."""
    barcode = barcode.strip()
    if not barcode:
        return JSONResponse({"known": None})
    product, _ = new_products.find_product(c.db, barcode)
    if product is not None:
        return JSONResponse({"known": product.name})
    return JSONResponse({"known": None})


@router.get("/new-product/guess")
def new_product_guess(barcode: str = "", c: Ctx = Depends(need(PERM))):
    """Name / category for a barcode from the internet. Called by the page after the form is already on screen."""
    return JSONResponse(new_products.web_lookup(c.db, barcode.strip()) or {})


@router.post("/new-product/add")
def new_product_add(barcode: str = Form(""), name: str = Form(""), supplier: str = Form(""), category: str = Form(""), qty: str = Form(""),
                    price: str = Form(""), cost: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    lst = npl.draft(c.db, c.user)
    try:
        npl.add_item(c.db, lst, barcode=barcode, name=name, supplier=supplier, category=category, qty=qty, price=price, cost=cost)
        c.db.commit()
    except npl.ListError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e).split(":")[0]) + (":" + str(e).split(":", 1)[1] if ":" in str(e) else "")}, status_code=400)
    return JSONResponse({"ok": True, **_data(_list(c, lst.id))})


@router.post("/new-product/line/{iid}/remove")
def new_product_remove(iid: int, csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    lst = npl.draft(c.db, c.user)
    try:
        npl.remove_item(c.db, lst, iid)
        c.db.commit()
    except npl.ListError as e:
        c.db.rollback()
        return JSONResponse({"ok": False, "error": c.t(str(e))}, status_code=400)
    return JSONResponse({"ok": True, **_data(_list(c, lst.id))})


@router.post("/new-product/save")
def new_product_save(csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    lst = npl.draft(c.db, c.user)
    try:
        npl.save(c.db, c.user, lst)
        c.db.commit()
    except npl.ListError as e:
        c.db.rollback()
        return redirect("/new-product", err=c.t(str(e)))
    msg = c.t("Saved. The Excel file is ready.")
    if c.can("product.push"):  # HQ roles create the products in StoreHub right away; staff lists wait for a push
        out = product_push.push_list(c.db, c.user, lst)
        if out["created"] or out["failed"]:
            msg += " " + c.t("In StoreHub") + f": {out['created']}" + (f" · {c.t('failed')}: {out['failed']}" if out["failed"] else "")
    return redirect(f"/new-product/lists/{lst.id}", ok=msg)


@router.post("/new-product/lists/{lid}/push")
def new_product_list_push(lid: int, csrf: str = Form(""), c: Ctx = Depends(need("product.push"))):
    """Push (or retry) the pending items of a saved list into StoreHub. Anyone with the push right can do it — also for a list a store user saved."""
    c.check_csrf(csrf)
    lst = c.db.scalar(select(NewProductList).where(NewProductList.id == lid).options(selectinload(NewProductList.items)))
    if lst is None or lst.status != "saved":
        raise HTTPException(404)
    out = product_push.push_list(c.db, c.user, lst)
    msg = c.t("In StoreHub") + f": {out['created']}"
    if out["failed"]:
        msg += f" · {c.t('failed')}: {out['failed']}"
    if out["skipped"]:
        msg += f" · {c.t('without a price')}: {out['skipped']}"
    return redirect(f"/new-product/lists/{lst.id}", ok=msg)


@router.get("/new-product/lists/{lid}")
def new_product_list_page(lid: int, c: Ctx = Depends(need(PERM))):
    lst = _list(c, lid)
    if lst.status != "saved":
        return redirect("/new-product")
    pushable = sum(1 for i in lst.items if i.status != "created" and i.price is not None)
    return render(c, "new_product_list.html", lst=lst, summary=npl.summary(lst), crumb=f"NP-{lst.id:05d}",
                  can_push=c.can("product.push") and pushable > 0)


@router.get("/new-product/lists/{lid}/excel")
def new_product_excel(lid: int, c: Ctx = Depends(need(PERM))):
    lst = _list(c, lid)
    if lst.status != "saved" or not lst.excel_path:
        raise HTTPException(404)
    path = (get_config().data_dir / lst.excel_path).resolve()
    if not path.is_file() or path.parent != (get_config().data_dir / new_products.RPA_DIR).resolve():
        raise HTTPException(404)
    return FileResponse(path, filename=npl.file_name(lst))


@router.post("/new-product/lists/{lid}/delete")
def new_product_list_delete(lid: int, csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    c.check_csrf(csrf)
    lst = _list(c, lid)
    if lst.created_by != c.user.id and not c.can("users.manage"):  # push roles may open and push a list, never delete it
        raise HTTPException(404)
    name = f"NP-{lst.id:05d}"
    npl.delete(c.db, c.user, lst)
    c.db.commit()
    return redirect("/new-product", ok=c.t("Deleted") + " " + name)


@router.post("/new-product/discard")
def new_product_discard(csrf: str = Form(""), c: Ctx = Depends(need(PERM))):
    """Throw away the list being built (every product on it)."""
    c.check_csrf(csrf)
    lst = npl.draft(c.db, c.user)
    if lst.items:
        npl.delete(c.db, c.user, lst)
    c.db.commit()
    return redirect("/new-product", ok=c.t("List discarded."))
