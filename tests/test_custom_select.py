from urllib.parse import urlsplit

from tests.test_web import client, add_user, login, seeded


def _page(pw, client, width=1440, height=900, touch=False):
    browser = pw.chromium.launch(channel='msedge', headless=True)
    ctx = browser.new_context(viewport={'width': width, 'height': height}, has_touch=touch, is_mobile=touch)
    page = ctx.new_page()

    def serve(route):
        u = urlsplit(route.request.url)
        url = u.path + ('?' + u.query if u.query else '')
        if route.request.method != 'GET':
            route.abort(); return
        r = client.get(url, follow_redirects=False)
        route.fulfill(status=r.status_code, body=r.content,
                      headers={k: v for k, v in r.headers.items() if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})
    page.route('http://qa.test/**', serve); page.route('https://**', lambda r: r.abort())
    return browser, page


def test_dropdowns_use_our_own_list_and_still_change_the_select(sync, fake, session, client):
    from playwright.sync_api import sync_playwright
    seeded(sync, fake, session)
    add_user(session, 'cs_admin', 'admin'); login(client, 'cs_admin')
    with sync_playwright() as pw:
        browser, page = _page(pw, client)
        page.goto('http://qa.test/sales/products')
        sel = page.locator('.psf-sort select')
        sel.click()
        pop = page.locator('.cs-pop')
        pop.wait_for(state='visible')
        assert [t.strip() for t in page.locator('.cs-opt .cs-text').all_text_contents()] == ['Sales', 'Units', 'Avg price', 'Name']
        assert page.locator('.cs-opt.is-on .cs-text').text_content().strip() == 'Sales'
        box, chip = pop.bounding_box(), page.locator('.psf-sort').bounding_box()
        assert box['y'] >= chip['y'] + chip['height'] and abs(box['x'] - chip['x']) < 30 or box['x'] + box['width'] <= 1440
        page.keyboard.press('Escape')
        page.wait_for_selector('.cs-pop', state='hidden')
        sel.click()
        page.locator('.cs-opt', has_text='Units').click()
        page.wait_for_function("()=>location.search.includes('sort=units')")
        assert page.locator('.psf-sort select').input_value() == 'units'
        browser.close()


def test_dropdown_keyboard_and_phone_sheet(sync, fake, session, client):
    from playwright.sync_api import sync_playwright
    seeded(sync, fake, session)
    add_user(session, 'cs_phone', 'admin'); login(client, 'cs_phone')
    with sync_playwright() as pw:
        browser, page = _page(pw, client)
        page.goto('http://qa.test/sales/products')
        page.locator('.psf-sort select').focus()
        page.keyboard.press('ArrowDown')
        page.wait_for_selector('.cs-pop', state='visible')
        page.keyboard.press('ArrowDown'); page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
        page.wait_for_function("()=>location.search.includes('sort=price')")
        browser.close()
        browser, page = _page(pw, client, 390, 844, touch=True)
        page.goto('http://qa.test/sales/products')
        page.locator('.psf-sort select').tap()
        page.wait_for_selector('.cs-pop.is-sheet', state='visible')
        page.wait_for_timeout(500)
        box = page.locator('.cs-pop').bounding_box()
        assert abs(box['y'] + box['height'] - 844) < 2 and box['width'] >= 389       # a bottom sheet
        page.locator('.cs-opt', has_text='Name').tap()
        page.wait_for_function("()=>location.search.includes('sort=name')")
        browser.close()
