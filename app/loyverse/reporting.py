"""Read-only Loyverse adapter into the shared reporting/operations model.

Source/account/external_id are authoritative. Legacy StoreHub key columns carry
namespaced compatibility keys and must never be sent to the StoreHub connector.
"""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Lock

from sqlalchemy import select, delete

from ..db import SessionLocal
from ..models import (Employee, Location, Product, Supplier, SalesTx, SalesLine,
                      Payment, InventoryCurrent, InventoryChange, LoyverseAccount, Setting)
from ..timeutil import now_utc, today_local
from ..config import get_config
from ..storehub.sync import recompute_sales_day
from .client import Client, LoyverseError, accounts
from .sync import moment, stamp

_lock = Lock()


def key(account, external):
    return 'lv:' + hashlib.sha256((account + ':' + str(external)).encode()).hexdigest()[:36]


def amount(value):
    d = Decimal(str(value or 0))
    if not d.is_finite():
        raise ValueError('Invalid numeric value')
    return float(d)


def identity(model, account, external):
    return model(source='loyverse', source_account=account, external_id=external,
                 storehub_id=key(account, external))


def catalog(db, account, stores, items, suppliers, categories, employees):
    locs = {x.external_id: x for x in db.scalars(select(Location).where(Location.source_account == account, Location.source == 'loyverse'))}
    for row in stores:
        obj = locs.get(row['id'])
        if obj is None:
            obj = identity(Location, account, row['id']); db.add(obj)
            obj.monitored = True
        obj.name = row['name']; obj.type = 'store'
        if row.get('deleted_at'): obj.monitored = False
        locs[row['id']] = obj
    sups = {x.external_id: x for x in db.scalars(select(Supplier).where(Supplier.source_account == account, Supplier.source == 'loyverse'))}
    for row in suppliers:
        obj = sups.get(row['id'])
        if obj is None: obj = identity(Supplier, account, row['id']); db.add(obj)
        obj.name = row['name']; obj.active = not bool(row.get('deleted_at'))
        sups[row['id']] = obj
    db.flush()
    cats = {r['id']: r['name'] for r in categories}
    prods = {x.external_id: x for x in db.scalars(select(Product).where(Product.source_account == account, Product.source == 'loyverse'))}
    for p in prods.values(): p.in_api = False
    for item in items:
        for v in item.get('variants', []):
            p = prods.get(v['variant_id'])
            if p is None: p = identity(Product, account, v['variant_id']); db.add(p)
            options = [str(v[f'option{i}_value']) for i in (1, 2, 3) if v.get(f'option{i}_value')]
            p.name = item['item_name'] + (' / ' + ' / '.join(options) if options else '')
            p.sku = v.get('sku'); p.barcode = v.get('barcode')
            p.category = cats.get(item.get('category_id'))
            p.unit_price = amount(v['default_price']) if v.get('default_price') is not None else None
            p.cost = amount(v['cost']) if v.get('cost') is not None else None
            p.track_stock = bool(item.get('track_stock')); p.is_parent = False
            p.in_api = not bool(item.get('deleted_at') or v.get('deleted_at'))
            p.last_seen = now_utc()
            image = item.get('image_url') or ''
            p.image_url = image if image.startswith('https://') else None
            sup = sups.get(item.get('primary_supplier_id'))
            p.supplier_id = sup.id if sup else None; p.supplier_source = 'loyverse'
            prods[v['variant_id']] = p
    emps = {x.external_id: x for x in db.scalars(select(Employee).where(Employee.source_account == account, Employee.source == 'loyverse'))}
    for row in employees:
        obj = emps.get(row['id'])
        if obj is None: obj = identity(Employee, account, row['id']); db.add(obj)
        obj.first_name = row.get('name') or 'Unnamed employee'; obj.last_name = ''
        obj.email = row.get('email'); obj.phone = row.get('phone_number')
    db.flush()


def apply_data(db, account, receipts, inventory):
    locs = {x.external_id: x for x in db.scalars(select(Location).where(Location.source == 'loyverse', Location.source_account == account))}
    prods = {x.external_id: x for x in db.scalars(select(Product).where(Product.source == 'loyverse', Product.source_account == account))}
    touched = set()
    for row in receipts:
        if row['store_id'] not in locs: raise ValueError('Receipt references unknown store')
        if row['receipt_type'] not in ('SALE', 'REFUND'): raise ValueError('Invalid receipt type')
        ref = key(account, row['receipt_number'])
        tx = db.scalar(select(SalesTx).where(SalesTx.refid == ref))
        digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:40]
        if tx and tx.raw_hash == digest: continue
        if tx:
            touched.add((tx.location_id, tx.business_date))
            tx.lines.clear(); tx.payments.clear()
        else:
            tx = SalesTx(refid=ref); db.add(tx)
        tx.location_id = locs[row['store_id']].id
        tx.invoice_no = row['receipt_number']; tx.tx_time = moment(row['receipt_date'])
        tx.business_date = (tx.tx_time + timedelta(hours=8)).date()
        tx.tx_type = 'Return' if row['receipt_type'] == 'REFUND' else 'Sale'
        tx.total = amount(row['total_money']); tx.tax = amount(row.get('total_tax'))
        tx.discount = amount(row.get('total_discount'))
        tx.service_charge = amount(row.get('surcharge')) + amount(row.get('tip'))
        tx.subtotal = tx.total - tx.tax - tx.service_charge + tx.discount
        tx.rounded = 0; tx.channel = 'Loyverse'
        tx.cancelled = bool(row.get('cancelled_at'))
        tx.cancelled_time = moment(row['cancelled_at']) if row.get('cancelled_at') else None
        tx.sale_invoice_no = row.get('refund_for')
        tx.employee_sh_id = key(account, row['employee_id']) if row.get('employee_id') else None
        tx.raw_hash = digest; tx.last_seen = now_utc()
        for index, line in enumerate(row.get('line_items', [])):
            vid = line.get('variant_id') or 'receipt-item:' + str(line.get('item_id') or line.get('id'))
            p = prods.get(vid)
            if p is None:
                p = identity(Product, account, vid)
                p.name = line.get('item_name') or 'Deleted item'; p.sku = line.get('sku')
                p.in_api = False; db.add(p); db.flush(); prods[vid] = p
            tax = sum(amount(t.get('money_amount')) for t in line.get('line_taxes', []))
            total = amount(line['total_money']); discount = amount(line.get('total_discount'))
            qty = amount(line['quantity'])
            if qty < 0: raise ValueError('Invalid quantity')
            tx.lines.append(SalesLine(line_no=index, product_id=p.id, product_sh_id=p.storehub_id,
                                      qty=qty, unit_price=amount(line.get('price')), total=total,
                                      tax=tax, discount=discount, subtotal=total-tax+discount, item_type='Item'))
        for payment in row.get('payments', []):
            method = 'Cash' if payment.get('type') == 'CASH' else payment.get('name') or 'Other'
            tx.payments.append(Payment(method=method[:30], amount=amount(payment['money_amount'])))
        touched.add((tx.location_id, tx.business_date))
    db.flush()
    for lid, day in touched: recompute_sales_day(db, lid, day)
    seen = set()
    for row in inventory:
        loc = locs.get(row['store_id']); p = prods.get(row['variant_id'])
        if not loc or not p: raise ValueError('Inventory references unknown catalog entry')
        if row.get('in_stock') is None: continue
        pair = (loc.id, p.id); seen.add(pair)
        qty = amount(row['in_stock']); inv = db.get(InventoryCurrent, pair)
        if inv is None:
            inv = InventoryCurrent(location_id=loc.id, product_id=p.id, on_hand=qty); db.add(inv)
        elif inv.on_hand != qty:
            db.add(InventoryChange(location_id=loc.id, product_id=p.id, old_qty=inv.on_hand,
                                   new_qty=qty, delta=qty-inv.on_hand))
            inv.on_hand = qty; inv.changed_at = now_utc()
        inv.synced_at = now_utc()
    # Complete snapshot: remove unavailable levels instead of inventing zero stock.
    for inv in db.scalars(select(InventoryCurrent).where(InventoryCurrent.location_id.in_([x.id for x in locs.values()]))):
        if (inv.location_id, inv.product_id) not in seen: db.delete(inv)


def sync_account(account, token, factory=Client):
    began = now_utc()
    history_key = 'loyverse.reporting_history_days.' + account
    with SessionLocal() as db:
        setting = db.get(Setting, history_key)
        history_days = int(setting.value) if setting else 90
        start = today_local() - timedelta(days=history_days)
        st = db.get(LoyverseAccount, account)
        full = not st or not st.reporting_history_from or st.reporting_history_from > start
        since = datetime.combine(start, datetime.min.time()) - timedelta(hours=8) if full else st.reporting_success_at - timedelta(minutes=3)
        fetch_catalog = not st or not st.reporting_catalog_at or began-st.reporting_catalog_at > timedelta(minutes=15)
    client = factory(token)
    try:
        merchant = list(client.pages('merchant'))[0]
        currency = merchant.get('currency', {}).get('code')
        if currency != 'PHP': raise LoyverseError('Shared reports require a confirmed PHP account; this account was not imported')
        data = {name: list(client.pages(name)) for name in ('stores', 'items', 'suppliers', 'categories', 'employees')} if fetch_catalog else None
        try:
            receipts = list(client.pages('receipts', {'updated_at_min': stamp(since), 'updated_at_max': stamp(began)}))
        except LoyverseError as error:
            if not (full and history_days > 30 and error.status_code == 402): raise
            # Some plans restrict receipt history. Preserve an explicit supported
            # window, rather than retrying an unavailable 90-day backfill forever.
            history_days = 30
            start = today_local() - timedelta(days=history_days)
            since = datetime.combine(start, datetime.min.time()) - timedelta(hours=8)
            receipts = list(client.pages('receipts', {'updated_at_min': stamp(since), 'updated_at_max': stamp(began)}))
        inventory = list(client.pages('inventory'))
        with SessionLocal() as db:
            st = db.get(LoyverseAccount, account)
            if st is None: st = LoyverseAccount(key=account); db.add(st)
            if data: catalog(db, account, **data)
            apply_data(db, account, receipts, inventory)
            name_key = 'loyverse.account_name.' + account
            label = db.get(Setting, name_key)
            name = str(merchant.get('business_name') or account.replace('LOYVERSE_TOKEN_', ''))[:120]
            if label is None: label = Setting(key=name_key, value=name); db.add(label)
            else: label.value = name
            st.currency = currency; st.reporting_error = None; st.reporting_success_at = began
            st.reporting_history_from = min(st.reporting_history_from or start, start)
            setting = db.get(Setting, history_key)
            if setting is None: setting = Setting(key=history_key, value=history_days); db.add(setting)
            else: setting.value = history_days
            if data: st.reporting_catalog_at = began
            db.commit()
        return {'account': account, 'status': 'ok', 'receipts': len(receipts), 'initial_history_days': history_days}
    except Exception as error:
        message = str(error) if isinstance(error, LoyverseError) else 'Reporting import failed validation; previous data retained'
        with SessionLocal() as db:
            st = db.get(LoyverseAccount, account)
            if st is None: st = LoyverseAccount(key=account); db.add(st)
            st.reporting_error = message
            if not isinstance(error, LoyverseError): st.reporting_catalog_at = None
            db.commit()
        return {'account': account, 'status': 'failed', 'error': message}
    finally:
        client.close()


def sync_all(factory=Client):
    if not _lock.acquire(blocking=False): return []
    try:
        return [sync_account(k, t, factory) for k, t in accounts(get_config().loyverse_tokens_file).items()]
    finally:
        _lock.release()
