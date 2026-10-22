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
                assert abs(bar['y']+bar['height']-1000)<2
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
