from tests.test_replenishment_sheets import setup_recs, client
from tests.test_web import add_user, login

def test_mobile_layout_and_save(sync,fake,session,client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    from pathlib import Path
    setup_recs(session,sync,fake)
    add_user(session,"mobile_admin","admin")
    login(client,"mobile_admin")
    def serve(route):
        req=route.request;u=urlsplit(req.url)
        r=client.request(req.method,u.path+("?"+u.query if u.query else ""),content=req.post_data_buffer,headers={k:v for k,v in req.all_headers().items() if k in ("content-type","x-requested-with")})
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ("content-length","content-encoding","transfer-encoding")})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page();errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.route("http://qa.test/**",serve)
        page.route("https://**",lambda r:r.abort())
        for width in (390,320,1440):
            page.set_viewport_size({"width":width,"height":844})
            page.goto("http://qa.test/replenishment?view=list")
            assert page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth")
            if width>700:
                assert page.locator('.rep-guide').evaluate('e=>e.open')
                assert page.locator('.rep-filter-panel').evaluate('e=>e.open')
                continue
            assert not page.locator('.rep-guide').evaluate('e=>e.open')
            assert not page.locator('.rep-filter-panel').evaluate('e=>e.open')
            page.locator('.rep-filter-panel>summary').click()
            assert page.locator('.rep-filter-panel').evaluate('e=>e.open')
            filters=page.locator('.filters').bounding_box()
            row=page.locator('.repl tbody tr').first.bounding_box()
            assert filters['y']<row['y']
            checkbox=page.locator('.rc').first
            checkbox.check()
            bar=page.locator('#repl-selection').bounding_box()
            assert abs(bar['y']+bar['height']-(780 if width<=700 else 844))<2   # on a phone the bar sits just above the 64 px tab bar
            assert page.locator('#repl-proceed').is_enabled()
            tr=checkbox.locator('xpath=ancestor::tr')
            qty=tr.locator('input[name=qty]')
            next_qty=str(int(qty.input_value())+1)
            qty.fill(next_qty)
            assert page.locator('#repl-proceed').is_disabled()
            tr.locator('.repl-save-button').click()
            page.wait_for_function("()=>!document.getElementById('repl-proceed').disabled")
            assert qty.input_value()==next_qty
            if width==390:
                Path('docs/previews').mkdir(parents=True,exist_ok=True)
                page.screenshot(path='docs/previews/replenishment-mobile.png',full_page=True)
            checkbox.uncheck()
        assert not errors
        browser.close()
