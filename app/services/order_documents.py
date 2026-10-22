"""Decimal-safe, editable print copies and StoreHub's official item CSV formats."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import audit
from ..models import OrderDocument, Product, Location
from ..timeutil import now_utc, to_local

HEADERS = {
    "po": ["Product Name", "SKU", "Product Id", "Qty to Order ", "Unit", "Supplier Price"],
    "transfer": ["Product Name", "SKU", "Product Id", "Ordered Qty", "Unit", "Serial Numbers"],
}
TEXT_FIELDS = {"name": 300, "sku": 80, "product_id": 40, "serial": 10000, "unit": 80}
HEADER_FIELDS = {"reference": 80, "supplier": 300, "from_name": 300, "to_name": 300,
                 "from_address": 2000, "to_address": 2000, "phone": 100, "notes": 10000}


class Invalid(ValueError):
    pass


class Conflict(ValueError):
    pass


def number(value, label="Number") -> str:
    text = str(value if value is not None else "0").strip()
    if len(text) > 200 or not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise Invalid("Check quantities and costs. Use a dot for decimals.")
    return plain(Decimal(text))


def plain(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def display(value, minimum=0):
    text = plain(Decimal(str(value or 0)))
    whole, _, fraction = text.partition(".")
    whole = f"{int(whole):,}"
    fraction = fraction.ljust(minimum, "0")
    return whole + ("." + fraction if fraction else "")


def cents(value):
    """What a PO printout shows for an amount: rounded to cents like StoreHub's own PO (3,053.57), however many decimals the exact figure has."""
    return display(Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP), 2)


def six_decimals(value):
    """Round only the displayed amount; calculations retain their Decimal precision."""
    with localcontext() as ctx:
        ctx.prec = 1024
        return display(Decimal(str(value or 0)).quantize(Decimal("0.000001"), ROUND_HALF_UP), 6)


def source_hash(source, kind):
    value = source.raw_hash if kind == "po" else source.payload_hash
    return value or hashlib.sha256(str(source.modified_sh if kind == "po" else source.last_import_id).encode()).hexdigest()


def print_date(value):
    return date.fromisoformat(value).strftime("%m/%d/%Y") if value else ""


def initial(db, source, kind):
    h = {key: "" for key in HEADER_FIELDS}
    h.update(reference=f"PO{source.sh_po_id:04d}" if kind == "po" else source.reference,
             date=to_local(source.created_sh).date().isoformat() if source.created_sh else "",
             expected=to_local(source.expected_at).date().isoformat() if kind == "po" and source.expected_at else "",
             tax=str(source.tax or 0) if kind == "po" else "0",
             discount=str(source.discount or 0) if kind == "po" else "0")
    lines = []
    raw_total = ""
    if kind == "po":
        h.update(supplier=source.supplier.name if source.supplier else "", to_name=source.location.name if source.location else "",
                 to_address=source.location.city or "" if source.location else "", notes=source.notes or "")
        for item in source.lines:
            p = item.product
            cost = item.price_exact or item.supplier_price  # StoreHub's own price text when it has been read
            if cost is None and item.ordered_qty:
                # Only a fallback for old API records without supplier price.
                with localcontext() as ctx:
                    ctx.prec = 200
                    cost = Decimal(str(item.subtotal or 0)) / Decimal(str(item.ordered_qty))
            lines.append(dict(name=p.name if p else item.product_sh_id or "", sku=p.sku or "" if p else "",
                              product_id=item.product_sh_id or (p.storehub_id if p else ""), serial="", unit="",
                              quantity=str(item.ordered_qty), cost=str(cost or 0)))
        for line in lines:  # StoreHub prints a PO unit price cut off after 6 decimals, not the float's noise
            line["cost"] = plain(Decimal(line["cost"]).quantize(Decimal("0.000001"), ROUND_DOWN) if Decimal(line["cost"]).as_tuple().exponent < -6 else Decimal(line["cost"]))
    else:
        h.update(from_name=source.source_name, to_name=source.target_name)
        for key, lid in (("from_address", source.source_location_id), ("to_address", source.target_location_id)):
            loc = db.get(Location, lid) if lid else None
            h[key] = loc.city or "" if loc else ""
        # Preserve decimal spelling from the retained original export, instead of its float mirror.
        raw_rows = {}
        if source.last_import and source.last_import.raw_csv:
            try:
                for row in csv.DictReader(io.StringIO(source.last_import.raw_csv.decode("utf-8-sig"))):
                    if row.get("S.T ID") == source.reference:
                        raw_rows[int(row.get("No.") or 0)] = row
            except (UnicodeError, ValueError, csv.Error):
                raw_rows = {}
        products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([i.product_id for i in source.lines if i.product_id] or [0])))}
        for item in source.lines:
            p = products.get(item.product_id)
            raw = raw_rows.get(item.line_no, {})
            raw_cost = next((v for k, v in raw.items() if k and re.fullmatch(r"Cost(?: \([^)]+\))?", k)), None)
            line = dict(name=item.product_name, sku=item.sku or (p.sku or "" if p else ""),
                        product_id=p.storehub_id if p else "", serial=item.serial_no or "", unit=item.unit or "",
                        quantity=(raw.get("Ordered Qty") or str(item.quantity)).replace(",", ""),
                        cost=(raw_cost or str(item.unit_cost or 0)).replace(",", ""))
            lines.append(line)
    for line in lines:
        line["quantity"] = plain(Decimal(line["quantity"]))
        line["cost"] = plain(Decimal(line["cost"]))
    return dict(header=h, lines=lines, revision=0, saved=False, changed=False)


def document(db, source, kind):
    saved = db.scalar(select(OrderDocument).where(OrderDocument.kind == kind, OrderDocument.source_id == source.id))
    d = dict(header=dict(saved.header), lines=[dict(i) for i in saved.lines], revision=saved.revision,
             saved=True, changed=saved.source_hash != source_hash(source, kind)) if saved else initial(db, source, kind)
    return calculate(d)


def calculate(d):
    # 200 input characters × 2 per product, plus room for the sum of up to 1,000 rows.
    with localcontext() as ctx:
        ctx.prec = 1024
        subtotal = Decimal(0)
        for line in d["lines"]:
            # Amounts and the total are exact (quantity x cost, summed): never rounded to 2 decimals the way StoreHub's own export is.
            line["amount"] = plain(Decimal(line["quantity"]) * Decimal(line["cost"]))
            subtotal += Decimal(line["amount"])
        d["subtotal"] = plain(subtotal)
        d["total"] = plain(subtotal + Decimal(d["header"].get("tax") or "0") - Decimal(d["header"].get("discount") or "0"))
    return d


def validate(payload, kind):
    if not isinstance(payload, dict) or not isinstance(payload.get("header"), dict) or not isinstance(payload.get("lines"), list):
        raise Invalid("Check the document fields and try again.")
    h = {}
    for key, limit in HEADER_FIELDS.items():
        value = payload["header"].get(key, "")
        if not isinstance(value, str) or len(value) > limit or "\x00" in value:
            raise Invalid("A text field is too long or invalid.")
        h[key] = value.strip()
    if not h["reference"]:
        raise Invalid("Enter a document number.")
    for key in ("date", "expected"):
        value = payload["header"].get(key, "")
        try:
            h[key] = date.fromisoformat(value).isoformat() if value else ""
        except (ValueError, TypeError):
            raise Invalid("Enter a valid date.")
    for key in ("tax", "discount"):
        h[key] = number(payload["header"].get(key, "0"), key) if kind == "po" else "0"
    if not 1 <= len(payload["lines"]) <= 1000:
        raise Invalid("Add between 1 and 1,000 item rows.")
    lines = []
    for item in payload["lines"]:
        if not isinstance(item, dict):
            raise Invalid("Check the item rows.")
        line = {}
        for key, limit in TEXT_FIELDS.items():
            value = item.get(key, "")
            if not isinstance(value, str) or len(value) > limit or "\x00" in value:
                raise Invalid("An item field is too long or invalid.")
            line[key] = value.strip()
        if not line["name"]:
            raise Invalid("Enter a product name for every row.")
        line.update(quantity=number(item.get("quantity", ""), "Quantity"), cost=number(item.get("cost", ""), "Unit cost"))
        lines.append(line)
    d = calculate(dict(header=h, lines=lines))
    if Decimal(d["total"]) < 0:
        raise Invalid("Discount cannot be greater than the subtotal plus tax.")
    return d


def save(db, source, kind, payload, revision, user):
    d = validate(payload, kind)
    row = db.scalar(select(OrderDocument).where(OrderDocument.kind == kind, OrderDocument.source_id == source.id))
    before = dict(header=row.header, lines=row.lines) if row else None
    values = dict(header=d["header"], lines=[{k: v for k, v in i.items() if k not in ("amount", "amount_raw")} for i in d["lines"]],
                  source_hash=source_hash(source, kind), updated_by=user.id, updated_at=now_utc(), revision=revision + 1)
    if row:
        result = db.execute(update(OrderDocument).where(OrderDocument.id == row.id, OrderDocument.revision == revision).values(**values))
        if result.rowcount != 1:
            raise Conflict("Someone saved changes to this document. Reload before editing again.")
    else:
        if revision != 0:
            raise Conflict("Someone saved changes to this document. Reload before editing again.")
        db.add(OrderDocument(kind=kind, source_id=source.id, **values))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise Conflict("Someone saved changes to this document. Reload before editing again.")
    audit.log(db, actor_type="user", actor_id=user.id, action="document.edit", entity_type=kind, entity_id=source.id,
              before=before, after=dict(header=d["header"], lines=values["lines"]))
    return revision + 1


def safe_text(text):
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def export(d, kind, mode):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    if mode == "storehub":
        writer.writerow(HEADERS[kind])
        for n, line in enumerate(d["lines"], 1):
            if not (line["product_id"] or line["sku"]):
                raise Invalid("Every item needs a StoreHub Product ID or SKU before exporting for StoreHub.")
            if any(safe_text(line[k]) != line[k] for k in ("product_id", "sku")):
                raise Invalid("A Product ID or SKU starts with a spreadsheet formula character. Check it before exporting.")
            writer.writerow([safe_text(line["name"]), line["sku"], line["product_id"], line["quantity"], safe_text(line["unit"]),
                             line["cost"] if kind == "po" else safe_text(line["serial"])])
    else:
        writer.writerow(["Document", "No.", "Product Name", "SKU", "Product Id", "Quantity", "Unit", "Serial Numbers", "Unit Cost (PHP)", "Amount (PHP)"])
        for n, line in enumerate(d["lines"], 1):
            writer.writerow([safe_text(d["header"]["reference"]), n, safe_text(line["name"]), safe_text(line["sku"]), safe_text(line["product_id"]),
                             line["quantity"], safe_text(line["unit"]), safe_text(line["serial"]), line["cost"], line["amount"]])
        for label, value in (("Subtotal (PHP)", d["subtotal"]), ("Tax (PHP)", d["header"]["tax"]),
                             ("Discount (PHP)", d["header"]["discount"]), ("Total (PHP)", d["total"])):
            writer.writerow([label] + [""] * 8 + [value])
        for key, value in d["header"].items():
            if key not in ("tax", "discount"):
                writer.writerow([key, safe_text(value)])
    return stream.getvalue().encode("utf-8-sig")
