import pytest
from sqlalchemy import select
from app.models import CycleCount, Location, Product
from app.services import counts
from tests.test_web import client, add_user, login, csrf_of

@pytest.fixture
def scan_setup(client,session,monkeypatch):
    loc=Location(storehub_id="SCAN_STORE",name="Scan store",type="store",monitored=True)
    prod=Product(storehub_id="SCAN_PRODUCT",name="Scan product",barcode="123456789",in_api=True)
    session.add_all([loc,prod]);session.commit()
    add_user(session,"scanner_admin","admin");login(client,"scanner_admin")
    csrf=csrf_of(client,"/counts")
    r=client.post("/counts/create",data={"location_id":loc.id,"mode":"scan","csrf":csrf})
    url=r.headers["location"]
    monkeypatch.setattr(counts,"live_quantity",lambda loc,prod:12.0)
    return url,csrf,prod.id,loc.id

def test_scan_save_rescan_and_submit(client,session,scan_setup):
    url,csrf,pid,_=scan_setup
    cid=int(url.rsplit("/",1)[1]);assert len(session.get(CycleCount,cid).lines)==0
    r=client.post(url+"/scan",data={"code":"123456789","csrf":csrf})
    assert r.json()["system"]==12
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":0,"note":"Empty shelf","csrf":csrf}).status_code==200
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":10,"note":"Two missing","csrf":csrf}).status_code==200
    session.expire_all();cc=session.get(CycleCount,cid)
    assert len(cc.lines)==1 and cc.lines[0].variance==-2
    lid=cc.lines[0].id
    r=client.post(url+"/save",data={"csrf":csrf,"submit":"1",f"c_{lid}":10,f"n_{lid}":"Two missing"})
    assert r.status_code==303
    session.expire_all();assert session.get(CycleCount,cid).status=="submitted"
    assert client.post(url+"/scan",data={"code":"123456789","csrf":csrf}).status_code==409

@pytest.mark.parametrize("qty",["-1","nan","inf","100000001"])
def test_invalid_count(client,scan_setup,qty):
    url,csrf,pid,_=scan_setup
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":qty,"csrf":csrf}).status_code==400

def test_failure_csrf_and_scope(client,session,scan_setup,monkeypatch):
    url,csrf,pid,loc=scan_setup
    assert client.post(url+"/scan",data={"code":"123456789","csrf":"bad"}).status_code in (400,403)
    assert client.post(url+"/scan",data={"code":"unknown","csrf":csrf}).status_code==404
    def fail(*args):raise counts.CountError("Unavailable")
    monkeypatch.setattr(counts,"live_quantity",fail)
    assert client.post(url+"/scan",data={"code":"123456789","csrf":csrf}).status_code==503
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":10,"csrf":csrf}).status_code==400
    session.expire_all();assert len(session.get(CycleCount,int(url.rsplit("/",1)[1])).lines)==0
    client.post("/logout",data={"csrf":csrf})
    add_user(session,"other_scanner","staff",[999999]);login(client,"other_scanner")
    assert client.post(url+"/scan",data={"code":"123456789","csrf":csrf_of(client,"/counts")}).status_code==403


def test_product_lookup(client, session, scan_setup):
    url, csrf, pid, _ = scan_setup
    session.add(Product(storehub_id="REMOVED_SCAN", name="Scan deleted", barcode="999", in_api=False))
    session.commit()
    for term in ("scan", "123456", "Scan product"):
        rows = client.get(url + "/products", params={"q": term}).json()
        assert [p["id"] for p in rows] == [pid]
    assert client.get(url + "/products?q=missing").json() == []
    assert client.post(url + "/scan", data={"product_id": pid, "csrf": csrf}).json()["system"] == 12
    assert client.post(url + "/scan", data={"product_id": 999999, "csrf": csrf}).status_code == 404
    client.post("/logout", data={"csrf": csrf})
    add_user(session, "lookup_other", "staff", [999999]); login(client, "lookup_other")
    assert client.get(url + "/products?q=scan").status_code == 403


def test_count_lookup_browser(client, scan_setup):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    url, _, _, _ = scan_setup
    def serve(route):
        req = route.request; u = urlsplit(req.url)
        r = client.request(req.method, u.path + ("?" + u.query if u.query else ""), content=req.post_data_buffer,
                           headers={k:v for k,v in req.all_headers().items() if k in ("content-type", "x-requested-with")})
        route.fulfill(status=r.status_code, body=r.content, headers={k:v for k,v in r.headers.items() if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.route("http://qa.test/**", serve)
        page.route("https://**", lambda r:r.abort())
        for width in (1440, 390):
            page.set_viewport_size({"width":width,"height":900})
            page.goto("http://qa.test" + url)
            page.locator("#count-code").fill("Scan prod")
            page.locator("#count-products [role=option]").first.wait_for()
            page.locator("#count-code").press("ArrowDown")
            page.locator("#count-code").press("Enter")
            page.locator("#count-entry").wait_for(state="visible")
            assert page.locator("#count-product").inner_text() == "Scan product"
            assert page.locator("#count-system").inner_text() == "12"
            assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
            page.locator("#count-cancel").click()
            page.locator("#count-code").fill("123456789")
            page.locator("#count-code").press("Enter")
            page.locator("#count-entry").wait_for(state="visible")
        browser.close()
