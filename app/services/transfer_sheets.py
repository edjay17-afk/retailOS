"""Warehouse transfer sheet: the warehouse scans each product it sends to a store and types the quantity, instead of writing the list in a notebook.

The saved sheet is the record of what was sent. It is exported as StoreHub's own "Import Items by CSV" file for a stock transfer (the template downloaded from the
StoreHub back office, kept in docs/storehub/), so it can be imported with no retyping. Products StoreHub does not know yet can still be put on the sheet: they are
kept apart from the import file (StoreHub would refuse the whole file for an unknown product) and join it as soon as the product exists in StoreHub.
Once StoreHub's own transfer shows up in the stock-transfer import it is matched back to the sheet. StoreHub is never written from here.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_config
from ..models import InventoryCurrent, Location, Product, StockTransfer, TransferSheet, TransferSheetLine, User
from ..timeutil import fmt_local, now_utc
from . import new_products

RPA_DIR = Path("rpa") / "stock-transfers"
# StoreHub "Import Items by CSV" template (Stock Transfer > Import Items by CSV > CSV template). The first row must not change; row 2 is the template's own
# explanation row and is kept as it is. Source and destination store are chosen in StoreHub when the transfer is created, so they are not columns.
IMPORT_HEADER = ["Product Name", "SKU", "Product Id", "Ordered Qty", "Unit", "Serial Numbers"]
IMPORT_NOTES = ["#Optional", "Required if no Product Id", "Required if no SKU (found in Manage Products > Export to CSV)", "Required for simple or composite products",
                "Optional; the transfer unit (e.g. ctn) for products tracked in multiple units. Leave blank to transfer in the base unit.",
                "Required for serialized products only; Must be unique if specified; Use semicolon(;) to separate multiple serial numbers. "
                "Serial numbers containing semicolons cannot be included in the CSV. Please enter them individually in the Stock Transfer page."]
EXCEL_HEADER = IMPORT_HEADER + ["Barcode", "Source Store", "Target Store", "Sheet"]
MAX_QTY = 100000


class SheetError(Exception):
    pass


def warehouse(db: Session) -> Location | None:
    return db.scalar(select(Location).where(Location.type == "warehouse").order_by(Location.monitored.desc(), Location.id))


def stores(db: Session) -> list[Location]:
    return list(db.scalars(select(Location).where(Location.type == "store", Location.monitored.is_(True)).order_by(Location.name)))


def _draft_lines_ok(sheet: TransferSheet) -> None:
    if sheet.status != "draft":
        raise SheetError("This sheet was already submitted and can no longer be changed")


def create(db: Session, user: User, target_id: int, note: str | None = None) -> TransferSheet:
    """Start a sheet for a store. An open draft for the same store is reused, so two people never start two sheets for the same trip."""
    src = warehouse(db)
    target = db.get(Location, target_id)
    if src is None:
        raise SheetError("No warehouse is set up in the system")
    if target is None or target.type != "store":
        raise SheetError("Choose the store the goods are going to")
    existing = db.scalar(select(TransferSheet).where(TransferSheet.status == "draft", TransferSheet.target_location_id == target.id,
                                                     TransferSheet.source_location_id == src.id))
    if existing is not None:
        return existing
    sheet = TransferSheet(source_location_id=src.id, target_location_id=target.id, note=(note or "").strip()[:300] or None, created_by=user.id)
    db.add(sheet)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer_sheet.create", entity_type="transfer_sheet", entity_id=sheet.id,
              after={"source": src.name, "target": target.name})
    return sheet


def find(db: Session, code: str, limit: int = 8) -> tuple[Product | None, list[Product]]:
    """A scanned barcode (or SKU) gives one exact product; anything else is searched by name so staff can pick."""
    code = (code or "").strip()
    if not code:
        return None, []
    exact = new_products.lookup(db, code)
    if exact is None:
        exact = db.scalar(select(Product).where(Product.sku == code, Product.in_api.is_(True)))
    if exact is not None:
        return exact, []
    q = select(Product).where(Product.in_api.is_(True))
    for word in re.split(r"\s+", code)[:5]:
        q = q.where(or_(Product.name.ilike(f"%{word}%"), Product.sku.ilike(f"%{word}%"), Product.barcode.ilike(f"%{word}%")))
    return None, list(db.scalars(q.order_by(Product.name).limit(limit)))


def catalog(db: Session, warehouse_id: int | None) -> list[list]:
    """Every StoreHub product in one small list, so the page can answer a scan instantly in the browser: [id, name, sku, [barcodes], warehouse stock]."""
    stock = {pid: q for pid, q in db.execute(select(InventoryCurrent.product_id, InventoryCurrent.on_hand).where(InventoryCurrent.location_id == warehouse_id))} if warehouse_id else {}
    rows = db.execute(select(Product.id, Product.name, Product.sku, Product.barcode).where(Product.in_api.is_(True)).order_by(Product.name)).all()
    return [[i, n, s or "", [b.strip() for b in (bc or "").split(",") if b.strip()], stock.get(i)] for i, n, s, bc in rows]


def on_hand(db: Session, location_id: int, product_ids: list[int]) -> dict[int, float]:
    if not product_ids:
        return {}
    return {pid: qty for pid, qty in db.execute(select(InventoryCurrent.product_id, InventoryCurrent.on_hand)
                                                .where(InventoryCurrent.location_id == location_id, InventoryCurrent.product_id.in_(product_ids)))}


def _check_qty(qty) -> float:
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        raise SheetError("Enter the quantity")
    if qty <= 0:
        raise SheetError("The quantity must be above zero")
    if qty > MAX_QTY or abs(qty - round(qty)) > 1e-9:
        raise SheetError("The quantity must be a whole number of pieces")
    return qty


def add_line(db: Session, sheet: TransferSheet, product: Product, qty) -> TransferSheetLine:
    """Add quantity for a product. Scanning the same product again adds to its line instead of making a second one."""
    _draft_lines_ok(sheet)
    qty = _check_qty(qty)
    line = next((l for l in sheet.lines if l.product_id == product.id), None)
    if line is None:
        line = TransferSheetLine(sheet_id=sheet.id, product_id=product.id, qty=0.0, unit_cost=product.cost)
        sheet.lines.append(line)
    line.qty = line.qty + qty
    if line.qty > MAX_QTY:
        raise SheetError("That quantity looks too large")
    db.flush()
    return line


def set_qty(db: Session, sheet: TransferSheet, line_id: int, qty) -> TransferSheetLine:
    _draft_lines_ok(sheet)
    line = next((l for l in sheet.lines if l.id == line_id), None)
    if line is None:
        raise SheetError("That line is not on this sheet")
    line.qty = _check_qty(qty)
    return line


def remove_line(db: Session, sheet: TransferSheet, line_id: int) -> None:
    _draft_lines_ok(sheet)
    line = next((l for l in sheet.lines if l.id == line_id), None)
    if line is None:
        raise SheetError("That line is not on this sheet")
    sheet.lines.remove(line)
    db.delete(line)
    db.flush()


def cancel(db: Session, user: User, sheet: TransferSheet) -> None:
    """Throw a draft away for good (not just hide it), so the sheet numbers carry on from the sheets that really exist: with none left, the next is TS-00001.
    The audit log keeps a record that it existed and what it held."""
    _draft_lines_ok(sheet)
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer_sheet.cancel", entity_type="transfer_sheet", entity_id=sheet.id,
              before={"sheet": f"TS-{sheet.id:05d}", "target": sheet.target.name,
                      "lines": [{"barcode": line_barcode(l), "name": line_name(l), "qty": l.qty} for l in sheet.lines][:200]})
    db.delete(sheet)
    db.flush()


def importable(sheet: TransferSheet) -> list[TransferSheetLine]:
    return [l for l in sorted(sheet.lines, key=lambda x: x.id) if l.product is not None]


def line_name(l: TransferSheetLine) -> str:
    return l.product.name if l.product else ""


def line_barcode(l: TransferSheetLine) -> str:
    return (l.product.barcode or "") if l.product else ""


def delete(db: Session, user: User, sheet: TransferSheet) -> None:
    """Remove a sheet for good: its lines and its Excel file. The audit log keeps a record that it existed and what it held."""
    detail = {"sheet": f"TS-{sheet.id:05d}", "status": sheet.status, "target": sheet.target.name, "sh_reference": sheet.sh_reference,
              "lines": [{"barcode": line_barcode(l), "name": line_name(l), "qty": l.qty} for l in sheet.lines][:200]}
    sid, xl = sheet.id, (get_config().data_dir / sheet.excel_path) if sheet.excel_path else None
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer_sheet.delete", entity_type="transfer_sheet", entity_id=sid, before=detail)
    db.delete(sheet)
    db.flush()
    if xl is not None and xl.parent.resolve() == (get_config().data_dir / RPA_DIR).resolve():
        xl.unlink(missing_ok=True)


def summary(sheet: TransferSheet) -> dict:
    return {"lines": len(sheet.lines), "qty": sum(l.qty for l in sheet.lines),
            "value": round(sum(l.qty * (l.unit_cost or 0) for l in sheet.lines), 2)}


def _qty(q: float):
    return int(q) if abs(q - round(q)) < 1e-9 else q


def import_rows(sheet: TransferSheet) -> list[list]:
    """The rows StoreHub imports: Product Name, SKU, Product Id, Ordered Qty, Unit (blank = the base unit), Serial Numbers (blank)."""
    return [[l.product.name, l.product.sku or "", l.product.storehub_id, _qty(l.qty), "", ""] for l in importable(sheet)]


def csv_text(sheet: TransferSheet) -> str:
    """StoreHub's stock-transfer import file, exactly in its template layout (UTF-8 with BOM, CRLF, header row, the template's note row, then the items)."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(IMPORT_HEADER)
    w.writerow(IMPORT_NOTES)
    w.writerows(import_rows(sheet))
    return "﻿" + out.getvalue()


def file_stem(sheet: TransferSheet) -> str:
    store = re.sub(r"[^A-Za-z0-9]+", "-", sheet.target.name.replace("The Daily Centro", "").strip()).strip("-") or "store"
    return f"StockTransfer-TS-{sheet.id:05d}-to-{store}"


def write_excel(sheet: TransferSheet) -> str:
    """The Excel file the RPA picks up (same folder convention as new products): the import columns first, then reference columns."""
    from openpyxl import Workbook
    d = get_config().data_dir / RPA_DIR
    d.mkdir(parents=True, exist_ok=True)
    rel = RPA_DIR / f"TS-{sheet.id:05d}_{now_utc().strftime('%Y%m%d-%H%M%S')}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock Transfer"
    ws.append(EXCEL_HEADER)
    for l in importable(sheet):
        ws.append([l.product.name, l.product.sku or "", l.product.storehub_id, _qty(l.qty), "", "", line_barcode(l), sheet.source.name, sheet.target.name, f"TS-{sheet.id:05d}"])
    for col, width in zip("ABCDEFGHIJ", (44, 14, 28, 12, 8, 14, 16, 26, 30, 12)):
        ws.column_dimensions[col].width = width
    wb.save(d / rel.name)
    return rel.as_posix()


def submit(db: Session, user: User, sheet: TransferSheet) -> TransferSheet:
    _draft_lines_ok(sheet)
    if not sheet.lines:
        raise SheetError("Scan at least one product before saving the sheet")
    sheet.status, sheet.submitted_by, sheet.submitted_at = "submitted", user.id, now_utc()
    db.flush()
    sheet.excel_path = write_excel(sheet)
    s = summary(sheet)
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer_sheet.submit", entity_type="transfer_sheet", entity_id=sheet.id,
              after={"source": sheet.source.name, "target": sheet.target.name, **s,
                     "lines_detail": [{"barcode": line_barcode(l), "name": line_name(l), "qty": l.qty} for l in sheet.lines][:200]})
    return sheet


def mark_in_storehub(db: Session, user: User, sheet: TransferSheet, reference: str) -> TransferSheet:
    """Staff keyed the sheet into StoreHub (or the RPA did) and noted its number, e.g. ST0071."""
    if sheet.status not in ("submitted", "in_storehub"):
        raise SheetError("Save the sheet first")
    reference = (reference or "").strip().upper()[:80]
    if not reference:
        raise SheetError("Enter the StoreHub transfer number, for example ST0071")
    sheet.sh_reference, sheet.status = reference, "in_storehub"
    audit.log(db, actor_type="human", actor_id=user.id, action="transfer_sheet.in_storehub", entity_type="transfer_sheet", entity_id=sheet.id,
              after={"reference": reference})
    return sheet


def match_storehub(db: Session) -> int:
    """Link submitted sheets to the StoreHub transfer that carries exactly the same products and quantities to the same store."""
    n = 0
    for sheet in db.scalars(select(TransferSheet).where(TransferSheet.status == "submitted")):
        want = {l.product_id: l.qty for l in sheet.lines}
        cands = db.scalars(select(StockTransfer).where(StockTransfer.target_location_id == sheet.target_location_id,
                                                       StockTransfer.created_sh >= sheet.submitted_at - timedelta(days=1)))
        for t in cands:
            if t.status == "Cancelled" or db.scalar(select(func.count()).select_from(TransferSheet).where(TransferSheet.sh_reference == t.reference)):
                continue
            have: dict[int, float] = {}
            for l in t.lines:
                if l.product_id:
                    have[l.product_id] = have.get(l.product_id, 0) + l.quantity
            if have == want:
                sheet.sh_reference, sheet.status = t.reference, "in_storehub"
                audit.log(db, actor_type="system", actor_id="match", action="transfer_sheet.matched", entity_type="transfer_sheet", entity_id=sheet.id,
                          after={"reference": t.reference})
                n += 1
                break
    return n
