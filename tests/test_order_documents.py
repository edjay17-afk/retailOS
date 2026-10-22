import csv
import io
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Location, Product, PurchaseOrder, PoLine, StockTransfer, StockTransferLine, StockTransferImport, OrderDocument
from app.services import order_documents as docs
from app.timeutil import now_utc
from tests.test_web import client, add_user, login, csrf_of


def setup_document(session, kind):
    location = Location(storehub_id='warehouse-doc', name='Zenwok Warehouse', type='warehouse', monitored=True, city='Mandaue')
    target = Location(storehub_id='store-doc', name='The Daily Centro - Getafe', type='store', monitored=True, city='Getafe')
    product = Product(storehub_id='prod-doc', name='Tteokbokki 105g Cheese', sku='CHEESE', in_api=True, cost=67.321428)
    session.add_all([location,target,product]);session.flush()
    if kind == 'po':
        source = PurchaseOrder(sh_po_id=151, location_id=location.id, status='open', created_sh=now_utc(), subtotal=807.86, tax=0,discount=0,total=807.86)
        source.lines = [PoLine(product_id=product.id, product_sh_id=product.storehub_id, ordered_qty=12, received_qty=0, received_present=True, supplier_price=67.321428, subtotal=807.86)]
    else:
        raw = ('S.T ID,No.,Ordered Qty,Cost (PHP)\r\nST0068,1,12,67.321428123456789123456789123456789\r\n').encode()
        owner = add_user(session,'importer','admin')
        imported = StockTransferImport(token='doc-test',filename='export.csv',sha256='x'*64,raw_csv=raw,created_by=owner.id)
        session.add(imported);session.flush()
        source = StockTransfer(reference='ST0068',source_name=location.name,target_name=target.name,source_location_id=location.id,target_location_id=target.id,
                               status='Created',currency='PHP',total=807.84,payload_hash='a'*64,last_import_id=imported.id,created_sh=now_utc())
        source.lines=[StockTransferLine(line_no=1,product_id=product.id,product_name=product.name,sku=product.sku,serial_no='',category='',quantity=12,unit='',unit_cost=67.32,subtotal=807.84)]
    session.add(source);session.commit()
    return source,location,target,product


@pytest.mark.parametrize('kind,prefix',[('po','/purchase-orders'),('transfer','/stock-transfers')])
def test_edit_export_print_and_canonical_records_untouched(session,client,kind,prefix):
    source,location,target,product=setup_document(session,kind)
    user=add_user(session,'editor','warehouse');login(client,'editor')
    base=f'{prefix}/{source.id}'
    html=client.get(base+'/edit');assert html.status_code==200
    csrf=csrf_of(client,base+'/edit')
    d=docs.document(session,source,kind)
    d['lines'][0]['quantity']='12.000000000000000000000000001'
    d['lines'][0]['cost']='67.321428123456789123456789123456789'
    d['lines'].append(dict(name='Added item',sku='NEW',product_id='',serial='A;B',quantity='2.5',unit='ctn',cost='0.123456789123456789'))
    payload=dict(header=d['header'],lines=d['lines'],revision=0,csrf=csrf)
    response=client.post(base+'/edit',json=payload);assert response.status_code==200,response.text
    session.expire_all()
    assert len(source.lines)==1
    assert source.lines[0].ordered_qty==12 if kind=='po' else source.lines[0].quantity==12
    saved=docs.document(session,source,kind)
    with docs.localcontext() as ctx:
        ctx.prec=1024
        expected=Decimal(d['lines'][0]['quantity'])*Decimal(d['lines'][0]['cost'])+Decimal('2.5')*Decimal('0.123456789123456789')
    assert saved['total']==docs.plain(expected)
    exported=client.get(base+'/export?mode=storehub');assert exported.status_code==200
    rows=list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert rows[0]==docs.HEADERS[kind]
    assert rows[1][3]==d['lines'][0]['quantity']
    assert rows[1][-1]==d['lines'][0]['cost'] if kind=='po' else rows[1][-1]==''
    report=client.get(base+'/export?mode=report')
    assert saved['total'] in report.content.decode('utf-8-sig')
    printed=client.get(base+'/print');assert printed.status_code==200
    assert (docs.six_decimals(saved['total']) if kind=='po' else docs.display(saved['total'],2)) in printed.text and 'print-grand-total' in printed.text  # PO print amounts use six decimal places
    assert 'Added item' in printed.text
    # Stale editors cannot overwrite the first save.
    assert client.post(base+'/edit',json=payload).status_code==409
    payload.update(revision=1);payload['lines'].pop(0)
    assert client.post(base+'/edit',json=payload).status_code==200
    session.expire_all();assert len(docs.document(session,source,kind)['lines'])==1
    # Source refreshes retain the editable copy and warn that data changed.
    if kind=='po':source.raw_hash='new-source-hash'
    else:source.payload_hash='b'*64
    session.commit();assert docs.document(session,source,kind)['changed']
    client.cookies.set('lang','zh')
    assert '保存修改' in client.get(base+'/edit').text
    assert '总金额' in client.get(base+'/print').text


def test_original_csv_decimal_precision_recovered(session):
    source,*_=setup_document(session,'transfer')
    d=docs.document(session,source,'transfer')
    assert d['lines'][0]['cost']=='67.321428123456789123456789123456789'
    assert d['total']=='807.857137481481469481481469481481468'


@pytest.mark.parametrize('bad',['NaN','Infinity','-1','1e20','1,000','', '9'*201])
def test_invalid_numbers_rejected(bad):
    with pytest.raises(docs.Invalid):docs.number(bad)


@pytest.mark.parametrize('kind,prefix',[('po','/purchase-orders'),('transfer','/stock-transfers')])
def test_permissions_csrf_scope_search_and_missing_ids(session,client,kind,prefix):
    source,location,target,product=setup_document(session,kind)
    admin=add_user(session,'docadmin','admin');login(client,'docadmin')
    base=f'{prefix}/{source.id}'
    d=docs.document(session,source,kind)
    assert client.post(base+'/edit',json={**d,'csrf':''}).status_code==403
    token=csrf_of(client,base+'/edit')
    result=client.get(base+'/products?q=Cheese').json();assert result[0]['product_id']==product.storehub_id
    d['lines'][0]['sku']='';d['lines'][0]['product_id']=''
    assert client.post(base+'/edit',json={**d,'csrf':token}).status_code==200
    assert client.get(base+'/export?mode=storehub').status_code==400
    assert client.get(base+'/export?mode=report').status_code==200
    staff=add_user(session,'docstaff','staff');login(client,'docstaff')
    assert client.get(base+'/edit').status_code==403
    assert client.post(base+'/edit',json={**d,'csrf':token}).status_code==403
    other=Location(storehub_id='outside',name='Outside',type='store',monitored=True);session.add(other);session.commit()
    restricted=add_user(session,'restricted','warehouse',[other.id]);login(client,'restricted')
    for suffix in ('edit','print','export','products?q=Cheese'):
        assert client.get(base+'/'+suffix).status_code in (403,404)


def test_report_formula_cells_sanitized():
    d=dict(header={'reference':'=bad','tax':'0','discount':'0'},lines=[dict(name='=SUM(1)',sku='SKU',product_id='ID',quantity='1',unit='',serial='',cost='1')])
    docs.calculate(d)
    rows=list(csv.reader(io.StringIO(docs.export(d,'transfer','report').decode('utf-8-sig'))))
    assert rows[1][0]=="'=bad" and rows[1][2]=="'=SUM(1)"


def test_document_amounts_and_total_are_exact_not_rounded_to_two_decimals(session):
    """StoreHub rounds each line and its Total to 2 decimals; the printout and report in the system keep the exact amounts."""
    raw = ('S.T ID,No.,Product Name,Ordered Qty,Cost (PHP),SubTotal (PHP),Total (PHP)\r\n'
           'ST0099,,,,,,1665.18\r\n'
           'ST0099,1,Item A,12,67.321428,807.86,\r\n'
           'ST0099,2,Item B,12,71.360714,856.33,\r\n'
           'ST0099,3,Item C,3,0.335,1.00,\r\n').encode()
    owner = add_user(session, 'importer_raw', 'admin')
    imported = StockTransferImport(token='doc-raw-total', filename='export.csv', sha256='y' * 64, raw_csv=raw, created_by=owner.id)
    session.add(imported)
    session.flush()
    source = StockTransfer(reference='ST0099', source_name='Zenwok Warehouse', target_name='The Daily Centro - Getafe',
                           status='Created', currency='PHP', total=1665.18, payload_hash='c' * 64,
                           last_import_id=imported.id, created_sh=now_utc())
    source.lines = [StockTransferLine(line_no=n, product_id=None, product_name=name, sku='', serial_no='', category='',
                                      quantity=qty, unit='', unit_cost=cost, subtotal=sub)
                    for n, name, qty, cost, sub in ((1, 'Item A', 12, 67.321428, 807.86),
                                                    (2, 'Item B', 12, 71.360714, 856.33),
                                                    (3, 'Item C', 3, 0.335, 1.00))]
    session.add(source)
    session.commit()
    d = docs.document(session, source, 'transfer')
    assert [line['amount'] for line in d['lines']] == ['807.857136', '856.328568', '1.005']  # quantity x cost, not StoreHub's rounded 807.86 / 856.33 / 1.00
    assert d['subtotal'] == '1665.190704'
    assert d['total'] == '1665.190704'  # not StoreHub's rounded 1665.18
    assert docs.display(d['total'], 2) == '1,665.190704'  # what the printout shows
    edited = docs.validate(dict(header=d['header'], lines=d['lines']), 'transfer')
    assert edited['total'] == '1665.190704'  # an edit recomputes the same way


def test_po_print_recalculates_six_decimal_amounts(session, client):
    source, location, target, product = setup_document(session, 'po')
    source.tax = 1.25
    source.discount = .5
    session.commit()
    add_user(session, 'precise_po', 'warehouse')
    login(client, 'precise_po')
    response = client.get(f'/purchase-orders/{source.id}/print')
    assert response.status_code == 200
    assert '807.857136' in response.text  # 12 * 67.321428, not the rounded API line subtotal
    assert '<td>1.25</td>' in response.text
    assert '1.250000' not in response.text
    assert '808.607136' in response.text  # add tax, subtract discount after summing
    session.refresh(source)
    assert source.total == 807.86 and source.lines[0].subtotal == 807.86
    assert docs.six_decimals('1.2') == '1.200000'
    assert docs.six_decimals('1.2345678') == '1.234568'
