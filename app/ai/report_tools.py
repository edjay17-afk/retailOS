"""Bounded, permission-scoped report lookups for the assistant."""
from datetime import date, timedelta

from sqlalchemy import select, func

from ..models import Product, SalesDay, SalesDayProduct, Supplier, TransferSheet, ExpiryBatch, CycleCount, Location
from ..security import allowed_location_ids
from ..timeutil import today_local


def locations(db, user, args):
    from .tools import _loc
    ref = args.get("location", args.get("loc"))
    return [_loc(db, user, ref).id] if ref not in (None, "", 0, "0") else allowed_location_ids(db, user)


def period(args):
    from .tools import ToolError
    try:
        end = date.fromisoformat(args["to"]) if args.get("to") else today_local()
        start = date.fromisoformat(args["frm"]) if args.get("frm") else end - timedelta(days=min(max(int(args.get("days", 14)), 1), 365) - 1)
        if start > end or (end-start).days > 400 or end > today_local():
            raise ValueError()
        return start, end
    except (ValueError, TypeError):
        raise ToolError("Use a valid date range, no more than 401 days, ending no later than today.")


def product_sales(db, user, args):
    ids = locations(db, user, args)
    start, end = period(args)
    query = str(args.get("query", args.get("q", "")))[:150].strip()
    stmt = select(Product.id, Product.name, func.sum(SalesDayProduct.units).label("units"), func.sum(SalesDayProduct.net_sales).label("net")).join(Product, Product.id == SalesDayProduct.product_id).where(
        SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.business_date.between(start, end))
    if query:
        stmt = stmt.where(Product.name.ilike(f"%{query}%") | Product.barcode.ilike(f"%{query}%") | Product.sku.ilike(f"%{query}%"))
    if args.get("product_id"):
        stmt = stmt.where(Product.id == int(args["product_id"]))
    if args.get("supplier") not in (None, "", "0", 0):
        stmt = stmt.where(Product.supplier_id == int(args["supplier"]))
    if args.get("category"):
        stmt = stmt.where(Product.category == str(args["category"])[:150])
    grouped = stmt.group_by(Product.id, Product.name).subquery()
    count, units, net = db.execute(select(func.count(), func.sum(grouped.c.units), func.sum(grouped.c.net))).one()
    facts = [f"Filtered product sales from {start} to {end}, selected location IDs {ids}: {count} products; total units {units or 0:g}; net sales PHP {net or 0:.2f}. Returns deducted; cancelled sales excluded."]
    order = {"units": grouped.c.units.desc(), "name": grouped.c.name.asc(), "price": (grouped.c.net / func.nullif(grouped.c.units, 0)).desc()}.get(args.get("sort"), grouped.c.net.desc())
    rows = db.execute(select(grouped).order_by(order, grouped.c.name).limit(20)).all()
    facts += [f"Product ID {pid}, {name}: {u:g} units; net sales PHP {n:.2f}, {start} to {end}." for pid, name, u, n in rows]
    if count > len(rows):
        facts.append(f"Showing {len(rows)} of {count} matching products; the totals cover all matching products.")
    if args.get("product_id"):
        daily = db.execute(select(SalesDayProduct.business_date, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).where(
            SalesDayProduct.product_id == int(args["product_id"]), SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.business_date.between(start, end)).group_by(SalesDayProduct.business_date).order_by(SalesDayProduct.business_date.desc()).limit(31))
        facts += [f"Product ID {args['product_id']} on {d}: {u:g} units, net sales PHP {n:.2f}." for d, u, n in daily]
        facts.append("Daily breakdown includes up to 31 most recent dates with recorded sales; period totals include the full range.")
    return {"facts": facts}


def supplier_sales(db, user, args):
    ids = locations(db, user, args)
    start, end = period(args)
    stmt = select(Supplier.name, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).select_from(SalesDayProduct).join(Product, Product.id == SalesDayProduct.product_id).outerjoin(Supplier, Supplier.id == Product.supplier_id).where(SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.business_date.between(start, end))
    rows = db.execute(stmt.group_by(Supplier.id, Supplier.name).order_by(func.sum(SalesDayProduct.net_sales).desc()).limit(20))
    facts = [f"Supplier {name or 'Unassigned'}, {start} to {end}: {u:g} units, net sales PHP {n:.2f}." for name, u, n in rows]
    return {"facts": facts + ["Supplier report uses each product's current assigned supplier; at most 20 suppliers shown."]}


def sales_history(db, user, args):
    ids = locations(db, user, args)
    start, end = period(args)
    stmt = select(func.sum(SalesDay.net_sales), func.sum(SalesDay.tx_count)).where(SalesDay.location_id.in_(ids or [0]))
    net, transactions = db.execute(stmt.where(SalesDay.business_date.between(start, end))).one()
    span = (end-start).days + 1
    prev_start, prev_end = start - timedelta(days=span), start - timedelta(days=1)
    prev_net, prev_transactions = db.execute(stmt.where(SalesDay.business_date.between(prev_start, prev_end))).one()
    facts = [f"Selected sales {start} to {end}: net sales PHP {net or 0:.2f}, {transactions or 0} transactions.",
             f"Previous equal period {prev_start} to {prev_end}: net sales PHP {prev_net or 0:.2f}, {prev_transactions or 0} transactions."]
    if end == today_local():
        facts.append("The selected period includes today, which is incomplete; a full-day comparison may be misleading.")
    rows = db.execute(select(SalesDay.business_date, func.sum(SalesDay.net_sales), func.sum(SalesDay.tx_count)).where(
        SalesDay.location_id.in_(ids or [0]), SalesDay.business_date.between(start, end)).group_by(SalesDay.business_date).order_by(SalesDay.business_date.desc()).limit(31))
    facts += [f"{day}: net sales PHP {n:.2f}, {tx} transactions." for day, n, tx in rows]
    return {"facts": facts}


def transfers(db, user, args):
    ids = locations(db, user, args)
    stmt = select(TransferSheet).where(TransferSheet.target_location_id.in_(ids or [0]))
    if args.get("sheet_id"):
        stmt = stmt.where(TransferSheet.id == int(args["sheet_id"]))
    rows = list(db.scalars(stmt.order_by(TransferSheet.id.desc()).limit(15)))
    facts = []
    for row in rows:
        facts.append(f"TS-{row.id:05d}: {row.source.name} to {row.target.name}, {row.status}, {len(row.lines)} products, {sum(l.qty for l in row.lines):g} units. Open /transfer-sheets/{row.id}.")
        if args.get("sheet_id"):
            facts += [f"TS-{row.id:05d}: {line.product.name}, quantity {line.qty:g}." for line in row.lines[:30]]
    return {"facts": facts or ["No matching transfer sheets in your accessible stores."]}


def expiry(db, user, args):
    ids = locations(db, user, args)
    end = today_local() + timedelta(days=min(max(int(args.get("days", 30)), 0), 365))
    rows = db.scalars(select(ExpiryBatch).where(ExpiryBatch.location_id.in_(ids or [0]), ExpiryBatch.status == "active", ExpiryBatch.expiry_date <= end).order_by(ExpiryBatch.expiry_date).limit(25))
    return {"facts": [f"Batch #{r.id}, {r.product.name} at {r.location.name}: {r.qty:g} units expire {r.expiry_date}; batch {r.batch_no or 'not recorded'}." for r in rows] or ["No matching active expiry batches; this does not establish that unrecorded stock is safe."]}


def counts(db, user, args):
    ids = locations(db, user, args)
    stmt = select(CycleCount).where(CycleCount.location_id.in_(ids or [0]))
    if args.get("count_id"):
        stmt = stmt.where(CycleCount.id == int(args["count_id"]))
    facts = []
    for row in db.scalars(stmt.order_by(CycleCount.id.desc()).limit(15)):
        counted = [l for l in row.lines if l.counted_qty is not None]
        facts.append(f"Count #{row.id}, {row.location.name}, {row.business_date}: {row.status}, {len(counted)} counted products, {sum(1 for l in counted if l.variance)} differences. Open /counts/{row.id}.")
        if args.get("count_id"):
            facts += [f"{l.product.name}: system {l.system_qty:g}, counted {l.counted_qty:g}, difference {l.variance or 0:g}." for l in counted[:30]]
    return {"facts": facts or ["No matching stock counts in your accessible stores."]}


def lark_reports(db, user, args):
    from ..services import lark_inspections
    from .tools import ToolError
    ids = locations(db, user, args)
    day = date.fromisoformat(args["date"]) if args.get("date") else today_local()
    try:
        result = lark_inspections.reports_for_day(day)
    except Exception as exc:
        raise ToolError("Lark could not be checked. Do not report that no inspection exists.") from exc
    if not result.get("available"):
        return {"facts": ["Lark is unavailable; inspection reports have not been checked."]}
    facts = []
    for loc in db.scalars(select(Location).where(Location.id.in_(ids or [0]))):
        matches = [r for r in result["reports"] if r["store"] == lark_inspections.store_key(loc.name)]
        facts.append(f"Lark daily inspection, {loc.name}, {day}: {len(matches)} reports found. A report does not mean the inspection passed.")
        facts += [f"{loc.name}: report at {r['time']} Philippine time, {r['attachments']} attachments." for r in matches[:10]]
    return {"facts": facts}


def prepare_transfer(db, user, args):
    from .tools import _loc, ToolError
    from ..services import transfer_sheets
    loc = _loc(db, user, args.get("location"))
    lines = args.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 50:
        raise ToolError("Choose between 1 and 50 products for the draft.")
    checked = []
    for line in lines:
        product = db.get(Product, int(line.get("product_id", 0)))
        qty = float(line.get("qty", 0))
        if product is None or not 0 < qty <= transfer_sheets.MAX_QTY or not qty.is_integer():
            raise ToolError("Each line needs a valid product and positive whole-number quantity.")
        checked.append((product, qty))
    sheet = transfer_sheets.create(db, user, loc.id)
    for product, qty in checked:
        transfer_sheets.add_line(db, sheet, product, qty)
    return {"sheet_id": sheet.id, "link": f"/transfer-sheets/{sheet.id}", "message": "Transfer draft prepared for review; it has not been submitted to StoreHub."}
