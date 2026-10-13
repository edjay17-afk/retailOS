"""Real supplier names. StoreHub's API only gives a supplier code on each PO, but the back-office "Purchase Orders" export lists the
supplier name next to every PO number. Matching on the PO number turns each code into its real name."""
from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import PurchaseOrder, Supplier


def _po_number(v: str) -> int | None:
    m = re.search(r"(\d+)", v or "")
    return int(m.group(1)) if m else None


def parse_po_export(text: str) -> dict[int, str]:
    """PO number -> supplier name, from the StoreHub back-office export (columns 'P.O ID' and 'Supplier')."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows or not {"P.O ID", "Supplier"} <= set(rows[0].keys()):
        raise ValueError("This does not look like the StoreHub purchase order export: the columns 'P.O ID' and 'Supplier' are missing.")
    out: dict[int, str] = {}
    for r in rows:
        n, name = _po_number(r["P.O ID"]), (r["Supplier"] or "").strip()
        if n is not None and name:
            out[n] = name
    return out


def apply_po_export(db: Session, text: str, actor_id: str | int | None = None) -> dict:
    """Name suppliers from the export. Names typed by a person are never overwritten; a code that maps to two different names is skipped."""
    po_names = parse_po_export(text)
    votes: dict[int, Counter] = defaultdict(Counter)
    matched = 0
    for po in db.scalars(select(PurchaseOrder)):
        if po.sh_po_id in po_names and po.supplier_id:
            votes[po.supplier_id][po_names[po.sh_po_id]] += 1
            matched += 1
    renamed, kept_manual, conflicts = 0, 0, []
    for sid, cnt in votes.items():
        sup = db.get(Supplier, sid)
        if len(cnt) > 1:
            conflicts.append(sup.name)
            continue
        name = next(iter(cnt))[:160]
        if sup.name_source == "manual":
            kept_manual += 1
            continue
        if sup.name != name:
            audit.log(db, actor_type="system", actor_id=actor_id or "po-export-import", action="supplier.rename", entity_type="supplier", entity_id=sid,
                      before={"name": sup.name}, after={"name": name}, reason="matched by PO number from the StoreHub PO export")
            sup.name = name
        sup.name_source = "export"
        renamed += 1
    still = [s.name for s in db.scalars(select(Supplier)) if s.name_source == "auto"]
    return {"pos_in_file": len(po_names), "pos_matched": matched, "suppliers_named": renamed, "kept_manual": kept_manual, "conflicts": conflicts, "still_unnamed": len(still)}
