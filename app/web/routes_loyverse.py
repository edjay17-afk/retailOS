"""Loyverse demand planning, isolated from StoreHub stock and transfer records."""
import csv
import io
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from .deps import Ctx, need, render
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
