from tests.test_web import client, add_user, login, seeded
from app.models import Location, SalesDay
from app.timeutil import today_local
from sqlalchemy import select


def test_live_sales_scope_and_updates(sync,fake,session,client):
    seeded(sync,fake,session)
    store=session.scalar(select(Location).where(Location.type=='store'))
    add_user(session,'live_manager','store_manager',[store.id]);login(client,'live_manager')
    r=client.get('/dashboard/store-sales')
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    assert [s['id'] for s in r.json()['stores']]==[store.id]
    row=session.scalar(select(SalesDay).where(SalesDay.location_id==store.id,SalesDay.business_date==today_local()))
    if row is None:
        row=SalesDay(location_id=store.id,business_date=today_local(),net_sales=0)
        session.add(row)
    row.net_sales=1234.56;session.commit()
    assert client.get('/dashboard/store-sales').json()['today']==1234.56
    add_user(session,'no_sales','staff',[store.id]);login(client,'no_sales')
    assert client.get('/dashboard/store-sales').status_code==403


def test_dashboard_updates_without_navigation(sync,fake,session,client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    seeded(sync,fake,session)
    add_user(session,'live_admin','admin');login(client,'live_admin')
    calls=[]
    def serve(route):
        u=urlsplit(route.request.url)
        if u.path=='/dashboard/store-sales':
            calls.append(1)
            r=client.get(u.path);data=r.json()
            data['today']=1234.56
            for store in data['stores']:store['today']=1234.56
            route.fulfill(json=data);return
        r=client.get(u.path)
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        page.clock.install()
        page.goto('http://qa.test/')
        page.clock.fast_forward(31000)
        page.wait_for_function("()=>document.getElementById('live-sales-today').textContent==='1,234.56'")
        assert calls and page.url=='http://qa.test/'
        assert page.locator('.store-today').first.inner_text()=='1,234.56'
        browser.close()
