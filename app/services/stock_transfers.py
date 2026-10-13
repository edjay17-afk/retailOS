"""Validated, idempotent StoreHub CSV import. Never writes canonical stock or physical confirmations."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..models import Location, Product, StockTransfer, StockTransferImport, StockTransferLine, User
from ..timeutil import now_utc, tz

MAX_BYTES = 5_000_000
MAX_ROWS = 30_000
STATUSES = ("Created", "Shipped", "Completed", "Cancelled")
REQUIRED = {"S.T ID", "Created Date", "Source Store", "Target Store", "No.", "Product Name", "Ordered Qty", "Status"}
DATE_FIELDS = {"Created Date": "created_sh", "Shipped Date": "shipped_at", "Received Date": "received_at", "Cancelled Date": "cancelled_at"}


class ImportError(ValueError):
    def __init__(self, key: str, **values):
        self.key, self.values = key, values
        super().__init__(key.format(**values))


def _date(value: str, row: int, column: str, required=False) -> datetime | None:
    if not value:
        if required:
            raise ImportError("Row {row}: {column} is required.", row=row, column=column)
        return None
    # StoreHub's verified export uses month/day/year and merchant local time.
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz()).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    raise ImportError("Row {row}: invalid date in {column}. Expected MM/DD/YYYY HH:MM.", row=row, column=column)


def _number(value: str, row: int, column: str, required=False) -> float | None:
    if not value:
        if required:
            raise ImportError("Row {row}: {column} is required.", row=row, column=column)
        return None
    try:
        n = float(value.replace(",", ""))
        if not math.isfinite(n) or n < 0:
            raise ValueError
        return n
    except ValueError:
        raise ImportError("Row {row}: {column} must be a finite, non-negative number.", row=row, column=column)


def parse(raw: bytes) -> list[dict]:
    if len(raw) > MAX_BYTES:
        raise ImportError("The CSV file must be 5 MB or smaller.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportError("Save the StoreHub export as a UTF-8 CSV file.")
    try:
        return _parse_rows(text)
    except csv.Error:
        raise ImportError("The CSV file is malformed. Export it again from StoreHub.")


def _parse_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not REQUIRED <= set(headers):
        raise ImportError("Use the StoreHub Stock Transfer CSV export with S.T ID, source, target and item columns.")
    money_columns = {}
    labels = set()
    for column in headers:
        match = re.fullmatch(r"(Cost|SubTotal|Total)(?: \(([^)]+)\))?", column)
        if match:
            money_columns[match[1]] = column
            labels.add(match[2] or "")
    if len(labels) > 1:
        raise ImportError("The amount columns must use the same currency label.")
    currency = next(iter(labels), "")
    # The StoreHub account is misconfigured to RM; this is a Philippine operation and the
    # exported amounts are pesos (StoreHub never converts values). Mirror them as PHP.
    currency = {"RM": "PHP"}.get(currency, currency)
    groups = {}
    for row_no, row in enumerate(reader, 2):
        if row_no > MAX_ROWS + 1:
            raise ImportError("The CSV has too many rows. Export a smaller date range.")
        if None in row or any(v is None for v in row.values()):
            raise ImportError("Row {row}: the number of columns does not match the header.", row=row_no)
        row = {k: v.strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        for column in ("S.T ID", "Source Store", "Target Store", "Status"):
            if not row[column]:
                raise ImportError("Row {row}: {column} is required.", row=row_no, column=column)
        if any(len(v) > 500 for v in row.values()) or len(row["S.T ID"]) > 80:
            raise ImportError("Row {row}: a field is too long.", row=row_no)
        if row["Source Store"].casefold() == row["Target Store"].casefold():
            raise ImportError("Row {row}: source and target must be different locations.", row=row_no)
        if row["Status"] not in STATUSES:
            raise ImportError("Row {row}: unsupported StoreHub transfer status: {status}.", row=row_no, status=row["Status"])
        info = dict(reference=row["S.T ID"], source_name=row["Source Store"], target_name=row["Target Store"], status=row["Status"],
                    sent_by=row.get("Sent By", ""), received_by=row.get("Received By", ""), cancelled_by=row.get("Cancelled By", ""), currency=currency)
        info.update({attr: _date(row.get(col, ""), row_no, col, required=col == "Created Date") for col, attr in DATE_FIELDS.items()})
        dates = [info[k] for k in ("shipped_at", "received_at", "cancelled_at") if info[k]]
        if any(d < info["created_sh"] for d in dates) or (info["shipped_at"] and info["received_at"] and info["received_at"] < info["shipped_at"]):
            raise ImportError("Row {row}: transfer dates are out of order.", row=row_no)
        ref = info["reference"]
        if ref not in groups:
            groups[ref] = {**info, "total": None, "lines": {}}
        group = groups[ref]
        if any(group[k] != v for k, v in info.items()):
            raise ImportError("Row {row}: conflicting header details for transfer {reference}.", row=row_no, reference=ref)
        total = _number(row.get(money_columns.get("Total", ""), ""), row_no, "Total")
        if total is not None:
            if group["total"] is not None and group["total"] != total:
                raise ImportError("Row {row}: conflicting totals for transfer {reference}.", row=row_no, reference=ref)
            group["total"] = total
        if not row["Product Name"]:
            if row["No."] or row["Ordered Qty"]:
                raise ImportError("Row {row}: a transfer item has no product name.", row=row_no)
            continue
        try:
            line_no = int(row["No."])
            if line_no <= 0:
                raise ValueError
        except ValueError:
            raise ImportError("Row {row}: item No. must be a positive integer.", row=row_no)
        line = dict(line_no=line_no, product_name=row["Product Name"], sku=row.get("SKU", ""), serial_no=row.get("Serial No.", ""),
                    category=row.get("Category", ""), quantity=_number(row["Ordered Qty"], row_no, "Ordered Qty", True), unit=row.get("Unit", ""),
                    unit_cost=_number(row.get(money_columns.get("Cost", ""), ""), row_no, "Cost"),
                    subtotal=_number(row.get(money_columns.get("SubTotal", ""), ""), row_no, "SubTotal"))
        if line_no in group["lines"] and group["lines"][line_no] != line:
            raise ImportError("Row {row}: conflicting item No. for transfer {reference}.", row=row_no, reference=ref)
        group["lines"][line_no] = line
    if not groups:
        raise ImportError("The CSV contains no stock transfers.")
    for group in groups.values():
        if not group["lines"] and not (group["status"] in ("Created", "Cancelled") and group["total"] == 0):
            raise ImportError("Transfer {reference} has no item details. Export the detailed stock-transfer report.", reference=group["reference"])
        group["lines"] = [group["lines"][k] for k in sorted(group["lines"])]
        group["payload_hash"] = hashlib.sha256(json.dumps(group, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    return list(groups.values())


def _index(records, key):
    index = {}
    for record in records:
        value = getattr(record, key)
        if value:
            index.setdefault(value.strip().casefold(), []).append(record.id)
    return index


def _unique(index, value):
    matches = index.get(value.strip().casefold(), [])
    return matches[0] if len(matches) == 1 else None


def plan(db: Session, raw: bytes, location_ids: list[int]) -> tuple[list[dict], dict]:
    groups = parse(raw)
    locations = list(db.scalars(select(Location)))
    names, ids = _index(locations, "name"), _index(locations, "storehub_id")
    products = list(db.scalars(select(Product)))
    skus, product_names = _index(products, "sku"), _index(products, "name")
    existing = {r.reference: r for r in db.scalars(select(StockTransfer).where(StockTransfer.reference.in_([g["reference"] for g in groups])))}
    counts = {"new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "lines": 0, "unmapped_locations": 0, "unmapped_products": 0, "renamed": 0}
    for group in groups:
        for side in ("source", "target"):
            value = group[side + "_name"]
            group[side + "_location_id"] = _unique(ids, value) or _unique(names, value)
        if group["source_location_id"] not in location_ids and group["target_location_id"] not in location_ids:
            raise ImportError("Transfer {reference}: neither location is within your assigned locations.", reference=group["reference"])
        if group["source_location_id"] is not None and group["source_location_id"] == group["target_location_id"]:
            raise ImportError("Transfer {reference}: source and target resolve to the same location.", reference=group["reference"])
        counts["unmapped_locations"] += sum(group[side + "_location_id"] is None for side in ("source", "target"))
        for line in group["lines"]:
            # A supplied SKU must match uniquely; never substitute a fuzzy name match.
            line["product_id"] = _unique(skus, line["sku"]) if line["sku"] else _unique(product_names, line["product_name"])
            counts["unmapped_products"] += line["product_id"] is None
        counts["lines"] += len(group["lines"])
        old = existing.get(group["reference"])
        if old and (old.source_name != group["source_name"] or old.target_name != group["target_name"]):
            # StoreHub is the source of truth and never reuses an S.T ID: a store rename must
            # update the mirror, not block the import. The new names change the payload hash,
            # so the transfer is applied as "updated" below.
            counts["renamed"] += 1
        group["action"] = "new" if old is None else "updated"
        if old:
            old_dates = [d for d in (old.created_sh, old.shipped_at, old.received_at, old.cancelled_at) if d]
            new_dates = [group[k] for k in ("created_sh", "shipped_at", "received_at", "cancelled_at") if group[k]]
            rank = {"Created": 0, "Shipped": 1, "Completed": 2, "Cancelled": 2}
            if max(new_dates) < max(old_dates) or rank[group["status"]] < rank[old.status] or (old.status in ("Completed", "Cancelled") and group["status"] != old.status):
                group["action"] = "skipped"
            elif old.payload_hash == group["payload_hash"]:
                group["action"] = "unchanged"
        counts[group["action"]] += 1
    return groups, counts


def apply(db: Session, source: StockTransferImport, user: User, location_ids: list[int], via: str | None = None) -> dict:
    if source.created_by != user.id:
        raise ImportError("This import preview belongs to another user.")
    if source.imported_at:
        return source.counts
    if source.created_at < now_utc() - timedelta(minutes=30):
        raise ImportError("This preview expired. Upload the export again.")
    groups, counts = plan(db, source.raw_csv, location_ids)
    for group in groups:
        if group["action"] in ("unchanged", "skipped"):
            continue
        rec = db.scalar(select(StockTransfer).where(StockTransfer.reference == group["reference"]).options(selectinload(StockTransfer.lines)))
        before = {"status": rec.status, "payload_hash": rec.payload_hash} if rec else None
        if rec is None:
            rec = StockTransfer(reference=group["reference"])
            db.add(rec)
        for key, value in group.items():
            if key not in ("action", "lines"):
                setattr(rec, key, value)
        rec.last_import_id = source.id
        # Delete old rows before inserting replacements under the unique line-number constraint.
        if rec.id:
            rec.lines.clear()
            db.flush()
        rec.lines = [StockTransferLine(**line) for line in group["lines"]]
        db.flush()
        audit.log(db, actor_type="human", actor_id=user.id, action="stock_transfer.import", entity_type="stock_transfer", entity_id=rec.id,
                  before=before, after={"reference": rec.reference, "status": rec.status, "payload_hash": rec.payload_hash, "lines": len(rec.lines)},
                  context={"import_id": source.id, "file_sha256": source.sha256, "via": via})
    source.imported_at, source.counts = now_utc(), counts
    audit.log(db, actor_type="human", actor_id=user.id, action="stock_transfer.import_file", entity_type="stock_transfer_import", entity_id=source.id,
              after=counts, context={"filename": source.filename, "sha256": source.sha256, "via": via})
    return counts
