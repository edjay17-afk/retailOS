from app.models import Employee
from tests.test_web import client, add_user, login, csrf_of


def test_employee_directory(client, session):
    session.add_all([Employee(storehub_id="QA-EMP-1", first_name="Example", last_name="Person", email="example@example.test", phone="12345"), Employee(storehub_id="QA-EMP-2", first_name="Second", last_name="Employee")])
    session.commit()
    add_user(session,"directory_admin","admin"); login(client,"directory_admin")
    r=client.get("/employees")
    assert r.status_code == 200
    assert "example@example.test" in r.text and "Not provided" in r.text
    for query in ("Example Person", "example@", "12345", "QA-EMP-1"):
        r=client.get("/employees",params={"q":query})
        assert "Example Person" in r.text and "Second Employee" not in r.text
    assert "No matching employees" in client.get("/employees?q=unknown").text
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
            page.set_viewport_size({"width":width,"height":900})
            page.goto("http://qa.test/employees")
            assert page.locator(".employee-table").is_visible()
            from pathlib import Path
            preview = Path("docs/previews"); preview.mkdir(parents=True, exist_ok=True)
            if width == 390:
                page.locator("#mt-more").click()
                page.locator(".more-all>summary").click()
                page.locator("#more-sheet").screenshot(path=str(preview / f"sidebar-reference-{width}.png"))
                assert page.locator('#more-sheet .more-all a[href="/counts"]').is_visible()
            else:
                page.locator("#sidebar").screenshot(path=str(preview / f"sidebar-reference-{width}.png"))
                assert page.locator('[data-group="Team"] > button').get_attribute("aria-expanded") == "false"
                page.locator('[data-group="Inventory"] > button').click()
                assert page.locator('[data-group="Team"] > button').get_attribute("aria-expanded") == "false"
                assert page.locator('[data-group="Inventory"] > button').get_attribute("aria-expanded") == "true"
                assert page.locator('#sidebar-nav a[href="/counts"]').is_visible()
                page.locator('[data-group="Settings"] > button').click()
                assert page.locator('#sidebar-nav a[href="/users"]').is_visible()
            assert page.locator("#sidebar-nav > .nav-group").count() == 9
            assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        browser.close()
    csrf=csrf_of(client,"/employees")
    client.post("/logout",data={"csrf":csrf})
    add_user(session,"directory_staff","staff");login(client,"directory_staff")
    assert client.get("/employees").status_code == 403


def test_employee_store_filter(client, session):
    from datetime import datetime
    from app.models import Location, Timesheet
    store=Location(storehub_id="EMP-STORE",name="Directory Store",type="store",monitored=True)
    session.add(store);session.flush()
    session.add_all([Employee(storehub_id="EMP-A",first_name="AssignedExample"),Employee(storehub_id="EMP-B",first_name="UnknownExample"),
                     Timesheet(employee_sh_id="EMP-A",location_id=store.id,clock_in=datetime(2026,10,22,8))])
    session.commit()
    add_user(session,"store_filter_admin","admin");login(client,"store_filter_admin")
    r=client.get("/employees",params={"loc":store.id})
    assert r.status_code==200 and "AssignedExample" in r.text and "UnknownExample" not in r.text
    assert "Directory Store" in r.text
    r=client.get("/employees?loc=unknown")
    assert "UnknownExample" in r.text and "AssignedExample" not in r.text
    assert "No matching employees" in client.get("/employees",params={"loc":store.id,"q":"UnknownExample"}).text
    assert client.get("/employees?loc=bad").status_code==400
    assert client.get("/employees?loc=999999").status_code==403


def test_sidebar_width_before_app_script_loads(client, session):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    add_user(session,"sidebar_initial_admin","admin"); login(client,"sidebar_initial_admin")
    def serve(route):
        u=urlsplit(route.request.url)
        if u.path == "/static/app.js":
            route.abort(); return
        r=client.get(u.path+("?"+u.query if u.query else ""))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ("content-length","content-encoding","transfer-encoding")})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page(viewport={"width":1440,"height":900})
        page.route("http://qa.test/**",serve)
        page.route("https://**",lambda r:r.abort())
        page.goto("http://qa.test/employees")
        assert round(page.locator("#sidebar").bounding_box()["width"]) == 276
        page.goto("http://qa.test/employees?q=none")
        assert round(page.locator("#sidebar").bounding_box()["width"]) == 276
        page.evaluate("() => localStorage.setItem('retailos.navigation','compact')")   # an old saved choice no longer collapses the sidebar
        page.reload()
        assert round(page.locator("#sidebar").bounding_box()["width"]) == 276
        browser.close()


def test_navigation_highlight_and_language_motion(client, session):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    add_user(session,"motion_admin","admin");login(client,"motion_admin")
    def serve(route):
        u=urlsplit(route.request.url)
        r=client.get(u.path+("?"+u.query if u.query else ""))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ("content-length","content-encoding","transfer-encoding")})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page(viewport={"width":1440,"height":900})
        page.route("http://qa.test/**",serve);page.route("https://**",lambda r:r.abort())
        page.clock.install();page.goto("http://qa.test/employees")
        page.locator('[data-group="Inventory"] > button').click()
        assert page.locator('[data-group="Inventory"].fly-open').count()==1          # a section with many pages opens a second panel
        page.locator('#sidebar-nav a[href="/inventory"]').click()                     # choosing a page opens it and closes the panel; no highlight is dragged across
        page.wait_for_url('**/inventory')
        assert page.locator('[data-group="Inventory"].fly-open').count()==0
        assert page.locator('.navigation-moving-highlight').count()==0
        assert round(page.locator('#sidebar').bounding_box()['width'])==276
        page.locator('.sidebar-language a[hreflang="zh"]').click()
        assert page.locator('.sidebar-language').get_attribute('data-target')=='zh'
        page.clock.run_for(250)
        page.wait_for_function("() => document.documentElement.lang === 'zh'")
        assert page.locator('.sidebar-language a[hreflang="zh"]').get_attribute('aria-current')=='true'
        assert round(page.locator('#sidebar').bounding_box()['width'])==276
        page.emulate_media(reduced_motion="reduce")
        page.locator('.sidebar-language a[hreflang="en"]').click()
        page.wait_for_function("() => document.documentElement.lang === 'en'")
        browser.close()


def test_sections_open_a_small_menu_beside_their_button_on_computers_and_a_drop_down_on_phones(client, session):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    add_user(session, "panel_admin", "admin"); login(client, "panel_admin")
    def serve(route):
        u = urlsplit(route.request.url)
        r = client.get(u.path + ("?" + u.query if u.query else ""))
        route.fulfill(status=r.status_code, body=r.content, headers={k: v for k, v in r.headers.items() if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("http://qa.test/**", serve); page.route("https://**", lambda r: r.abort())
        page.goto("http://qa.test/employees")
        links = page.locator('[data-group="Inventory"] .ng-links')
        assert not links.is_visible()                                                 # closed until you ask
        page.locator('[data-group="Inventory"] > button').click()
        assert links.is_visible()
        assert page.locator('[data-group="Inventory"] > button').get_attribute("aria-expanded") == "true"
        page.locator('[data-group="Sales"] > button').click()                         # one panel at a time
        assert not links.is_visible() and page.locator('[data-group="Sales"] .ng-links').is_visible()
        page.keyboard.press("Escape")
        assert not page.locator('[data-group="Sales"] .ng-links').is_visible()
        page.locator('[data-group="Inventory"] > button').click()
        page.mouse.click(900, 700)                                                    # a click elsewhere closes it
        assert not links.is_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("http://qa.test/employees")
        page.locator("#mt-more").click()
        page.locator(".more-all>summary").click()
        assert page.locator('#more-sheet .more-all a[href="/counts"]').is_visible() and not page.locator('[data-group="Inventory"].fly-open').count()
        browser.close()
