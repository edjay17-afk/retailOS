"""Responsive checks use an isolated database and mocked stock, never live POS data."""
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from tests.test_count_scanner import scan_setup
from tests.test_web import client


def test_phone_layout_and_desktop_isolation(client, scan_setup):
    count_url, _, _, loc = scan_setup

    def serve(route):
        req = route.request
        u = urlsplit(req.url)
        response = client.request(
            req.method, u.path + ('?' + u.query if u.query else ''),
            content=req.post_data_buffer,
            headers={k: v for k, v in req.all_headers().items()
                     if k in ('content-type', 'x-requested-with')},
        )
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda r: r.abort())
        for path in (f'/inventory?loc={loc}', count_url):
            page.goto('http://qa.test' + path)
            # Pixel comparison: new mobile CSS has no effect on desktop rendering.
            page.add_style_tag(content='.phone-only{display:none!important}')
            before = page.screenshot(animations='disabled')
            page.locator('link[href*="phone_app.css"]').evaluate('(el)=>el.disabled=true')
            assert page.screenshot(animations='disabled') == before

        for theme in ('light', 'dark'):
            for width in (320, 390, 700):
                page.set_viewport_size({'width': width, 'height': 844})
                page.goto(f'http://qa.test/inventory?loc={loc}')
                page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
                assert page.locator('.phone-stock-list').is_visible()
                assert not page.locator('main .tw').is_visible()
                page.get_by_role('button', name='More filters', exact=True).click()
                assert page.locator('#inventory-category').is_visible()
                page.get_by_role('button', name='Work', exact=True).click()
                assert page.locator('#phone-work').is_visible()
                page.locator('#phone-work [data-modal-close]').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        page.goto('http://qa.test' + count_url)
        page.locator('#count-code').fill('123456789')
        page.locator('#count-code').press('Enter')
        page.locator('#count-entry').wait_for(state='visible')
        page.locator('#count-qty').fill('10')
        assert page.locator('#phone-count-diff').inner_text() == 'Difference: -2'
        page.locator('#count-entry button[type=submit]').click()
        page.locator('#count-entry').wait_for(state='hidden')
        assert '10 counted' in page.locator('#count-message').inner_text()
        assert page.locator('#count-code').input_value() == ''
        browser.close()
