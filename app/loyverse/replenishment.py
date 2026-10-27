"""Sales-only suggestions from completed Philippine business days."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from sqlalchemy import select
from ..models import LoyverseAccount, LoyverseStore, LoyverseVariant, LoyverseReceipt
from ..timeutil import today_local, now_utc
from .. import settings_service

def report(db, account="", store="", search="", allowed_pairs=None):
    lookback=int(settings_service.get(db,"repl.lookback_days"))
    cover=Decimal(str(settings_service.get(db,"repl.sales_only_cover_days")))
    end=today_local()-timedelta(days=1);start=end-timedelta(days=lookback-1)
    states={s.key:s for s in db.scalars(select(LoyverseAccount))}
    stores=list(db.scalars(select(LoyverseStore).where(LoyverseStore.active.is_(True)).order_by(LoyverseStore.name)))
    if allowed_pairs is not None:
        stores = [s for s in stores if (s.account_key, s.store_id) in allowed_pairs]
        states = {k:v for k,v in states.items() if k in {s.account_key for s in stores}}
    selected={(s.account_key,s.store_id):s for s in stores if (not account or s.account_key==account) and (not store or s.store_id==store)}
    trading=defaultdict(set);units=defaultdict(Decimal);names={}
    query=select(LoyverseReceipt).where(LoyverseReceipt.business_date>=start,LoyverseReceipt.business_date<=end,LoyverseReceipt.cancelled.is_(False))
    if account:query=query.where(LoyverseReceipt.account_key==account)
    if store:query=query.where(LoyverseReceipt.store_id==store)
    for receipt in db.scalars(query):
        location=(receipt.account_key,receipt.store_id)
        if location not in selected:continue
        if receipt.receipt_type=="SALE":trading[location].add(receipt.business_date)
        for line in receipt.lines:
            key=(*location,line["variant_id"])
            units[key]+=Decimal(line["quantity"])*(1 if receipt.receipt_type=="SALE" else -1)
            names[key]=line["name"]
    variants={(v.account_key,v.variant_id):v for v in db.scalars(select(LoyverseVariant))}
    rows=[];term=search.strip().casefold()
    for (akey,sid,vid),net in units.items():
        if net<=0:continue
        variant=variants.get((akey,vid))
        name=variant.name if variant else names[(akey,sid,vid)]
        if term and term not in (name+" "+(variant.sku or "")+" "+(variant.barcode or "") if variant else name).casefold():continue
        days=len(trading[(akey,sid)])
        if not days:continue
        state=states.get(akey)
        ready=bool(state and state.last_success_at and not state.last_error and state.history_from and state.history_from<=start and now_utc()-state.last_success_at<timedelta(minutes=15))
        available=bool(variant and variant.active)
        average=net/Decimal(days)
        qty=(average*cover).quantize(Decimal("0.001") if variant and variant.sold_by_weight else Decimal("1"),rounding=ROUND_CEILING)
        rows.append(dict(account=akey,store_id=sid,store=selected[(akey,sid)].name,variant_id=vid,name=name,sku=variant.sku if variant else "",barcode=variant.barcode if variant else "",units=net,days=days,average=average,quantity=qty if ready and available else None,
                         reason="Sync unavailable or stale" if not ready else "Item unavailable in catalog" if not available else "Short sales history" if days<int(settings_service.get(db,"repl.min_trading_days")) else "Ready",
                         composite=bool(variant and variant.composite),rounding=Decimal("0.001") if variant and variant.sold_by_weight else Decimal("1")))
    rows.sort(key=lambda r:(r['store'].casefold(),-r['units'],r['name'].casefold()))
    return dict(rows=rows,stores=stores,accounts=states,account=account,store=store,search=search,start=start,end=end,lookback=lookback,cover=cover)
