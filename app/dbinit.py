"""Create the schema, install the append-only triggers, and seed defaults."""
from __future__ import annotations

import re

from sqlalchemy import inspect, select, text

from . import models  # noqa: F401  (register models)
from .db import Base, SessionLocal, get_engine
from .models import InspectionTemplate, SchemaVersion, TemplateItem
from .timeutil import now_utc

CURRENT_VERSION = 16

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
]

TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS audit_event_no_update BEFORE UPDATE ON audit_event
       BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;""",
    """CREATE TRIGGER IF NOT EXISTS audit_event_no_delete BEFORE DELETE ON audit_event
       BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;""",
]


def _already_applied(stmt: str) -> bool:
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
        ("Aircon Temp", "number", None, None, "C", "P3", False),  # a reading to record; no allowed range was given
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
