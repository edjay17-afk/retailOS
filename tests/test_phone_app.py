"""Responsive checks use an isolated database and mocked stock, never live POS data."""
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from app.models import InventoryCurrent, Product
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

                page.goto('http://qa.test/counts')
                page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
                assert page.locator('.count-mobile-card').is_visible()
                assert not page.locator('.counts-list-table').is_visible()
                assert page.locator('.count-mobile-card').get_by_role('link', name='Open').get_attribute('href') == count_url
                head = page.locator('.counts-page-head').bounding_box()
                upload = page.locator('[data-modal="counts-upload"]').bounding_box()
                assert abs(upload['x'] + upload['width'] - head['x'] - head['width']) < 2
                page.locator('[data-modal="counts-upload"]').click()
                assert page.locator('#counts-upload').is_visible()
                page.locator('#counts-upload [data-modal-close]').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        page.set_viewport_size({'width': 320, 'height': 844})
        page.goto('http://qa.test' + count_url)
        assert page.locator('#count-code').is_visible()
        assert page.locator('[data-scan="count-code"]').is_visible()
        assert page.locator('#count-find button[type=submit]').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
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


def test_stock_pagination_and_count_action_stay_accessible(client, session, scan_setup):
    _, _, _, loc = scan_setup
    for i in range(55):
        product = Product(storehub_id=f'PHONE_STOCK_{i}', name=f'Product {i:02d}', in_api=True)
        session.add(product)
        session.flush()
        session.add(InventoryCurrent(location_id=loc, product_id=product.id, on_hand=5))
    session.commit()

    def serve(route):
        req = route.request
        url = urlsplit(req.url)
        response = client.request(req.method, url.path + ('?' + url.query if url.query else ''),
                                  content=req.post_data_buffer,
                                  headers={k: v for k, v in req.all_headers().items()
                                           if k in ('content-type', 'x-requested-with')})
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda route: route.abort())
        for width in (320, 390, 700):
            page.set_viewport_size({'width': width, 'height': 844})
            page.goto(f'http://qa.test/inventory?loc={loc}')
            cta = page.locator('.phone-stock-cta')
            top = cta.bounding_box()['y']
            for position in ('document.body.scrollHeight / 2', 'document.body.scrollHeight'):
                page.evaluate(f'window.scrollTo(0, {position})')
                assert abs(cta.bounding_box()['y'] - top) < 1
            last = page.locator('.phone-stock-row').last.bounding_box()
            next_button = page.locator('.stock-page-next').bounding_box()
            pager = page.locator('.stock-pager').bounding_box()
            assert next_button['y'] >= last['y'] + last['height']
            assert abs(next_button['x'] + next_button['width'] - pager['x'] - pager['width']) < 1
            assert next_button['y'] + next_button['height'] <= cta.bounding_box()['y']
            assert not page.locator('.stock-pager .small').is_visible()
            page.locator('.stock-page-next').click()
            page.locator('.stock-page-prev').wait_for(state='visible')
            assert page.locator('.stock-page-prev').is_visible()
            assert page.locator('.stock-page-next').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()
