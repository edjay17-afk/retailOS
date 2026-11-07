"""Loyverse demand planning, isolated from StoreHub stock and transfer records."""
import csv
import io
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from .deps import Ctx, need, redirect, render
from ..config import get_config
from ..loyverse.replenishment import report

router=APIRouter()

def csv_text(value):
    value=str(value or "")
    return "'"+value if value.lstrip().startswith(("=","+","-","@")) else value

@router.get("/replenishment/loyverse")
def loyverse_replenishment(account: str="", store: str="", search: str="", export: str="", page: int=1, c: Ctx=Depends(need("loyverse.view"))):
    from ..models import Location
    from sqlalchemy import select
    locations = c.locations()
    pairs = {(x.source_account,x.external_id) for x in locations if x.source == 'loyverse'}
    # Compatibility with installations still awaiting their first shared import.
    if not c.user.location_ids and c.user.role in ('admin','management') and not c.db.scalar(select(Location.id).where(Location.source == 'loyverse').limit(1)):
        pairs = None
    data=report(c.db,account,store,search[:150],allowed_pairs=pairs)
    if export=="csv":
        out=io.StringIO();writer=csv.writer(out)
        writer.writerow(["Account","Store","Product","SKU","Barcode","Variant ID","From","To","Net units sold","Sales days","Average per sales day","Cover days","Suggested quantity","Status"])
        for row in data['rows']:
            writer.writerow([csv_text(row[k]) for k in ('account','store','name','sku','barcode','variant_id')]+[data['start'],data['end'],str(row['units']),row['days'],f"{row['average']:.4f}",str(data['cover']),str(row['quantity']) if row['quantity'] is not None else "",row['reason']])
        return Response("\ufeff"+out.getvalue(),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="loyverse-replenishment.csv"'})
    total=len(data['rows']);pages=max(1,(total+99)//100);page=min(max(1,page),pages)
    data['rows']=data['rows'][(page-1)*100:page*100]
    return render(c,"loyverse_replenishment.html",**data,total=total,page=page,pages=pages)


# ---------------------------------------------------------------- edit Loyverse stock (writes to Loyverse after a review step)
def _loyverse_location(c: Ctx, loc: int):
    from ..models import Location
    location = c.db.get(Location, loc)
    if location is None or location.source != "loyverse":
        raise HTTPException(404, "Not a Loyverse store")
    c.check_loc(location.id)
    return location


@router.post("/inventory/loyverse/apply")
async def loyverse_stock_apply(request: Request, c: Ctx = Depends(need("loyverse.stock_edit"))):
    """Saves the numbers typed on the Inventory page. Each product is re-read from Loyverse first, written, read back, and logged."""
    from ..loyverse.stock_edit import MAX_CHANGES, apply_changes, parse_qty
    from ..loyverse.client import LoyverseError
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    location = _loyverse_location(c, int(form.get("loc", 0) or 0))
    back = str(form.get("back", "")) or f"/inventory?loc={location.id}"
    if not back.startswith("/inventory") or "//" in back[:3]:
        back = f"/inventory?loc={location.id}"
    reason = str(form.get("reason", "")).strip()[:200] or "Edited in Retail OS"
    changes = []
    for key, value in form.multi_items():
        if key.startswith("new_") and key[4:].isdigit():
            pid = key[4:]; new = parse_qty(value); old = parse_qty(form.get("old_" + pid, ""))
            if new is None or old is None:
                return redirect(back, err="Stock must be a number, 0 or more, with at most 3 decimals")
            if abs(new - old) > 0.0005:
                changes.append({"product_id": int(pid), "old": old, "new": new})
    if not changes:
        return redirect(back, err="Nothing was changed")
    if len(changes) > MAX_CHANGES:
        return redirect(back, err=f"Change at most {MAX_CHANGES} products at a time")
    try:
        res = apply_changes(c.db, get_config(), c.user, location, changes, reason)
    except LoyverseError as e:
        return redirect(back, err=str(e))
    ok = f"{len(res['done'])} product(s) changed in Loyverse" if res["done"] else None
    bad = "; ".join(f"{n}: {why}" for n, why in res["skipped"][:5]) + (f" (+{len(res['skipped']) - 5} more)" if len(res["skipped"]) > 5 else "") if res["skipped"] else None
    return redirect(back, ok=ok, err=res["error"] or (("Not changed - " + bad) if bad else None))
