from tests.test_web import add_user, client, login, seeded, csrf_of
from app.models import Product
from sqlalchemy import select

def test_wastage_search(sync,fake,session,client):
    seeded(sync,fake,session)
    add_user(session,'waste_admin','admin');login(client,'waste_admin')
    p=session.scalar(select(Product).where(Product.barcode=='111'))
    rows=client.get('/wastage/products?q=Cola').json()
    assert any(r['id']==p.id for r in rows)
    assert client.get('/wastage/products?q=111').json()[0]['id']==p.id
    assert client.get('/wastage/products?q=missing').json()==[]
    assert client.get('/wastage/products?q=').json()==[]
    p.in_api=False;session.commit()
    assert client.get('/wastage/products?q=111').json()==[]
    csrf=csrf_of(client,'/wastage')
    r=client.post('/wastage/add',data={'csrf':csrf,'location_id':2,'product_id':p.id,'product':p.name,'qty':1,'reason':'Damaged'})
    assert 'err=' in r.headers['location']
    client.cookies.clear()
    assert client.get('/wastage/products?q=111').status_code!=200

def test_wastage_lookup_browser(sync,fake,session,client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    seeded(sync,fake,session)
    add_user(session,'lookup_admin','admin');login(client,'lookup_admin')
    def serve(route):
        u=urlsplit(route.request.url)
        assert route.request.method=='GET'
        r=client.get(u.path+('?' + u.query if u.query else ''))
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':900});page.goto('http://qa.test/wastage')
            field=page.locator('#waste-product');field.fill('Cola')
            page.locator('#waste-products [role=option]').first.wait_for(state='visible')
            field.press('ArrowDown');field.press('Enter')
            assert field.input_value()=='Cola 500ml'
            assert int(page.locator('#waste-product-id').input_value())>0
            field.fill('111')
            page.wait_for_function("()=>document.getElementById('waste-product').value==='Cola 500ml'")
            assert page.locator('[data-scan=waste-product]').is_visible()
            assert field.get_attribute('data-scan-fill-only') is not None
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
        browser.close()


def test_expiry_lookup_save_and_next(sync,fake,session,client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    from app.timeutil import today_local
    from datetime import timedelta
    seeded(sync,fake,session)
    add_user(session,'expiry_lookup','admin');login(client,'expiry_lookup')
    assert client.get('/expiry/products?q=Cola').json()[0]['name']=='Cola 500ml'
    def serve(route):
        req=route.request;u=urlsplit(req.url)
        r=client.request(req.method,u.path+('?' + u.query if u.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.all_headers().items() if k in ('content-type','x-requested-with')})
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':900});page.goto('http://qa.test/expiry')
            page.locator('[data-modal=expiry-record]').click()
            field=page.locator('#expiry-product-code');field.fill('Cola')
            page.locator('#waste-products [role=option]').first.click()
            assert field.input_value()=='Cola 500ml'
            form=page.locator('#expiry-record-form')
            form.locator('[name=qty]').fill('2')
            form.locator('[name=expiry]').fill(str(today_local()+timedelta(days=30)))
            form.locator('button:not([type=button])').click()
            page.wait_for_function("()=>document.getElementById('expiry-record-message').textContent.includes('Ready for the next batch')")
            assert page.locator('#expiry-record').is_visible()
            assert field.input_value()==''
            assert page.locator('#waste-product-id').input_value()=='0'
            assert not page.locator('#waste-products').is_visible()
            assert page.locator('#waste-lookup-status').inner_text()==''
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
        browser.close()
