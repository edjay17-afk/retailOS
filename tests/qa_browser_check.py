"""Repeatable browser QA against a temporary DB; no live business data or network calls."""
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy import select
from app.models import Location, Product
from tests.test_web import login
from app.timeutil import today_local

class BrowserCheck:
    def __init__(self, catalog=None, folder='docs/qa-2026-10-20'):
        self.catalog = catalog
        self.folder = folder

    def pytest_runtest_teardown(self, item, nextitem):
        client = item.funcargs['client']; session = item.funcargs['session']
        login(client, 'qa_admin')
        location = session.scalar(select(Location).where(Location.storehub_id == 'S_A'))
        product = session.scalar(select(Product).where(Product.storehub_id == 'P1'))
        folder = Path(self.folder); folder.mkdir(exist_ok=True)
        if self.catalog:
            for target, source in zip(session.scalars(select(Product).order_by(Product.id)), self.catalog):
                target.name = source['name']
                target.barcode = source['barcode']
                target.category = source['category']
            session.commit()
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge')
            page = browser.new_page(viewport={'width':1440,'height':1000}, reduced_motion='reduce')
            if self.catalog:
                page.add_init_script("""document.addEventListener('DOMContentLoaded',()=>{
                  const e=document.createElement('div');
                  e.textContent='QA preview: real catalog products; test sales, stock, stores and accounts.';
                  e.style.cssText='padding:10px 14px;margin-bottom:16px;border:1px solid #d4dcd8;border-radius:8px;font:12px sans-serif;color:#33564c;background:#edf6f1';
                  (document.querySelector('.content')||document.body).prepend(e);
                });""")
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            def serve(route):
                req = route.request; url = urlsplit(req.url)
                assert req.method == 'GET', 'Browser verification must not submit business changes'
                response = client.get(url.path + ('?' + url.query if url.query else ''))
                route.fulfill(status=response.status_code, body=response.content,
                              headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
            page.route('http://qa.test/**', serve)
            page.route('https://**', lambda route: route.abort())
            page.goto(f'http://qa.test/sales/product/{product.id}?days=1&loc={location.id}')
            assert page.locator('.content').inner_text().find('123.45') >= 0
            page.screenshot(path=str(folder/'product-scope.png'))
            page.goto('http://qa.test/stock-events')
            page.locator('.stock-change-head').screenshot(path=str(folder/'stock-changes-text.png'))
            page.goto('http://qa.test/sales/products')
            if self.catalog:
                page.screenshot(path=str(folder/'sales-by-product.png'))
            page.locator('.drp-btn').click()
            page.locator('.drp-boxes input').first.fill('13/99/2026')
            page.locator('.drp-foot button').filter(has_text='Apply').click()
            assert page.locator('.drp-error').inner_text()
            assert page.locator('.drp-pop').is_visible()
            assert not page.locator('#chat-fab').is_visible()
            page.locator('.drp-pop').screenshot(path=str(folder/'invalid-date-desktop.png'))
            for width in [390, 320]:
                page.set_viewport_size({'width':width,'height':844})
                box = page.locator('.drp-pop').bounding_box()
                assert box['x'] >= 0 and box['x'] + box['width'] <= width+1
            page.set_viewport_size({'width':390,'height':844})
            page.locator('.drp-pop').screenshot(path=str(folder/'invalid-date-mobile.png'))
            page.locator('.drp-foot button').filter(has_text='Cancel').click()
            page.locator('.drp-btn').click()
            page.locator('.drp-opt').first.click()
            page.locator('.drp-foot button').filter(has_text='Apply').click()
            page.wait_for_url('**days=custom**')
            assert ('frm='+today_local().isoformat()) in page.url
            page.set_viewport_size({'width':1440,'height':1000})
            from app import settings_service
            settings_service.set_(session, 'repl.mode', 'sales_only'); session.commit()
            page.goto('http://qa.test/')
            card = page.locator('.ops-card').filter(has_text='Days of stock left')
            assert 'Not used in sales-only' in card.inner_text()
            card.screenshot(path=str(folder/'sales-only-cover.png'))
            page.goto('http://qa.test/replenishment')
            page.screenshot(path=str(folder/'automatic-suggestions.png'))
            assert not errors, errors
            browser.close()

if __name__ == '__main__':
    raise SystemExit(pytest.main(['tests/test_qa_followup.py::test_product_detail_keeps_one_day_and_location','-q'], plugins=[BrowserCheck()]))

