"""New-product list: staff scan the barcode of each new product and type its name, pick the supplier and category from the ones that already exist,
and give the quantity. Many products go on one list; saving the list makes the Excel file the RPA uses.
Nothing is written to StoreHub from here.
"""
from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_config
from ..models import NewProductItem, NewProductList, Product, Supplier, User
from ..timeutil import fmt_local, now_utc
from . import new_products

MAX_QTY = 100000


class ListError(Exception):
    pass


def suppliers(db: Session) -> list[str]:
    return [r[0] for r in db.execute(select(Supplier.name).where(Supplier.name != "").order_by(Supplier.name)).all()]


def categories(db: Session) -> list[str]:
    return [r[0] for r in db.execute(select(Product.category).where(Product.category.is_not(None), Product.category != "")
                                     .group_by(Product.category).order_by(Product.category)).all()]


def draft(db: Session, user: User) -> NewProductList:
    """The user's open list (started on first use, so a half-finished list is still there after a reload or on another device)."""
    lst = db.scalar(select(NewProductList).where(NewProductList.status == "draft", NewProductList.created_by == user.id).order_by(NewProductList.id.desc()))
    if lst is None:
        lst = NewProductList(created_by=user.id)
        db.add(lst)
        db.flush()
    return lst


def _check_draft(lst: NewProductList) -> None:
    if lst.status != "draft":
        raise ListError("This list was already saved and can no longer be changed")


def _qty(value) -> float:
    try:
        q = float(str(value).strip().replace(",", ""))
    except ValueError:
        raise ListError("Enter the quantity") from None
    if q <= 0:
        raise ListError("Enter a quantity above zero")
    if q > MAX_QTY or abs(q - round(q)) > 1e-9:
        raise ListError("The quantity must be a whole number of pieces")
    return float(round(q))


def _money(value, label: str) -> float | None:
    """An optional price/cost: empty stays empty (the Excel/RPA path), a value must be a clean non-negative number."""
    if value is None or str(value).strip() == "":
        return None
    try:
        v = float(str(value).strip().replace(",", ""))
    except ValueError:
        raise ListError(f"Enter the {label} as a number") from None
    if v < 0:
        raise ListError(f"The {label} cannot be negative")
    return round(v, 4)


def add_item(db: Session, lst: NewProductList, *, barcode: str, name: str, supplier: str, category: str, qty,
             price=None, cost=None) -> NewProductItem:
    _check_draft(lst)
    barcode, name = (barcode or "").strip(), " ".join((name or "").split())
    if not barcode:
        raise ListError("Scan or enter the barcode")
    if not name:
        raise ListError("Enter the product name")
    if supplier not in suppliers(db):
        raise ListError("Choose the supplier from the list")
    if category not in categories(db):
        raise ListError("Choose the category from the list")
    known = new_products.lookup(db, barcode)
    if known is not None:
        raise ListError(f"Already in StoreHub: {known.name}")
    if barcode in {i.barcode for i in lst.items}:
        raise ListError("This barcode is already on the list")
    q = _qty(qty)
    price, cost = _money(price, "selling price"), _money(cost, "supplier cost")
    if price is not None and cost is not None and price < cost:
        raise ListError(f"The selling price ({price:g}) is below the cost ({cost:g}) — raise the price")
    item = NewProductItem(barcode=barcode[:80], name=name[:300], supplier_name=supplier[:160], category=category[:120], qty=q, price=price, cost=cost)
    lst.items.append(item)
    db.flush()
    return item


def remove_item(db: Session, lst: NewProductList, item_id: int) -> None:
    _check_draft(lst)
    item = next((i for i in lst.items if i.id == item_id), None)
    if item is None:
        raise ListError("That line is no longer on the list")
    lst.items.remove(item)
    db.flush()


def summary(lst: NewProductList) -> dict:
    return {"items": len(lst.items), "qty": sum(i.qty for i in lst.items)}


def save(db: Session, user: User, lst: NewProductList) -> NewProductList:
    _check_draft(lst)
    if not lst.items:
        raise ListError("Add at least one product before saving")
    lst.status, lst.saved_at = "saved", now_utc()
    db.flush()
    lst.excel_path = write_excel(lst)
    audit.log(db, actor_type="human", actor_id=user.id, action="new_product_list.save", entity_type="new_product_list", entity_id=lst.id,
              after={**summary(lst), "items_detail": [{"barcode": i.barcode, "name": i.name, "supplier": i.supplier_name, "category": i.category, "qty": i.qty}
                                                       for i in lst.items][:200]})
    return lst


def delete(db: Session, user: User, lst: NewProductList) -> None:
    """Remove a list for good with its Excel file; the audit log keeps what it held."""
    detail = {"list": f"NP-{lst.id:05d}", "status": lst.status, "items": [{"barcode": i.barcode, "name": i.name, "qty": i.qty} for i in lst.items][:200]}
    lid, xl = lst.id, (get_config().data_dir / lst.excel_path) if lst.excel_path else None
    audit.log(db, actor_type="human", actor_id=user.id, action="new_product_list.delete", entity_type="new_product_list", entity_id=lid, before=detail)
    db.delete(lst)
    db.flush()
    if xl is not None and xl.parent.resolve() == (get_config().data_dir / new_products.RPA_DIR).resolve():
        xl.unlink(missing_ok=True)


def file_name(lst: NewProductList) -> str:
    return f"NewProducts-NP-{lst.id:05d}.xlsx"


def write_excel(lst: NewProductList) -> str:
    """The Excel file the ShadowBot RPA picks up: same folder and columns as every new-product file (the first 11 columns keep their positions), one row per product.
    Cost and price ride along when the scanner typed them; the StoreHub push only needs the price, the RPA file documents the rest."""
    from openpyxl import Workbook
    rel = new_products.RPA_DIR / f"NP-L{lst.id:05d}_{now_utc().strftime('%Y%m%d-%H%M%S')}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "New Products"
    ws.append(new_products.EXCEL_HEADERS)
    made = fmt_local(lst.saved_at or lst.created_at)
    who = (lst.creator.full_name or lst.creator.username) if lst.creator else ""
    for i in lst.items:
        ws.append([i.name, "", i.barcode, i.category, i.supplier_name, i.cost, i.price, i.qty, "Pending RPA", made, who, "New", "", "", "piece", 1, None, i.qty])
    for col, width in zip("ABCDEFGHIJKLMNOPQR", (34, 12, 16, 16, 24, 10, 10, 14, 12, 18, 16, 12, 16, 16, 8, 14, 12, 10)):
        ws.column_dimensions[col].width = width
    new_products.rpa_dir()
    wb.save(get_config().data_dir / rel)
    return rel.as_posix()
