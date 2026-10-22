"""Save draft suggestion quantities and create local transfer sheets per store."""
import math

from sqlalchemy import select, update

from .. import audit
from ..models import Recommendation, TransferSheet
from ..timeutil import today_local
from . import replenishment, transfer_sheets


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
