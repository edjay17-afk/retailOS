from datetime import timedelta
from decimal import Decimal
import httpx
import pytest
from sqlalchemy import select, func
from app.loyverse.client import Client, LoyverseError
from app.loyverse.sync import sync_account, receipt_values
from app.loyverse.replenishment import report
from app.models import LoyverseAccount, LoyverseReceipt, LoyverseVariant
from app.timeutil import now_utc, today_local
from tests.test_web import client, add_user, login

class Fake:
    fail=False
    def __init__(self, token): pass
    def close(self): pass
    def pages(self, resource, params=None):
        if self.fail: raise LoyverseError("Loyverse rejected the access token")
        if resource == "stores": return iter([dict(id="s",name="Example store")])
        if resource == "items": return iter([dict(id="i",item_name="Example tea",variants=[dict(variant_id="v",sku="TEA",barcode="123")])])
        return iter(self.receipts)

def receipt(number, qty, days=1, kind="SALE", cancelled=False):
    date=today_local()-timedelta(days=days)
    return dict(receipt_number=number,store_id="s",receipt_date=f"{date}T02:00:00Z",updated_at=now_utc().isoformat()+"Z",receipt_type=kind,cancelled_at="yes" if cancelled else None,line_items=[dict(variant_id="v",item_name="Example tea",quantity=qty)],customer_id="PRIVATE")

def test_sync_and_sales_only(session):
    Fake.fail=False
    Fake.receipts=[receipt("1",10),receipt("2",6,2),receipt("3",2,1,"REFUND"),receipt("4",900,1,cancelled=True),receipt("5",100,0)]
    assert sync_account("A","fake",Fake)["status"] == "ok"
    assert sync_account("A","fake",Fake)["status"] == "ok"
    assert sync_account("B","fake",Fake)["status"] == "ok"
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(LoyverseReceipt)) == 10
    rows=report(session,account="A")["rows"]
    assert len(rows)==1
    assert rows[0]["units"] == Decimal(14)
    assert rows[0]["days"] == 2
    assert rows[0]["quantity"] == 49
    assert "PRIVATE" not in str(session.get(LoyverseReceipt,("A","1")).lines)
    assert not report(session,search="missing")["rows"]
    assert len(report(session,search="123")["rows"]) == 2
    Fake.receipts=[receipt("1",10,cancelled=True)]
    sync_account("A","fake",Fake); session.expire_all()
    assert report(session,account="A")["rows"][0]["units"]==4
    previous=session.get(LoyverseAccount,"A").last_success_at
    Fake.fail=True
    assert sync_account("A","fake",Fake)["status"] == "failed"
    session.expire_all()
    assert session.get(LoyverseAccount,"A").last_success_at == previous
    assert report(session,account="A")["rows"][0]["quantity"] is None
    Fake.fail=False

def test_client_pagination_and_errors():
    calls=[]
    def handler(req):
        assert req.method=="GET"
        calls.append(str(req.url))
        if len(calls)==1: return httpx.Response(429)
        if "cursor" not in req.url.params: return httpx.Response(200,json={"items":[{"id":"one"}],"cursor":"next"})
        return httpx.Response(200,json={"items":[{"id":"two"}]})
    c=Client("secret-test",transport=httpx.MockTransport(handler),sleep=lambda _:None)
    assert list(c.pages("items"))==[{"id":"one"},{"id":"two"}]
    c.close()
    c=Client("secret-test",transport=httpx.MockTransport(lambda r:httpx.Response(401,text="secret-test private data")),sleep=lambda _:None)
    with pytest.raises(LoyverseError,match="rejected the access token") as e: list(c.pages("items"))
    assert "secret-test" not in str(e.value)
    c.close()

def test_timezone():
    r=receipt("one",1)
    r["receipt_date"]="2026-10-20T20:00:00Z"
    assert str(receipt_values(r)["business_date"])=="2026-10-21"

def test_page_and_export(client,session):
    Fake.fail=False;Fake.receipts=[receipt("1",10)]
    sync_account("A","fake",Fake);sync_account("B","fake",Fake)
    add_user(session,"lv_admin","admin");login(client,"lv_admin")
    r=client.get("/replenishment/loyverse")
    assert r.status_code==200
    assert "Example tea" in r.text
    r=client.get("/replenishment/loyverse?account=A&store=s&export=csv")
    assert r.status_code==200 and "text/csv" in r.headers["content-type"]
    assert "A,Example store,Example tea" in r.text and "B,Example store" not in r.text
    assert "Example tea" not in client.get("/replenishment/loyverse?search=missing&export=csv").text

def test_staff_denied(client,session):
    add_user(session,"lv_staff","staff");login(client,"lv_staff")
    assert client.get("/replenishment/loyverse").status_code==403
    assert client.get("/replenishment/loyverse?export=csv").status_code==403


def test_responsive(client,session):
    Fake.fail=False;Fake.receipts=[receipt("1",10)]
    sync_account("A","fake",Fake)
    add_user(session,"lv_ui","admin");login(client,"lv_ui")
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    def serve(route):
        u=urlsplit(route.request.url)
        r=client.get(u.path+("?"+u.query if u.query else ""))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ("content-length","content-encoding","transfer-encoding")})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page()
        page.route("http://qa.test/**",serve)
        page.route("https://**",lambda r:r.abort())
        for width in (1440,390):
            page.set_viewport_size(dict(width=width,height=900))
            page.goto("http://qa.test/replenishment/loyverse")
            assert page.locator(".loyverse-page").is_visible()
            assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        browser.close()


def test_network_retry():
    calls=[]
    def handler(req):
        calls.append(1)
        if len(calls)==1: raise httpx.ReadTimeout("private token must not leak")
        return httpx.Response(200,json={"stores":[]})
    c=Client("fake",transport=httpx.MockTransport(handler),sleep=lambda _:None)
    assert list(c.pages("stores"))==[] and len(calls)==2
    c.close()

def test_weighted_and_stale(session):
    Fake.fail=False;Fake.receipts=[receipt("1",1.2345)]
    sync_account("A","fake",Fake)
    v=session.get(LoyverseVariant,("A","v"));v.sold_by_weight=True;session.commit()
    assert report(session)["rows"][0]["quantity"]==Decimal("8.642")
    state=session.get(LoyverseAccount,"A");state.last_success_at=now_utc()-timedelta(minutes=16);session.commit()
    assert report(session)["rows"][0]["quantity"] is None


def test_explanation_drawer(client,session):
    Fake.fail=False;Fake.receipts=[receipt('1',10),receipt('2',6,2),receipt('3',2,1,'REFUND')]
    sync_account('A','fake',Fake)
    add_user(session,'why_admin','admin');login(client,'why_admin')
    client.cookies.set('pos_scope','loyverse')
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    def serve(route):
        u=urlsplit(route.request.url);r=client.get(u.path+('?' +u.query if u.query else ''))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        for width in (1440,390):
            page.set_viewport_size(dict(width=width,height=950));page.goto('http://qa.test/replenishment/loyverse')
            page.get_by_role('button',name='Why this number',exact=True).click()
            assert page.locator('#drawer').is_visible()
            assert 'Suggested quantity: 49' in page.locator('#drawer-body').inner_text()
            assert '14' in page.locator('.lv-formula').inner_text()
            page.locator('#drawer-close').click()
            assert not page.locator('#drawer').is_visible()
        browser.close()
