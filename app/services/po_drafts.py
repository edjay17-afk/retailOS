"""Write a purchase order in Retail OS: choose the supplier and where it is delivered, then add products with the quantity to order and the supplier price.

Quantities and prices are exact decimal text, never a float and never cut to two decimals (a supplier price such as 254.4642857 keeps every digit). The total of a
line is quantity x price, worked out with Decimal. The saved order is exported as StoreHub's own purchase-order item import (Product Name, SKU, Product Id,
Qty to Order, Unit, Supplier Price) and as an Excel file for the RPA. StoreHub itself is never written from here.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_config
from ..models import Location, PoDraft, PoDraftLine, PoLine, Product, PurchaseOrder, Supplier, User
from ..timeutil import now_utc, to_local
from . import order_documents as docs

RPA_DIR = Path("rpa") / "purchase-orders"
EXCEL_HEADER = ["Product Name", "SKU", "Product Id", "Qty to Order", "Unit", "Supplier Price", "Total", "Barcode", "Supplier", "Deliver To", "Order", "Created By"]
MAX_DIGITS = 18  # digits before the decimal point
MAX_DECIMALS = 12
PRICE_DECIMALS = 6  # StoreHub keeps a supplier price to 6 decimals and cuts the rest off; so does every price here
MAX_LINES = 500
NUMBER = re.compile(r"^(\d+)(?:\.(\d*))?$|^\.(\d+)$")


class PoError(Exception):
    pass


def reference(d: PoDraft) -> str:
    return f"RO-{d.id:05d}"


def suppliers(db: Session) -> list[str]:
    """Existing suppliers, by name. A placeholder StoreHub made from an id only ('Supplier a1b2c3') is not offered."""
    return [s.name for s in db.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.name)) if s.name_source != "auto"]


def supplier_by_name(db: Session, name: str) -> Supplier | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    return next((s for s in db.scalars(select(Supplier).where(Supplier.active.is_(True))) if s.name_source != "auto" and s.name.strip().lower() == key), None)


def price6(value: Decimal) -> str:
    """A supplier price as StoreHub holds it: cut off (not rounded) after 6 decimals, 254.4642857 -> 254.464285."""
    if -value.as_tuple().exponent > PRICE_DECIMALS:
        value = value.quantize(Decimal(1).scaleb(-PRICE_DECIMALS), ROUND_DOWN)
    return docs.plain(value)


def _clean(text, label: str, max_decimals: int = MAX_DECIMALS) -> str:
    t = str(text if text is not None else "").strip().replace(",", "").replace(" ", "")
    if not t:
        raise PoError(f"Enter the {label}")
    m = NUMBER.match(t)
    if not m:
        raise PoError(f"The {label} must be a number (use a dot for decimals)")
    whole = m.group(1) if m.group(1) is not None else "0"
    frac = (m.group(2) if m.group(1) is not None else m.group(3)) or ""
    if len(whole.lstrip("0")) > MAX_DIGITS or len(frac) > max_decimals:
        raise PoError(f"The {label} has too many digits")
    return docs.plain(Decimal(f"{whole}.{frac}" if frac else whole))


def check_qty(value) -> str:
    q = _clean(value, "quantity to order")
    if Decimal(q) <= 0:
        raise PoError("The quantity must be above zero")
    return q


def check_price(value) -> str:
    """The supplier price, with at most 6 decimals (a longer one is cut off after the 6th, as StoreHub does)."""
    return price6(Decimal(_clean(value, "supplier price", max_decimals=40)))


def total_of(qty: str, price: str) -> str:
    """quantity x price, exact."""
    with localcontext() as ctx:
        ctx.prec = 100
        return docs.plain(Decimal(qty) * Decimal(price))


def show(text: str) -> str:
    """For the screen: thousands separators and at least two decimals; more than two decimals are all kept (3053.5714285 -> 3,053.5714285)."""
    whole, _, frac = str(text).partition(".")
    return f"{int(whole):,}.{frac.ljust(2, '0')}"


def rate_of(d: PoDraft) -> str:
    return d.tax_rate or "12"


def tax_of(subtotal: str, rate: str) -> str:
    """The tax on the subtotal: subtotal x rate / 100, exact."""
    with localcontext() as ctx:
        ctx.prec = 100
        return docs.plain(Decimal(subtotal) * Decimal(rate) / 100)


def summary(d: PoDraft) -> dict:
    """lines, the subtotal (sum of the line totals), the tax on it, and the overall total = subtotal + tax. All exact."""
    with localcontext() as ctx:
        ctx.prec = 100
        sub = sum((Decimal(total_of(l.qty, l.price)) for l in d.lines), Decimal(0))
        tax = Decimal(tax_of(docs.plain(sub), rate_of(d)))
        return {"lines": len(d.lines), "subtotal": docs.plain(sub), "tax": docs.plain(tax), "total": docs.plain(sub + tax), "rate": rate_of(d)}


def current(db: Session, user: User) -> PoDraft | None:
    return db.scalar(select(PoDraft).where(PoDraft.status == "draft", PoDraft.created_by == user.id).order_by(PoDraft.id.desc()))


def start(db: Session, user: User, supplier_name: str, location_id: int, allowed: list[int], tax_rate: str = "12") -> PoDraft:
    sup = supplier_by_name(db, supplier_name)
    if sup is None:
        raise PoError("Choose the supplier from the list")
    loc = db.get(Location, location_id)
    if loc is None or loc.id not in allowed:
        raise PoError("Choose where the order is delivered")
    if loc.source != "storehub" or sup.source != "storehub":
        raise PoError("Purchase-order exports are currently supported for StoreHub only")
    d = current(db, user)
    if d is not None:  # an open order is carried on, never started twice
        return change(db, user, d, supplier_name, location_id, allowed)
    d = PoDraft(supplier_id=sup.id, location_id=loc.id, created_by=user.id, tax_rate=tax_rate)
    db.add(d)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="po_draft.start", entity_type="po_draft", entity_id=d.id, after={"supplier": sup.name, "deliver_to": loc.name})
    return d


def change(db: Session, user: User, d: PoDraft, supplier_name: str, location_id: int, allowed: list[int]) -> PoDraft:
    _draft(d)
    sup = supplier_by_name(db, supplier_name)
    if sup is None:
        raise PoError("Choose the supplier from the list")
    loc = db.get(Location, location_id)
    if loc is None or loc.id not in allowed:
        raise PoError("Choose where the order is delivered")
    if loc.source != "storehub" or sup.source != "storehub":
        raise PoError("Purchase-order exports are currently supported for StoreHub only")
    d.supplier_id, d.location_id = sup.id, loc.id
    db.flush()
    return d


def _draft(d: PoDraft) -> None:
    if d.status != "draft":
        raise PoError("This order was already saved and can no longer be changed")


def last_prices(db: Session, supplier_id: int) -> dict[int, tuple[str, int]]:
    """The price most recently paid to this supplier for each product (from its StoreHub POs): product id -> (exact price, PO number)."""
    out: dict[int, tuple[str, int]] = {}
    q = (select(PoLine.product_id, PoLine.price_exact, PoLine.supplier_price, PurchaseOrder.sh_po_id).join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
         .where(PurchaseOrder.supplier_id == supplier_id, PoLine.product_id.is_not(None), PoLine.supplier_price.is_not(None), PurchaseOrder.missing_from_api.is_(False))
         .order_by(PurchaseOrder.created_sh.desc(), PoLine.id.desc()))
    for pid, exact, price, no in db.execute(q):  # StoreHub's own price text when it has been read; otherwise the 2-decimal price its API sends
        out.setdefault(pid, (price6(Decimal(exact) if exact else Decimal(repr(float(price)))), no))
    return out


def catalog(db: Session, supplier_id: int) -> list[list]:
    """Every StoreHub product in one small list the page keeps in memory: [id, name, sku, [barcodes], suggested price, PO number of that price].
    The suggested price is what this supplier was last paid; otherwise the product's cost; otherwise empty."""
    last = last_prices(db, supplier_id)
    rows = db.execute(select(Product.id, Product.name, Product.sku, Product.barcode, Product.cost).where(Product.in_api.is_(True)).order_by(Product.name)).all()
    out = []
    for i, n, s, bc, cost in rows:
        price, po = last.get(i, (price6(Decimal(repr(float(cost)))) if cost else "", None))
        out.append([i, n, s or "", [b.strip() for b in (bc or "").split(",") if b.strip()], price, po])
    return out


def add_line(db: Session, d: PoDraft, product: Product, qty, price) -> PoDraftLine:
    if product.source != "storehub":
        raise PoError("Loyverse items cannot be exported to StoreHub")
    """Add a product. The same product again adds to the quantity of its line and takes the new price."""
    _draft(d)
    q, p = check_qty(qty), check_price(price)
    line = next((l for l in d.lines if l.product_id == product.id), None)
    if line is None:
        if len(d.lines) >= MAX_LINES:
            raise PoError("That is too many products for one order")
        line = PoDraftLine(draft_id=d.id, product_id=product.id, qty=q, price=p)
        d.lines.append(line)
    else:
        line.qty, line.price = docs.plain(Decimal(line.qty) + Decimal(q)), p
    db.flush()
    return line


def _line(d: PoDraft, line_id: int) -> PoDraftLine:
    line = next((l for l in d.lines if l.id == line_id), None)
    if line is None:
        raise PoError("That line is not on this order")
    return line


def set_line(db: Session, d: PoDraft, line_id: int, qty, price) -> PoDraftLine:
    _draft(d)
    line = _line(d, line_id)
    line.qty, line.price = check_qty(qty), check_price(price)
    db.flush()
    return line


def remove_line(db: Session, d: PoDraft, line_id: int) -> None:
    _draft(d)
    line = _line(d, line_id)
    d.lines.remove(line)
    db.delete(line)
    db.flush()


def mark_new_product(db: Session, d: PoDraft, product_id: int) -> None:
    """Remember that this product was created in StoreHub from this order: when the order is saved, its quantity is split between the stores."""
    ids = list(d.new_product_ids or [])
    if product_id not in ids:
        d.new_product_ids = ids + [product_id]
        db.flush()


def distribute_new(db: Session, user: User, d: PoDraft) -> list:
    """The products quick-created from this order go to the stores in equal shares: each store's open transfer sheet gets
    its share as a line (a sheet that is still open simply collects the new products; a submitted one is followed by a new sheet).
    A leftover piece goes to the first stores, one each. Transfer sheets only take whole pieces, so a fractional quantity is
    rounded to the nearest whole piece before splitting."""
    from . import transfer_sheets as ts

    new = set(d.new_product_ids or [])
    lines = [l for l in d.lines if l.product_id in new and l.product is not None]
    stores = ts.stores(db)
    if not lines or not stores:
        return []
    made: dict[int, object] = {}
    for l in lines:
        pieces = int(Decimal(l.qty).to_integral_value(rounding=ROUND_HALF_UP))
        base, extra = divmod(pieces, len(stores))
        for i, store in enumerate(stores):
            share = base + (1 if i < extra else 0)
            if share <= 0:
                continue
            sheet = made.get(store.id) or ts.create(db, user, store.id, note=f"From purchase order {reference(d)}")
            made[store.id] = sheet
            ts.add_line(db, sheet, l.product, share)
    sheets = list(made.values())
    if sheets:
        audit.log(db, actor_type="human", actor_id=user.id, action="po_draft.distribute", entity_type="po_draft", entity_id=d.id,
                  after={"order": reference(d), "sheets": [{"sheet": f"TS-{s.id:05d}", "target": s.target.name,
                                                            "lines": [{"name": ts.line_name(x), "qty": x.qty} for x in s.lines]} for s in sheets]})
    return sheets


def discard(db: Session, user: User, d: PoDraft) -> int:
    _draft(d)
    n = len(d.lines)
    if n:
        audit.log(db, actor_type="human", actor_id=user.id, action="po_draft.discard", entity_type="po_draft", entity_id=d.id, before=_detail(d))
    db.delete(d)
    db.flush()
    return n


def _detail(d: PoDraft) -> dict:
    return {"order": reference(d), "supplier": d.supplier.name, "deliver_to": d.location.name, **summary(d),
            "lines_detail": [{"barcode": l.product.barcode or "", "name": l.product.name, "qty": l.qty, "price": l.price} for l in d.lines][:200]}


def sorted_lines(d: PoDraft) -> list[PoDraftLine]:
    return sorted(d.lines, key=lambda x: x.id)


def csv_text(d: PoDraft) -> str:
    """StoreHub's purchase-order item import: the header row of its template, then one row per product with the exact quantity and supplier price."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(docs.HEADERS["po"])
    for l in sorted_lines(d):
        w.writerow([l.product.name, l.product.sku or "", l.product.storehub_id, l.qty, "", l.price])
    return "﻿" + out.getvalue()


def file_stem(d: PoDraft) -> str:
    sup = re.sub(r"[^A-Za-z0-9]+", "-", d.supplier.name).strip("-")[:40] or "supplier"
    return f"PurchaseOrder-{reference(d)}-{sup}"


def _num(text: str):
    """An Excel number: a whole number stays whole; a decimal keeps all the digits a number can hold."""
    v = Decimal(text)
    return int(v) if v == v.to_integral_value() else float(v)


def write_excel(d: PoDraft) -> str:
    from openpyxl import Workbook
    folder = get_config().data_dir / RPA_DIR
    folder.mkdir(parents=True, exist_ok=True)
    rel = RPA_DIR / f"{reference(d)}_{now_utc().strftime('%Y%m%d-%H%M%S')}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchase Order"
    ws.append(EXCEL_HEADER)
    who = (d.creator.full_name or d.creator.username) if d.creator else ""
    for l in sorted_lines(d):
        ws.append([l.product.name, l.product.sku or "", l.product.storehub_id, _num(l.qty), "", _num(l.price), _num(total_of(l.qty, l.price)), l.product.barcode or "",
                   d.supplier.name, d.location.name, reference(d), who])
    sm = summary(d)  # the totals under the lines, for people reading the file
    ws.append([])
    for label, key in (("Subtotal", "subtotal"), (f"Tax {sm['rate']}%", "tax"), ("Total", "total")):
        ws.append([label, "", "", "", "", "", _num(sm[key])])
    for col, width in zip("ABCDEFGHIJKL", (44, 14, 28, 12, 8, 16, 16, 16, 28, 30, 12, 16)):
        ws.column_dimensions[col].width = width
    wb.save(get_config().data_dir / rel)
    return rel.as_posix()


def save(db: Session, user: User, d: PoDraft) -> PoDraft:
    _draft(d)
    if not d.lines:
        raise PoError("Add at least one product before saving the order")
    d.status, d.saved_at = "saved", now_utc()
    db.flush()
    d.excel_path = write_excel(d)
    d.new_sheets = distribute_new(db, user, d)  # transient, for the confirmation message: the sheets the new products went to
    audit.log(db, actor_type="human", actor_id=user.id, action="po_draft.save", entity_type="po_draft", entity_id=d.id, after=_detail(d))
    return d


def delete(db: Session, user: User, d: PoDraft) -> None:
    detail = _detail(d)
    xl = (get_config().data_dir / d.excel_path) if d.excel_path else None
    audit.log(db, actor_type="human", actor_id=user.id, action="po_draft.delete", entity_type="po_draft", entity_id=d.id, before=detail)
    db.delete(d)
    db.flush()
    if xl is not None and xl.parent.resolve() == (get_config().data_dir / RPA_DIR).resolve():
        xl.unlink(missing_ok=True)


def document(d: PoDraft) -> dict:
    """The saved order as the print layout of a StoreHub PO (the same printout as every PO in the system)."""
    header = {k: "" for k in docs.HEADER_FIELDS}
    header.update(reference=reference(d), supplier=d.supplier.name, to_name=d.location.name, to_address=d.location.city or "", date=to_local(d.saved_at or d.created_at).date().isoformat(), tax=summary(d)["tax"], discount="0")
    lines = [dict(name=l.product.name, sku=l.product.sku or "", product_id=l.product.storehub_id, serial="", unit="", quantity=l.qty, cost=l.price) for l in sorted_lines(d)]
    return docs.calculate(dict(header=header, lines=lines, revision=0, saved=True, changed=False))

