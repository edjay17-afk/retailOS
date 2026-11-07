from tests.test_new_product_lists import _setup, _no_storehub_or_internet, client
from tests.test_web import login

def test_layout(sync,fake,session,client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    from pathlib import Path
    _setup(sync,fake,session)
    login(client,"np_user")
    def serve(route):
        req=route.request; u=urlsplit(req.url)
        r=client.request(req.method,u.path+("?"+u.query if u.query else ""),content=req.post_data_buffer,headers={k:v for k,v in req.all_headers().items() if k in ["content-type","x-requested-with"]})
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ["content-length","content-encoding","transfer-encoding"]})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page(); errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.route("http://qa.test/**",serve)
        page.route("https://**",lambda r:r.abort())
        for width in (1600,1024,390):
            page.set_viewport_size({"width":width,"height":1000})
            page.goto("http://qa.test/new-product")
            for scroll in (0, 10000):
                page.evaluate("y=>window.scrollTo(0,y)", scroll)
                bar=page.locator('.np-entry-bar').bounding_box()
                assert abs(bar['y']+bar['height']-(936 if width<=700 else 1000))<2   # on a phone the bar sits just above the 64 px tab bar
            page.evaluate("()=>window.scrollTo(0,0)")
            page.locator("#np-scan").fill("88776655")
            page.locator("#np-scan").press("Enter")
            page.locator("#np-name").wait_for(state="visible")
            page.locator("#np-name").fill("Sample product")
            assert page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth")
            if width==1600:
                a=page.locator("#np-app").bounding_box(); b=page.locator(".np-review").bounding_box()
                assert b["x"]>a["x"]+a["width"]
                Path("docs/previews").mkdir(parents=True,exist_ok=True)
                page.screenshot(path="docs/previews/new-product-desktop.png",full_page=True)
                page.locator("#np-supplier").fill("Acme Foods")
                page.locator("#np-category").fill("Snacks")
                page.locator("#np-qty").fill("12")
                page.locator("#np-add").click()
                page.wait_for_function("()=>document.querySelectorAll('#np-lines .np-row').length===1")
                assert page.locator("#np-save").is_enabled()
                page.locator("#np-lines .np-remove").click()
                page.wait_for_function("()=>document.querySelectorAll('#np-lines .np-row').length===0")
        assert not errors
        page.goto("http://qa.test/stock-transfers")
        headers=page.locator("table thead").inner_text()
        assert "Total(PHP)" in headers and "Imported" not in headers and "Export total" not in headers
        browser.close()


def test_breadcrumb_goes_back_to_the_page_you_came_from(sync, fake, session, client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    _setup(sync, fake, session)
    login(client, "np_user")
    def serve(route):
        req = route.request; u = urlsplit(req.url)
        r = client.request(req.method, u.path + ("?" + u.query if u.query else ""), content=req.post_data_buffer, headers={k: v for k, v in req.all_headers().items() if k in ["content-type", "x-requested-with"]})
        route.fulfill(status=r.status_code, body=r.content, headers={k: v for k, v in r.headers.items() if k.lower() not in ["content-length", "content-encoding", "transfer-encoding"]})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(); errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("http://qa.test/**", serve)
        page.route("https://**", lambda r: r.abort())
        first = lambda: page.locator(".crumbs a").first
        page.goto("http://qa.test/stock-transfers")
        assert first().inner_text().strip() == "Dashboard"                           # opened directly: the normal parent
        page.goto("http://qa.test/new-product?x=1", referer="http://qa.test/stock-transfers")
        assert first().inner_text().strip() == "Stock transfers" and first().get_attribute("href") == "/stock-transfers"
        page.goto("http://qa.test/new-product?x=2", referer="http://qa.test/new-product?x=1")   # same list, other filter: its own entry is replaced
        assert first().get_attribute("href") == "/stock-transfers"
        page.goto("http://qa.test/stock-transfers", referer="http://qa.test/new-product?x=2")   # back to the earlier page
        assert first().inner_text().strip() == "Dashboard"
        page.goto("http://qa.test/new-product")                                      # typed address: starts again
        assert first().inner_text().strip() == "Dashboard"
        assert not errors
        browser.close()


def test_dashboard_checklists_refresh_without_reload(sync, fake, session, client):
    """The Today page re-reads its numbers: a checklist finished after the page opened shows without a reload."""
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    from sqlalchemy import select
    from app.models import Inspection
    from tests.test_web import add_user
    _setup(sync, fake, session)
    add_user(session, "dash_admin", "admin"); login(client, "dash_admin")
    def serve(route):
        req = route.request; u = urlsplit(req.url)
        r = client.request(req.method, u.path + ("?" + u.query if u.query else ""), content=req.post_data_buffer, headers={k: v for k, v in req.all_headers().items() if k in ["content-type", "x-requested-with"]})
        route.fulfill(status=r.status_code, body=r.content, headers={k: v for k, v in r.headers.items() if k.lower() not in ["content-length", "content-encoding", "transfer-encoding"]})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(); errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("http://qa.test/**", serve)
        page.route("https://**", lambda r: r.abort())
        page.clock.install()
        page.goto("http://qa.test/")
        foot = page.locator('[data-live^="foot-"]').first
        assert foot.count() >= 1
        if True:
            before = foot.inner_html()
            foot.evaluate("e => { e.innerHTML = '<b>stale</b>'; }")        # stand-in for numbers that changed on the server
            page.clock.run_for(61000)
            page.wait_for_function("() => !document.querySelector('[data-live^=\"foot-\"]').innerHTML.includes('stale')")
            assert foot.inner_html() == before
        assert not errors
        browser.close()
