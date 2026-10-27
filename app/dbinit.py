"""Create the schema, install the append-only triggers, and seed defaults."""
from __future__ import annotations

import re

from sqlalchemy import inspect, select, text

from . import models  # noqa: F401  (register models)
from .db import Base, SessionLocal, get_engine
from .models import InspectionTemplate, SchemaVersion, TemplateItem
from .timeutil import now_utc

CURRENT_VERSION = 42

# (version, [sql, ...]) applied in order to databases created by an earlier version.
MIGRATIONS: list[tuple[int, list[str]]] = [
    (2, ["ALTER TABLE inspection ADD COLUMN created_at DATETIME"]),
    (3, []),  # AI layer: new tables only (created automatically)
    (4, ["ALTER TABLE recommendation ADD COLUMN received_qty FLOAT",
         "ALTER TABLE notification ADD COLUMN lark_sent_at DATETIME",
         "ALTER TABLE order_batch ADD COLUMN dispatched_by INTEGER",
         "ALTER TABLE order_batch ADD COLUMN dispatched_at DATETIME",
         "ALTER TABLE order_batch ADD COLUMN received_by INTEGER",
         "ALTER TABLE order_batch ADD COLUMN received_at DATETIME"]),
    (5, ["ALTER TABLE receiving ADD COLUMN photo_path VARCHAR(300)"]),
    (6, []),  # inspection templates: one Store Daily Inspection, "Warehouse Inspection" (data change, see _migrate_templates_v6)
    (7, []),  # stock events: new table only (created automatically by create_all, like v3)
    (8, ["ALTER TABLE user ADD COLUMN password_plain VARCHAR(100)"]),
    (9, []),  # StoreHub stock-transfer export mirror and retained import sources (new tables)
    (10, ["ALTER TABLE user ADD COLUMN deleted_at DATETIME"]),
    (11, ["ALTER TABLE inspection_result ADD COLUMN photo_paths JSON"]),
    (12, []),  # editable PO/transfer print copies; create_all adds the new table
    (13, ["ALTER TABLE compliance_item ADD COLUMN processed_date DATE"]),  # permits: when the permit was processed (the expiry is due_date)
    # permits without an expiry date: due_date may be empty, so the table is rebuilt (SQLite cannot drop NOT NULL in place)
    (14, ["DROP INDEX IF EXISTS ix_compliance_item_due_date",
          "ALTER TABLE compliance_item RENAME TO compliance_item_old",
          """CREATE TABLE compliance_item (id INTEGER NOT NULL, kind VARCHAR(14) NOT NULL, name VARCHAR(160) NOT NULL, location_id INTEGER, owner_id INTEGER, due_date DATE,
             notes TEXT, status VARCHAR(10) NOT NULL, reference VARCHAR(80), document_path VARCHAR(300), created_at DATETIME NOT NULL, processed_date DATE,
             PRIMARY KEY (id), FOREIGN KEY(location_id) REFERENCES location (id), FOREIGN KEY(owner_id) REFERENCES user (id))""",
          "INSERT INTO compliance_item (id, kind, name, location_id, owner_id, due_date, notes, status, reference, document_path, created_at, processed_date) "
          "SELECT id, kind, name, location_id, owner_id, due_date, notes, status, reference, document_path, created_at, processed_date FROM compliance_item_old",
          "DROP TABLE compliance_item_old",
          "CREATE INDEX ix_compliance_item_due_date ON compliance_item (due_date)"]),
    (15, []),  # StoreHub wastage records mirror: new tables only (created automatically)
    (16, []),  # V2.1 inventory_event trace table: new table only (created automatically)
    (17, []),  # V2.1 store receipt confirmation on StoreHub transfers: new tables only (created automatically)
    (18, []),  # new-product registration (Lark New Product table + RPA Excel): new table only (created automatically)
    (19, ["ALTER TABLE new_product ADD COLUMN expiry_date DATE"]),  # the Lark table tracks Expiration Date
    (20, ["ALTER TABLE new_product ADD COLUMN entry_type VARCHAR(10) DEFAULT 'new'",  # product entry flow: new / existing, units and pack maths
          "ALTER TABLE new_product ADD COLUMN brand VARCHAR(120)",
          "ALTER TABLE new_product ADD COLUMN flavor VARCHAR(120)",
          "ALTER TABLE new_product ADD COLUMN unit VARCHAR(12)",
          "ALTER TABLE new_product ADD COLUMN units_per_pack FLOAT",
          "ALTER TABLE new_product ADD COLUMN pack_price FLOAT",
          "ALTER TABLE new_product ADD COLUMN packs FLOAT",
          "ALTER TABLE new_product ADD COLUMN product_id INTEGER REFERENCES product(id)"]),
    (21, []),  # warehouse transfer sheets (scan + quantity): new tables only (created automatically)
    (22, ["ALTER TABLE transfer_sheet_line RENAME TO transfer_sheet_line_old",  # a line may be a product that is not in StoreHub yet: product_id becomes optional
          "CREATE TABLE transfer_sheet_line (id INTEGER NOT NULL PRIMARY KEY, sheet_id INTEGER NOT NULL REFERENCES transfer_sheet (id) ON DELETE CASCADE, "
          "product_id INTEGER REFERENCES product (id), manual_name VARCHAR(300), manual_barcode VARCHAR(80), qty FLOAT NOT NULL, unit_cost FLOAT, "
          "CONSTRAINT uq_transfer_sheet_line UNIQUE (sheet_id, product_id))",
          "INSERT INTO transfer_sheet_line (id, sheet_id, product_id, qty, unit_cost) SELECT id, sheet_id, product_id, qty, unit_cost FROM transfer_sheet_line_old",
          "DROP TABLE transfer_sheet_line_old",
          "CREATE INDEX IF NOT EXISTS ix_transfer_sheet_line_sheet_id ON transfer_sheet_line (sheet_id)"]),
    (23, ["UPDATE template_item SET unit = NULL WHERE label = 'Aircon Temp' AND template_id IN (SELECT id FROM inspection_template WHERE code = 'store_daily')"]),  # the store inspection no longer shows a Celsius unit
    (24, ["DELETE FROM template_item WHERE label = 'Aircon Temp' AND template_id IN (SELECT id FROM inspection_template WHERE code = 'store_daily') AND id NOT IN (SELECT item_id FROM inspection_result)"]),  # the store inspection is pictures and a video only: no typed temperature reading
    (25, ["ALTER TABLE inspection_result ADD COLUMN video_paths JSON"]),  # a store walkthrough can have one or more videos
    (26, []),  # new-product lists (scan + name, supplier, category, quantity -> PDF): new tables only (created automatically)
    (27, ["ALTER TABLE new_product_list RENAME COLUMN pdf_path TO excel_path"]),  # a saved new-product list is an Excel file for the RPA, not a PDF
    (28, ["UPDATE po_line SET supplier_price = subtotal / ordered_qty WHERE supplier_price IS NOT NULL AND ordered_qty > 0 AND subtotal IS NOT NULL AND ROUND(subtotal / ordered_qty, 2) = ROUND(supplier_price, 2) AND ABS(subtotal / ordered_qty - supplier_price) > 0.000000001"]),  # StoreHub sends the supplier price rounded; the exact price is in the line subtotal
    (29, ["UPDATE po_line SET supplier_price = ROUND(ROUND(supplier_price, 2) * 1.12, 2) / 1.12 WHERE supplier_price IS NOT NULL AND ordered_qty > 0 AND subtotal IS NOT NULL AND ROUND(ROUND(ROUND(supplier_price, 2) * 1.12, 2) / 1.12, 2) = ROUND(supplier_price, 2) AND ABS(ROUND(ROUND(supplier_price, 2) * 1.12, 2) / 1.12 * ordered_qty - subtotal) <= 0.0051"]),  # exact PO supplier price: whole-cents price with 12% tax divided back out, as StoreHub has it (254.464285 = 285 / 1.12)
    (30, []),  # purchase-order drafts (supplier, deliver to, products with exact quantity and price): new tables only (created automatically)
    (31, ["DELETE FROM transfer_sheet_line WHERE sheet_id IN (SELECT id FROM transfer_sheet WHERE status = 'cancelled')",
          "DELETE FROM transfer_sheet WHERE status = 'cancelled'"]),  # a discarded sheet is deleted, not hidden: the numbers restart from the sheets that really exist
    (32, ["ALTER TABLE po_draft ADD COLUMN tax_rate VARCHAR(20)"]),  # purchase-order drafts carry their tax percent (12)
    (33, ["ALTER TABLE sales_day ADD COLUMN gross_profit FLOAT"]),  # StoreHub's Gross Profit per store per day (its API has no item costs; its report does)
    (34, ["ALTER TABLE po_line ADD COLUMN price_exact VARCHAR(40)"]),  # the exact supplier price StoreHub holds (its API sends it rounded to 2 decimals)
    (35, ["ALTER TABLE new_product_item ADD COLUMN price FLOAT",  # new-product lists can be pushed straight to StoreHub (POST /products needs a price)
          "ALTER TABLE new_product_item ADD COLUMN cost FLOAT",
          "ALTER TABLE new_product_item ADD COLUMN status VARCHAR(12) DEFAULT 'pending'",
          "ALTER TABLE new_product_item ADD COLUMN storehub_product_id VARCHAR(40)",
          "ALTER TABLE new_product_item ADD COLUMN push_error VARCHAR(300)"]),
    (36, []),  # StoreHub push registry (what went in, when, by whom): new table only (created automatically)
    (37, ["ALTER TABLE po_draft ADD COLUMN new_product_ids JSON"]),  # products quick-created from the order: split between the stores on save
    (38, ["ALTER TABLE recommendation ADD COLUMN saved_qty FLOAT",
          "ALTER TABLE recommendation ADD COLUMN transfer_sheet_id INTEGER REFERENCES transfer_sheet(id) ON DELETE SET NULL"]),
    (39, []),  # Loyverse isolated read-only catalog and sales tables
    (40, ["ALTER TABLE employee ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'storehub'", "ALTER TABLE employee ADD COLUMN source_account VARCHAR(80) NOT NULL DEFAULT ''", 'ALTER TABLE employee ADD COLUMN external_id VARCHAR(120)', "ALTER TABLE location ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'storehub'", "ALTER TABLE location ADD COLUMN source_account VARCHAR(80) NOT NULL DEFAULT ''", 'ALTER TABLE location ADD COLUMN external_id VARCHAR(120)', "ALTER TABLE supplier ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'storehub'", "ALTER TABLE supplier ADD COLUMN source_account VARCHAR(80) NOT NULL DEFAULT ''", 'ALTER TABLE supplier ADD COLUMN external_id VARCHAR(120)', "ALTER TABLE product ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'storehub'", "ALTER TABLE product ADD COLUMN source_account VARCHAR(80) NOT NULL DEFAULT ''", 'ALTER TABLE product ADD COLUMN external_id VARCHAR(120)', 'ALTER TABLE product ADD COLUMN image_url TEXT', 'ALTER TABLE loyverse_account ADD COLUMN reporting_catalog_at DATETIME', 'ALTER TABLE loyverse_account ADD COLUMN currency VARCHAR(3)', 'ALTER TABLE loyverse_account ADD COLUMN reporting_history_from DATE', 'ALTER TABLE loyverse_account ADD COLUMN reporting_error VARCHAR(200)', 'ALTER TABLE loyverse_account ADD COLUMN reporting_success_at DATETIME']),
    (41, []),  # StoreHub shifts (cashier, register, payments, opening fund, withdrawals) read from its Shift Reports: new table only (created automatically)
    (42, []),  # Official StoreHub Z-reading list/detail snapshots: new table only (created automatically)
]

TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS audit_event_no_update BEFORE UPDATE ON audit_event
       BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;""",
    """CREATE TRIGGER IF NOT EXISTS audit_event_no_delete BEFORE DELETE ON audit_event
       BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;""",
]


def _already_applied(stmt: str) -> bool:
    r = re.match(r"ALTER TABLE (\w+) RENAME COLUMN (\w+) TO (\w+)", stmt, re.I)
    if r:  # a rename is done when the old name is gone
        return r.group(2) not in {c["name"] for c in inspect(get_engine()).get_columns(r.group(1))}
    m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", stmt, re.I)
    if not m:
        return False
    return m.group(2) in {c["name"] for c in inspect(get_engine()).get_columns(m.group(1))}


def init_db() -> None:
    engine = get_engine()
    fresh = "schema_version" not in inspect(engine).get_table_names()  # a brand new database already has the latest schema
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for t in TRIGGERS:
            conn.execute(text(t))
    with SessionLocal() as db:
        have = db.scalar(select(SchemaVersion.version).order_by(SchemaVersion.version.desc()).limit(1)) or 0
        if fresh:
            have = CURRENT_VERSION
        was = have
        for ver, stmts in MIGRATIONS:
            if ver > have:
                for s in stmts:
                    if _already_applied(s):
                        continue  # idempotent: a column that already exists is not added twice
                    db.execute(text(s))
                db.add(SchemaVersion(version=ver, applied_at=now_utc()))
                have = ver
        if db.get(SchemaVersion, CURRENT_VERSION) is None:
            db.add(SchemaVersion(version=CURRENT_VERSION, applied_at=now_utc()))
        db.commit()
        seed_templates(db)
        db.flush()
        if was < 6:
            _migrate_templates_v6(db)
        db.commit()


# ------------------------------------------------------------------ inspection templates (from the proposal)
def _items(rows):
    return [TemplateItem(sort=i, label=r[0], kind=r[1], min_value=r[2], max_value=r[3], unit=r[4],
                         fail_severity=r[5], photo_on_fail=r[6]) for i, r in enumerate(rows)]


def C(label, sev="P2", photo=True):
    return (label, "check", None, None, None, sev, photo)


def N(label, lo, hi, unit, sev="P1"):
    return (label, "number", lo, hi, unit, sev, True)


def V(label, sev="P3"):
    """A video the person records and attaches; there is no pass or fail, it only has to be sent."""
    return (label, "video", None, None, None, sev, False)


TEMPLATES = [
    ("store_daily", "Store Daily Inspection", "store", "daily", "12:00", [  # to be sent before 12 noon
        C("Store Entrance", "P3"),
        C("Counter Area", "P3"),
        C("Restroom", "P2"),
        C("Floor & Table Cleanliness", "P3"),
        C("All Machines (All)", "P2"),
        C("Shelf Stocks (All including chillers and refs)", "P2"),
        V("Video Walkthrough"),
    ]),
    ("warehouse_daily", "Warehouse Inspection", "warehouse", "daily", "12:00", [
        C("Receiving inspection against PO: quantity, damage, expiry, chilled temperature", "P2"),
        N("Storage temperature", 15, 30, "C", "P2"),
        N("Humidity", 30, 70, "%", "P2"),
        C("No leaks or pest signs; traps checked", "P1"),
        C("FIFO order kept; near-expiry batches set aside", "P2"),
        C("Damaged or returned goods separated and logged", "P3"),
        C("Daily cycle count completed", "P2", False),
        C("Aisles clear, safe stacking, dry floors", "P2"),
        C("Fire equipment, lighting and locks in order", "P1"),
    ]),
]

# the old three store checklists were replaced by the single daily inspection
RETIRED_TEMPLATES = ("store_opening", "store_midday", "store_closing")


def _migrate_templates_v6(db) -> None:
    """Existing databases: switch off the old opening, midday and closing checklists, rename the warehouse one, and remove
    today's and future untouched tasks of the retired ones so nobody is asked to do (or alerted about) a checklist that no longer exists."""
    from .models import Inspection, Issue
    from .timeutil import today_local
    for t in db.scalars(select(InspectionTemplate)):
        if t.code in RETIRED_TEMPLATES:
            t.active = False
        elif t.code == "warehouse_daily" and t.name == "Warehouse daily checklist":
            t.name = "Warehouse Inspection"
    db.flush()
    retired = [t.id for t in db.scalars(select(InspectionTemplate).where(InspectionTemplate.code.in_(RETIRED_TEMPLATES)))]
    if not retired:
        return
    for i in db.scalars(select(Inspection).where(Inspection.template_id.in_(retired), Inspection.business_date >= today_local(),
                                                 Inspection.status.in_(("pending", "missed", "in_progress")), Inspection.started_at.is_(None))):
        for iss in db.scalars(select(Issue).where(Issue.dedupe_key == f"missed:{i.id}")):
            iss.status = "closed"
        db.delete(i)


def seed_templates(db) -> None:
    for code, name, ltype, slot, due, items in TEMPLATES:
        if db.scalar(select(InspectionTemplate).where(InspectionTemplate.code == code)):
            continue
        db.add(InspectionTemplate(code=code, name=name, location_type=ltype, slot=slot, due_time=due, items=_items(items)))
