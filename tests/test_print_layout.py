import pytest
from tests.test_order_documents import setup_document, client, add_user, login

@pytest.mark.parametrize('kind,prefix',[('po','/purchase-orders'),('transfer','/stock-transfers')])
def test_print_preview(session,client,kind,prefix):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    from pathlib import Path
    source,*_=setup_document(session,kind)
    add_user(session,'print_viewer','warehouse');login(client,'print_viewer')
    def serve(route):
        u=urlsplit(route.request.url);r=client.get(u.path)
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        if kind == 'po':
            for detail_width in (1440,390):
                page.set_viewport_size({'width':detail_width,'height':1000})
                page.goto(f'http://qa.test{prefix}/{source.id}')
                assert page.locator('.po-amounts').is_visible()
                assert page.locator('.po-order-items').is_visible()
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                assert 'Calculated totals' in page.locator('.po-amounts').inner_text()
                assert '807.857136' in page.locator('.po-amounts').inner_text()
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':1000});page.goto(f'http://qa.test{prefix}/{source.id}/print')
            assert page.locator('.print-toolbar button').is_visible()
            assert page.locator('.print-toolbar').bounding_box()['width'] <= width
            assert page.locator('.print-brand').count() == 1
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
        page.set_viewport_size({'width':1440,'height':1000})
        Path('docs/previews').mkdir(parents=True,exist_ok=True)
        page.screenshot(path=f'docs/previews/print-{kind}.png',full_page=True)
        page.emulate_media(media='print')
        assert not page.locator('.print-toolbar').is_visible()
        assert page.locator('.print-grand-total').is_visible()
        assert page.locator('.print-sheet h1').evaluate('e=>getComputedStyle(e).fontSize') == '30.6667px'
        pdf=page.pdf(format='A4',prefer_css_page_size=True)
        assert pdf.startswith(b'%PDF')
        browser.close()
