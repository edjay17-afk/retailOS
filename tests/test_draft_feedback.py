"""Draft save feedback uses temporary databases and mocked StoreHub only."""
import pytest
from urllib.parse import urlsplit
from sqlalchemy import select
from app.models import Location
from tests.test_web import client, login, csrf_of
from tests.test_po_drafts import _setup, _start


def test_draft_feedback_in_browser(client, session, sync, fake):
    from playwright.sync_api import sync_playwright
    loc, product = _setup(sync, fake, session)
    login(client, 'po_w'); _start(client, session, loc)
    target = session.scalar(select(Location).where(Location.type == 'store'))
    response = client.post('/transfer-sheets/new', data={'csrf':csrf_of(client, '/transfer-sheets'),'target_id':target.id})
    transfer_url = response.headers['location']
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page(viewport={'width':1440,'height':950})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def serve(route):
            req=route.request; u=urlsplit(req.url)
            r=client.request(req.method,u.path+('?' + u.query if u.query else ''),content=req.post_data_buffer,
                             headers={k:v for k,v in req.all_headers().items() if k in ['content-type','x-requested-with']})
            route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ['content-length','content-encoding','transfer-encoding']})
        page.route('http://qa.test/**',serve)
        page.route('https://**',lambda route:route.abort())
        for url, prefix, line_input, final in [(transfer_url,'ts','#ts-lines input','.ts-bar button'),('/purchase-orders/new','po','#po-lines input','#po-save')]:
            page.goto('http://qa.test'+url)
            page.locator('#'+prefix+'-scan').fill('111')
            page.locator('#'+prefix+'-scan').press('Enter')
            page.locator('#'+prefix+'-qty').fill('12')
            if prefix=='po': page.locator('#po-price').fill('5')
            page.evaluate('''()=>{const real=window.fetch;window.fetch=(url,opts)=>{
                if(opts&&opts.method==='POST'&&String(url).endsWith('/add'))return new Promise(resolve=>{window.releaseSave=()=>resolve(real(url,opts));});
                return real(url,opts);
            };}''')
            page.locator('#'+prefix+'-add').click()
            assert page.locator('#draft-save-status').inner_text()=='Saving draft...'
            assert page.locator(final).is_disabled()
            page.evaluate('()=>{window.releaseSave();}')
            page.wait_for_function("()=>document.getElementById('draft-save-status').textContent==='Draft saved'")
            field=page.locator(line_input).first
            assert field.input_value()=='12'
            field.fill('14')
            assert page.locator('#draft-save-status').inner_text()=='Unsaved changes'
            assert page.evaluate("()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}")
            page.evaluate('''()=>{const real=window.fetch;window.fetch=(url,opts)=>{
                if(opts&&opts.method==='POST'&&String(url).includes('/line/')) {window.fetch=real;return Promise.resolve(new Response(JSON.stringify({ok:false,error:'QA validation failure'}),{headers:{'Content-Type':'application/json'}}));}
                return real(url,opts);
            };}''')
            field.press('Tab')
            page.wait_for_function("()=>document.getElementById('draft-save-status').dataset.state==='error'")
            assert field.input_value()=='14'
            assert page.locator(final).is_disabled()
            field.fill('15');field.press('Tab')
            page.wait_for_function("()=>document.getElementById('draft-save-status').textContent==='Draft saved'")
            assert page.locator(line_input).first.input_value()=='15'
            page.reload()
            assert page.locator(line_input).first.input_value()=='15'
            page.set_viewport_size({'width':390,'height':844})
            assert page.locator('.ts-bar').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
            page.set_viewport_size({'width':1440,'height':950})
            assert not page.evaluate("()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}")
            page.evaluate('''()=>{const real=window.fetch;window.fetch=(url,opts)=>{
                if(opts&&opts.method==='POST')return Promise.reject(new Error('QA disconnected'));
                return real(url,opts);
            };}''')
            page.locator(line_input).first.fill('17');page.locator(line_input).first.press('Tab')
            page.wait_for_function("()=>document.getElementById('draft-save-status').textContent.includes('Reload')")
            assert page.locator(line_input).first.input_value()=='17'
            assert page.locator(final).is_disabled()
            assert page.evaluate("()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}")
            page.once('dialog',lambda dialog:dialog.accept())
            page.reload()
            assert page.locator(line_input).first.input_value()=='15'
        assert not errors, errors
        browser.close()
