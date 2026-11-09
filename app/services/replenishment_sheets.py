"""Save draft suggestion quantities and create local transfer sheets per store."""
import math

from decimal import Decimal

from sqlalchemy import func, select, update

from .. import audit
from ..models import InventoryCurrent, Location, Product, Recommendation, Supplier, TransferSheet
from ..timeutil import today_local
from . import po_drafts, replenishment, transfer_sheets


def quantity(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise replenishment.DecisionError("Enter a whole quantity above zero")
    if not math.isfinite(value) or not 0 < value <= transfer_sheets.MAX_QTY or not value.is_integer():
        raise replenishment.DecisionError("Enter a whole quantity above zero")
    return value


def save_quantity(db, rec, user, value):
    value = quantity(value)
    if rec.business_date != today_local() or rec.status != "pending" or rec.batch_id or rec.transfer_sheet_id:
        raise replenishment.DecisionError("This suggestion is no longer editable. Reload the page.")
    before = rec.saved_qty
    changed = db.execute(update(Recommendation).where(
        Recommendation.id == rec.id, Recommendation.status == "pending",
        Recommendation.batch_id.is_(None), Recommendation.transfer_sheet_id.is_(None),
    ).values(saved_qty=value))
    if changed.rowcount != 1:
        raise replenishment.DecisionError("This suggestion is no longer editable. Reload the page.")
    audit.log(db, actor_type="human", actor_id=user.id, action="repl.save_quantity",
              entity_type="recommendation", entity_id=rec.id,
              before={"saved_qty": before}, after={"saved_qty": value, "suggested_qty": rec.final_qty})


def create_sheets(db, user, ids, location_ids):
    """Caller owns the transaction: invalid selections roll back the entire operation."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise replenishment.DecisionError("Select at least one product")
    if len(ids) > 100:
        raise replenishment.DecisionError("Select no more than 100 products at a time")
    recs = list(db.scalars(select(Recommendation).where(Recommendation.id.in_(ids)).order_by(Recommendation.id)))
    if len(recs) != len(ids) or any(r.location_id not in location_ids for r in recs):
        raise replenishment.DecisionError("Some selected suggestions are unavailable. Reload the page.")
    source = transfer_sheets.warehouse(db)
    if source is None:
        raise transfer_sheets.SheetError("No warehouse is set up in the system")
    for r in recs:
        if (r.business_date != today_local() or r.source_kind != "transfer" or r.location.type != "store"
                or r.status not in ("pending", "approved", "modified") or r.batch_id or r.transfer_sheet_id):
            raise replenishment.DecisionError("Some selected suggestions were already processed or need review. Reload the page.")
        quantity(r.saved_qty if r.status == "pending" and r.saved_qty is not None else
                 r.final_qty if r.status == "pending" else r.approved_qty)
    sheets = {}
    for r in recs:
        if r.location_id not in sheets:
            sheet = TransferSheet(source_location_id=source.id, target_location_id=r.location_id,
                                  created_by=user.id, note="Created from order suggestions")
            db.add(sheet)
            db.flush()
            sheets[r.location_id] = sheet
            audit.log(db, actor_type="human", actor_id=user.id, action="transfer_sheet.create",
                      entity_type="transfer_sheet", entity_id=sheet.id,
                      after={"source": source.name, "target": r.location.name, "origin": "replenishment"})
        sheet = sheets[r.location_id]
        # Claim each recommendation in the same transaction as sheet creation.
        claim = db.execute(update(Recommendation).where(
            Recommendation.id == r.id, Recommendation.status == r.status,
            Recommendation.transfer_sheet_id.is_(None), Recommendation.batch_id.is_(None),
        ).values(transfer_sheet_id=sheet.id))
        if claim.rowcount != 1:
            raise replenishment.DecisionError("Some selected suggestions were already processed or need review. Reload the page.")
        db.refresh(r)
        if r.status == "pending":
            replenishment.decide(db, r, user, "approve", quantity(r.saved_qty if r.saved_qty is not None else r.final_qty))
        transfer_sheets.add_line(db, sheet, r.product, quantity(r.approved_qty))
        r.status = "ordered"
        audit.log(db, actor_type="human", actor_id=user.id, action="repl.transfer_sheet",
                  entity_type="recommendation", entity_id=r.id,
                  after={"sheet_id": sheet.id, "quantity": r.approved_qty, "location_id": r.location_id})
    db.flush()
    return list(sheets.values())


def plan(db, recs):
    """For each product, what the warehouse can send and what is left to order from the supplier.
    Returns {product_id: {"recs": [(rec, quantity, send)], "need", "send", "order"}}. The warehouse stock is shared between the stores in order."""
    wh_ids = [l.id for l in db.scalars(select(Location).where(Location.type == "warehouse", Location.source == "storehub"))]
    stock = {pid: float(n or 0) for pid, n in db.execute(select(InventoryCurrent.product_id, func.sum(InventoryCurrent.on_hand)).where(
        InventoryCurrent.location_id.in_(wh_ids or [0])).group_by(InventoryCurrent.product_id))} if wh_ids else {}
    out = {}
    for r in sorted(recs, key=lambda x: (x.product_id, x.location_id)):
        q = quantity(r.saved_qty if r.status == "pending" and r.saved_qty is not None else r.final_qty if r.status == "pending" else r.approved_qty)
        item = out.setdefault(r.product_id, {"product": r.product, "recs": [], "need": 0.0})
        item["recs"].append([r, q, 0.0])
        item["need"] += q
    for pid, item in out.items():
        available = float(int(max(stock.get(pid, 0.0), 0.0))) if wh_ids and pid in stock else (item["need"] if not wh_ids else 0.0)
        left = min(available, item["need"])
        item["send"] = left
        for row in item["recs"]:
            row[2] = min(row[1], left)
            left -= row[2]
        item["order"] = item["need"] - item["send"]
    return out


def proceed(db, user, ids, location_ids, can_sheet, can_po):
    """One action for the selected products: what the warehouse has goes on transfer sheets (one per store), what it does not have goes on a purchase order
    to the product's supplier (the open purchase order of this person, delivered to the warehouse). Caller owns the transaction."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise replenishment.DecisionError("Select at least one product")
    if len(ids) > 100:
        raise replenishment.DecisionError("Select no more than 100 products at a time")
    recs = list(db.scalars(select(Recommendation).where(Recommendation.id.in_(ids)).order_by(Recommendation.id)))
    if len(recs) != len(ids) or any(r.location_id not in location_ids for r in recs):
        raise replenishment.DecisionError("Some selected suggestions are unavailable. Reload the page.")
    for r in recs:
        if (r.business_date != today_local() or r.source_kind != "transfer" or r.location.type != "store"
                or r.status not in ("pending", "approved", "modified") or r.batch_id or r.transfer_sheet_id):
            raise replenishment.DecisionError("Some selected suggestions were already processed or need review. Reload the page.")
    work = plan(db, recs)
    needs_sheet = any(x["send"] > 0 for x in work.values())
    needs_po = any(x["order"] > 0 for x in work.values())
    if needs_sheet and not can_sheet:
        raise replenishment.DecisionError("Your role does not allow transfer sheet creation")
    if needs_po and not can_po:
        raise replenishment.DecisionError("Your role does not allow creating purchase orders")
    # the purchase order first: it is the part that can be refused (no supplier, another supplier already open)
    draft, po_lines = None, 0
    if needs_po:
        order = {pid: x for pid, x in work.items() if x["order"] > 0}
        sup_ids = {x["product"].supplier_id for x in order.values()}
        missing = [x["product"].name for x in order.values() if x["product"].supplier_id is None]
        if missing:
            raise replenishment.DecisionError("No supplier is set for: " + ", ".join(missing[:3]) + (" ..." if len(missing) > 3 else "") + ". Set one, or order it from the purchase order page.")
        if len(sup_ids) > 1:
            raise replenishment.DecisionError("The products to order belong to more than one supplier. Pick one supplier in the filter and select its products.")
        supplier = db.get(Supplier, next(iter(sup_ids)))
        wh = transfer_sheets.warehouse(db)
        if wh is None:
            raise transfer_sheets.SheetError("No warehouse is set up in the system")
        open_draft = po_drafts.current(db, user)
        if open_draft is not None and open_draft.supplier_id != supplier.id:
            raise replenishment.DecisionError("You already have an open purchase order for another supplier. Save or discard it first.")
        from .. import settings_service as cfg
        from . import order_documents as docs
        draft = po_drafts.start(db, user, supplier.name, wh.id, [wh.id], docs.plain(Decimal(str(cfg.get(db, "product.vat_rate") or 12))))
        last = po_drafts.last_prices(db, supplier.id)
        for x in order.values():
            prod = x["product"]
            price = last[prod.id][0] if prod.id in last else (po_drafts.price6(Decimal(repr(float(prod.cost)))) if prod.cost else None)
            if price is None:
                raise replenishment.DecisionError(f"No price is known for {prod.name}. Add it on the purchase order page.")
            po_drafts.add_line(db, draft, prod, str(int(x["order"])), price)
            po_lines += 1
    # then the transfer sheets, for what the warehouse can send
    source = transfer_sheets.warehouse(db)
    sheets = {}
    for x in work.values():
        for r, q, send in x["recs"]:
            if send > 0:
                if source is None:
                    raise transfer_sheets.SheetError("No warehouse is set up in the system")
                if r.location_id not in sheets:
                    sheet = TransferSheet(source_location_id=source.id, target_location_id=r.location_id, created_by=user.id, note="Created from order suggestions")
                    db.add(sheet)
                    db.flush()
                    sheets[r.location_id] = sheet
                    audit.log(db, actor_type="human", actor_id=user.id, action="transfer_sheet.create", entity_type="transfer_sheet", entity_id=sheet.id,
                              after={"source": source.name, "target": r.location.name, "origin": "replenishment"})
                sheet = sheets[r.location_id]
                claim = db.execute(update(Recommendation).where(Recommendation.id == r.id, Recommendation.status == r.status, Recommendation.transfer_sheet_id.is_(None),
                                                                Recommendation.batch_id.is_(None)).values(transfer_sheet_id=sheet.id))
                if claim.rowcount != 1:
                    raise replenishment.DecisionError("Some selected suggestions were already processed or need review. Reload the page.")
                db.refresh(r)
            if r.status == "pending":
                replenishment.decide(db, r, user, "approve", send if send > 0 else q)
            if send > 0:
                transfer_sheets.add_line(db, sheets[r.location_id], r.product, send)
            r.status = "ordered"
            audit.log(db, actor_type="human", actor_id=user.id, action="repl.proceed", entity_type="recommendation", entity_id=r.id,
                      after={"sheet_id": r.transfer_sheet_id, "send": send, "to_order": q - send, "location_id": r.location_id,
                             "po_draft_id": draft.id if draft is not None and q - send > 0 else None})
    db.flush()
    return {"sheets": list(sheets.values()), "draft": draft, "po_lines": po_lines}
