from tests.test_web import add_user, client, login

def test_users_layout(session,client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    add_user(session,"layout_admin","admin")
    add_user(session,"layout_staff","staff")
    login(client,"layout_admin")
    def serve(route):
        u=urlsplit(route.request.url)
        r=client.get(u.path+("?"+u.query if u.query else ""))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ("content-length","content-encoding","transfer-encoding")})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page()
        page.route("http://qa.test/**",serve)
        page.route("https://**",lambda r:r.abort())
        for width in (1440,390,320):
            page.set_viewport_size({"width":width,"height":900})
            page.goto("http://qa.test/users")
            assert page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth")
            record=page.locator('.user-record').first
            assert not record.evaluate('e=>e.open')
            record.locator('summary').click()
            assert record.locator('input[name=full_name]').is_visible()
            assert page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth")
            record.locator('summary').click()
            page.locator('[data-modal=user-create]').click()
            dialog=page.locator('#user-create')
            assert dialog.is_visible()
            box=dialog.bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=width
            assert dialog.locator('input[name=username]').is_visible()
            assert dialog.evaluate('e=>e.scrollWidth<=e.clientWidth')
            dialog.locator('.user-modal-head [data-modal-close]').click()
            assert not dialog.is_visible()
        browser.close()
