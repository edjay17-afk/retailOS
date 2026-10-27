from datetime import timedelta
import pytest
from sqlalchemy import select, func
from app.loyverse.reporting import sync_account, key
from app.loyverse.client import LoyverseError
from app.models import (Product, Location, SalesTx, SalesDay, InventoryCurrent, LoyverseAccount)
from app.timeutil import today_local, now_utc
from tests.test_web import client, add_user, login, csrf_of


class FakeReporting:
    fail = False
    currency = 'PHP'
    quantity = 8
    cancelled = False
    def __init__(self, token): pass
    def close(self): pass
    def pages(self, resource, params=None):
        if self.fail: raise LoyverseError('Loyverse connection failed')
        day = today_local() - timedelta(days=1)
        receipt = dict(receipt_number='1-1', store_id='s', receipt_type='SALE', receipt_date=f'{day}T02:00:00Z',
                       total_money=112, total_tax=12, total_discount=0, employee_id='e', updated_at=now_utc().isoformat()+'Z',
                       cancelled_at=f'{day}T03:00:00Z' if self.cancelled else None,
                       line_items=[dict(variant_id='v',item_name='Tea',quantity=2,total_money=112,price=56,line_taxes=[dict(money_amount=12)])],
                       payments=[dict(type='CASH',name='Cash',money_amount=112, payment_details={'card_number':'PRIVATE'})])
        return iter({'merchant':[dict(currency={'code':self.currency})],
                     'stores':[dict(id='s',name='Example Loyverse')],
                     'items':[dict(id='i',item_name='Tea',track_stock=True,category_id='cat',primary_supplier_id='sup',variants=[dict(variant_id='v',sku='SAME',barcode='123',cost=10,default_price=56)])],
                     'suppliers':[dict(id='sup',name='Example supplier')],
                     'categories':[dict(id='cat',name='Drinks')],
                     'employees':[dict(id='e',name='Example Employee')],
                     'inventory':[dict(store_id='s',variant_id='v',in_stock=self.quantity)],
                     'receipts':[receipt]}[resource])


@pytest.fixture(autouse=True)
def reset_fake():
    FakeReporting.fail=False;FakeReporting.currency='PHP';FakeReporting.quantity=8;FakeReporting.cancelled=False


def test_shared_import_isolated_idempotent_and_recomputed(session, sync):
    assert sync_account('A','fake',FakeReporting)['status']=='ok'
    assert sync_account('B','fake',FakeReporting)['status']=='ok'
    sync_account('A','fake',FakeReporting)
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(SalesTx))==2
    assert session.scalar(select(func.count()).select_from(Product))==2
    assert all(x.net_sales==112 and x.tax==12 and x.cash_sales==112 for x in session.scalars(select(SalesDay)))
    sync.sync_products();sync.sync_inventory()
    session.expire_all()
    assert all(p.in_api for p in session.scalars(select(Product).where(Product.source=='loyverse')))
    FakeReporting.quantity=5;FakeReporting.cancelled=True
    sync_account('A','fake',FakeReporting);session.expire_all()
    loc=session.scalar(select(Location).where(Location.source_account=='A'))
    day=session.get(SalesDay,(loc.id,today_local()-timedelta(days=1)))
    assert day.net_sales==0 and day.cancelled_count==1
    assert session.scalar(select(InventoryCurrent.on_hand).where(InventoryCurrent.location_id==loc.id))==5
    previous=session.get(LoyverseAccount,'A').reporting_success_at
    FakeReporting.fail=True
    assert sync_account('A','fake',FakeReporting)['status']=='failed'
    session.expire_all()
    assert session.get(LoyverseAccount,'A').reporting_success_at==previous


def test_currency_guard(session):
    FakeReporting.currency='USD'
    assert sync_account('A','fake',FakeReporting)['status']=='failed'
    assert session.scalar(select(func.count()).select_from(SalesTx))==0


def test_history_entitlement_fallback_is_not_repeated(session):
    from app.models import Setting
    class LimitedHistory(FakeReporting):
        receipt_calls=0
        def pages(self, resource, params=None):
            if resource == 'receipts':
                LimitedHistory.receipt_calls += 1
                if LimitedHistory.receipt_calls == 1:
                    raise LoyverseError('History unavailable',402,'receipts')
            return super().pages(resource,params)
    result=sync_account('A','fake',LimitedHistory)
    assert result['status']=='ok' and result['initial_history_days']==30
    sync_account('A','fake',LimitedHistory)
    assert LimitedHistory.receipt_calls==3
    assert session.get(Setting,'loyverse.reporting_history_days.A').value==30


def test_refund_financial_totals(session):
    class WithRefund(FakeReporting):
        def pages(self, resource, params=None):
            rows=list(super().pages(resource,params))
            if resource == 'receipts':
                import copy
                refund=copy.deepcopy(rows[0]);refund.update(receipt_number='refund-1',receipt_type='REFUND',refund_for='1-1',total_money=56,total_tax=6)
                refund['line_items'][0].update(quantity=1,total_money=56,line_taxes=[dict(money_amount=6)])
                refund['payments'][0]['money_amount']=56
                rows.append(refund)
            return iter(rows)
    assert sync_account('A','fake',WithRefund)['status']=='ok'
    day=session.scalar(select(SalesDay))
    assert day.net_sales==56 and day.tax==6 and day.cash_sales==56 and day.return_count==1


def test_shared_pages_exports_and_scope(client,session):
    sync_account('A','fake',FakeReporting);sync_account('B','fake',FakeReporting)
    add_user(session,'report_admin','admin');login(client,'report_admin')
    client.cookies.set('pos_scope','loyverse:A')
    loc=session.scalar(select(Location).where(Location.source_account=='A'))
    other=session.scalar(select(Location).where(Location.source_account=='B'))
    p=session.scalar(select(Product).where(Product.source_account=='A'))
    otherp=session.scalar(select(Product).where(Product.source_account=='B'))
    for path in ('/','/sales','/sales/products','/sales/transactions','/sales/employees','/inventory','/employees','/sync','/counts','/expiry','/wastage','/inspections'):
        r=client.get(path)
        assert r.status_code==200, (path,r.text[:200])
    for path in ('/sales','/sales/products','/sales/transactions','/inventory'):
        r=client.get(path,params={'export':'csv','loc':loc.id})
        assert r.status_code==200
    assert client.get(f'/sales?loc={other.id}').status_code==403
    assert client.get(f'/sales/product/{otherp.id}').status_code==404
    assert client.get(f'/sales/product/{p.id}').status_code==200
    assert client.get('/wastage/products?q=Tea').json()[0]['id']==p.id
    token=csrf_of(client,'/expiry')
    r=client.post('/expiry/add',data=dict(csrf=token,location_id=loc.id,product_id=otherp.id,search='Tea',qty=1,expiry=str(today_local()+timedelta(days=10))),headers={'x-requested-with':'fetch'})
    assert r.status_code==400


def test_product_images_only_for_loyverse(client, session, sync):
    sync_account('A', 'fake', FakeReporting)
    sync.sync_stores()
    sync.sync_products()
    loyverse = session.scalar(select(Product).where(Product.source == 'loyverse'))
    centro = session.scalar(select(Product).where(Product.source == 'storehub'))
    image = 'https://api.loyverse.com/images/example.gif'
    loyverse.image_url = centro.image_url = image
    session.commit()
    add_user(session, 'image_admin', 'admin'); login(client, 'image_admin')
    client.cookies.set('pos_scope', 'loyverse:A')
    response = client.get(f'/sales/product/{loyverse.id}')
    assert response.status_code == 200
    assert f'src="{image}"' in response.text
    assert 'onerror="this.hidden=true"' in response.text
    assert 'https://api.loyverse.com' in response.headers['content-security-policy']
    loyverse.image_url = 'https://untrusted.example/photo.png'
    session.commit()
    assert 'src="https://untrusted.example/photo.png"' not in client.get(f'/sales/product/{loyverse.id}').text
    client.cookies.set('pos_scope', 'storehub')
    response = client.get(f'/sales/product/{centro.id}')
    assert response.status_code == 200
    assert f'src="{image}"' not in response.text


def test_unassigned_staff_cannot_see_new_accounts(client,session):
    sync_account('A','fake',FakeReporting)
    loc=session.scalar(select(Location).where(Location.source_account=='A'))
    add_user(session,'limited_staff','staff');login(client,'limited_staff')
    client.cookies.set('pos_scope','loyverse')
    assert client.get(f'/sales?loc={loc.id}').status_code==403
    assert client.get('/wastage/products?q=Tea').json()==[]


def test_cross_account_operation_blocked(session):
    sync_account('A','fake',FakeReporting);sync_account('B','fake',FakeReporting)
    from app.services.sources import require_match
    loc=session.scalar(select(Location).where(Location.source_account=='A'))
    p=session.scalar(select(Product).where(Product.source_account=='B'))
    with pytest.raises(ValueError,match='same POS account'):require_match(session,loc.id,p.id)


def test_source_switch_keeps_dates_and_assigned_staff(client,session):
    from app.models import User
    sync_account('A','fake',FakeReporting)
    sync_account('B','fake',FakeReporting)
    loc=session.scalar(select(Location).where(Location.source_account=='A'))
    add_user(session,'assigned','staff')
    user=session.scalar(select(User).where(User.username=='assigned'))
    user.location_ids=[loc.id];session.commit()
    login(client,'assigned')
    token=csrf_of(client,'/')
    r=client.post('/data-source',data=dict(csrf=token,scope='loyverse:A',back='/sales?days=custom&frm=2026-01-01&to=2026-01-02&loc=99'),follow_redirects=False)
    assert r.status_code==303 and 'frm=2026-01-01' in r.headers['location'] and 'loc=99' not in r.headers['location']
    assert client.get('/replenishment/loyverse').status_code==200
    assert client.get('/inventory').status_code==200
    assert client.post('/data-source',data=dict(csrf=token,scope='loyverse:B',back='/')).status_code==400
    response=client.post('/data-source',data=dict(csrf=token,scope='storehub',back='/replenishment/loyverse'),follow_redirects=False)
    assert response.headers['location']=='/replenishment'


def test_desktop_mobile_shared_sales(client,session):
    sync_account('A','fake',FakeReporting)
    add_user(session,'preview','admin');login(client,'preview')
    client.cookies.set('pos_scope','loyverse:A')
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    def serve(route):
        u=urlsplit(route.request.url)
        r=client.get(u.path+('?' +u.query if u.query else ''))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page()
        page.route('http://qa.test/**',serve)
        page.route('https://**',lambda r:r.abort())
        for width in (1440,390):
            page.set_viewport_size(dict(width=width,height=950))
            for path in ('/sales','/inventory','/sales/products'):
                page.goto('http://qa.test'+path)
                if width > 700:
                    assert page.locator('#pos-source').is_visible()
                    assert not page.locator('.pos-source-mobile').is_visible()
                else:
                    assert not page.locator('#pos-source').is_visible()
                    assert page.locator('.pos-source-mobile select').is_visible()
                    assert page.locator('.pos-source-mobile select').input_value() == 'loyverse:A'
                assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
            from pathlib import Path
            Path('docs/previews').mkdir(exist_ok=True)
            page.screenshot(path=f'docs/previews/loyverse-products-{width}.png')
        browser.close()


def test_dashboard_many_stores_stays_compact(client,session):
    sync_account('A','fake',FakeReporting)
    for i in range(11):
        session.add(Location(storehub_id=f'layout-{i}',name=f'Example branch {i}',source='loyverse',source_account='A',external_id=f'layout-{i}',monitored=True))
    session.commit()
    add_user(session,'dashboard_preview','admin');login(client,'dashboard_preview')
    client.cookies.set('pos_scope','loyverse:A')
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    def serve(route):
        u=urlsplit(route.request.url)
        r=client.get(u.path+('?' +u.query if u.query else ''))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page()
        page.route('http://qa.test/**',serve)
        page.route('https://**',lambda r:r.abort())
        for width in (1440,768,390):
            page.set_viewport_size(dict(width=width,height=950))
            page.goto('http://qa.test/')
            assert page.locator('.b-card').count()==12  # one card per store
            assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
            # every card stays inside the window at every width, and the numbers inside are not cut off
            assert page.evaluate('() => [...document.querySelectorAll(".b-card")].every(c => c.getBoundingClientRect().right <= innerWidth + 1 && c.scrollWidth <= c.clientWidth + 1)')
        browser.close()


def test_the_switch_lists_each_account_and_has_no_commissary_tab(client, session):
    sync_account('A', 'fake', FakeReporting)
    session.add(Location(storehub_id='sw-1', name='Switch branch', source='loyverse', source_account='A', external_id='sw-1', monitored=True))
    session.commit()
    add_user(session, 'switch_admin', 'admin'); login(client, 'switch_admin')
    client.cookies.set('pos_scope', 'loyverse')               # an old combined choice opens the first account instead
    html = client.get('/').text
    assert 'name="scope" value="storehub"' in html and 'name="scope" value="loyverse:A"' in html
    assert 'Commissary' not in html and 'All accounts' not in html and 'name="scope" value="loyverse"' not in html
    assert 'class="is-active"' in html.split('value="loyverse:A"')[1][:80]
