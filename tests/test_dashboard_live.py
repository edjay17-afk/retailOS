from tests.test_web import client, add_user, login, seeded
from app.models import Location, SalesDay
from app.timeutil import today_local
from sqlalchemy import select


def test_live_sales_scope_and_updates(sync,fake,session,client):
    seeded(sync,fake,session)
    store=session.scalar(select(Location).where(Location.type=='store'))
    add_user(session,'live_manager','store_manager',[store.id]);login(client,'live_manager')
    r=client.get('/dashboard/store-sales')
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    assert [s['id'] for s in r.json()['stores']]==[store.id]
    row=session.scalar(select(SalesDay).where(SalesDay.location_id==store.id,SalesDay.business_date==today_local()))
    if row is None:
        row=SalesDay(location_id=store.id,business_date=today_local(),net_sales=0)
        session.add(row)
    row.net_sales=1234.56;session.commit()
    assert client.get('/dashboard/store-sales').json()['today']==1234.56
    add_user(session,'no_sales','staff',[store.id]);login(client,'no_sales')
    assert client.get('/dashboard/store-sales').status_code==403


def test_dashboard_updates_without_navigation(sync,fake,session,client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    seeded(sync,fake,session)
    add_user(session,'live_admin','admin');login(client,'live_admin')
    calls=[]
    def serve(route):
        u=urlsplit(route.request.url)
        if u.path=='/dashboard/store-sales':
            calls.append(1)
            r=client.get(u.path);data=r.json()
            data['today']=1234.56
            for store in data['stores']:store['today']=1234.56
            route.fulfill(json=data);return
        r=client.get(u.path)
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        page.clock.install()
        page.goto('http://qa.test/')
        page.clock.fast_forward(31000)
        page.wait_for_function("()=>document.querySelector('#live-sales-today .rn-sr').textContent==='1,234.56'")
        assert calls and page.url=='http://qa.test/'
        assert page.locator('.store-today .rn-sr').first.text_content()=='1,234.56'
        browser.close()


def test_dashboard_phone_layout_is_ready_before_javascript(sync, fake, session, client):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    seeded(sync, fake, session)
    add_user(session, 'first_paint_admin', 'admin'); login(client, 'first_paint_admin')

    def serve(route):
        url = urlsplit(route.request.url)
        response = client.get(url.path)
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844}, java_script_enabled=False)
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda route: route.abort())
        page.goto('http://qa.test/')
        assert 'has-mtabs' in page.locator('body').get_attribute('class')
        assert page.locator('head link[href*="dashboard_lv.css"]').count() == 1
        assert page.locator('.phone-home-actions').is_visible()
        assert not page.locator('.lv-hero > .lv-t').first.is_visible()
        assert page.locator('.lv-hero').evaluate('(el) => getComputedStyle(el).display') == 'block'
        page.set_viewport_size({'width': 1440, 'height': 900})
        assert not page.locator('.phone-home-actions').is_visible()
        assert page.locator('.lv-hero > .lv-t').first.is_visible()
        assert page.locator('.lv-hero').evaluate('(el) => getComputedStyle(el).display') == 'grid'
        browser.close()


def test_new_sales_come_back_after_the_last_one_the_page_saw(sync,fake,session,client):
    from datetime import datetime
    from app.models import Payment, SalesLine, SalesTx
    seeded(sync,fake,session)
    store=session.scalar(select(Location).where(Location.type=='store'))
    add_user(session,'toast_manager','store_manager',[store.id]);login(client,'toast_manager')
    first=client.get('/dashboard/store-sales').json()
    assert first['new_sales']==[]                                   # the first read only learns where things stand
    now=datetime.utcnow()
    def tx(ref,total,**kw):
        return SalesTx(refid=ref,location_id=store.id,tx_type=kw.get('tx_type','Sale'),tx_time=now,business_date=today_local(),total=total,cancelled=kw.get('cancelled',False),raw_hash='x')
    one,two=tx('t-new-1',120.05),tx('t-new-2',80.5)
    session.add_all([one,two,tx('t-cancelled',999,cancelled=True),tx('t-return',50,tx_type='Return')]);session.flush()
    session.add_all([SalesLine(tx_id=one.id,line_no=1,qty=2,total=100),SalesLine(tx_id=one.id,line_no=2,qty=1,total=20.05),Payment(tx_id=one.id,method='GCASH',amount=120.05),
                     Payment(tx_id=two.id,method='Cash',amount=50),Payment(tx_id=two.id,method='GCash',amount=30.5)]);session.commit()
    got=client.get('/dashboard/store-sales?after=%d'%first['latest_tx']).json()
    assert [round(x['total'],2) for x in got['new_sales']]==[120.05,80.5]
    assert (got['new_sales'][0]['items'],got['new_sales'][0]['pay'])==(3,'GCash')   # items are units; GCASH is shown as GCash
    assert got['new_sales'][1]['pay']=='Split'                                       # paid with more than one method
    assert got['latest_tx']>first['latest_tx']
    assert client.get('/dashboard/store-sales?after=%d'%got['latest_tx']).json()['new_sales']==[]


def _notice_page(client, session, sync, fake, user, role, sales, can_open=True):
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    seeded(sync, fake, session)
    add_user(session, user, role); login(client, user)
    state = {'n': 0, 'opened': []}

    def serve(route):
        u = urlsplit(route.request.url)
        if u.path == '/dashboard/store-sales':
            state['n'] += 1
            data = client.get(u.path).json()
            if state['n'] > 1:
                data['new_sales'] = sales; data['can_open'] = can_open
            route.fulfill(json=data); return
        if u.path.startswith('/sales/'):
            state['opened'].append(u.path)
        r = client.get(u.path)
        route.fulfill(status=r.status_code, body=r.content, headers={k: v for k, v in r.headers.items() if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})
    return sync_playwright, serve, state


def test_a_sale_notice_appears_at_the_top_centre(sync,fake,session,client):
    sales=[{'id':9,'store':'Getafe','total':120.05,'items':3,'pay':'Cash'}]
    sync_playwright,serve,state=_notice_page(client,session,sync,fake,'toast_admin','admin',sales)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':900});page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        page.clock.install()
        page.goto('http://qa.test/')
        page.wait_for_function("()=>window.SaleToast&&document.querySelector('#live-sales-today .rn-sr')")
        assert page.locator('.sale-toast').count()==0                # nothing for what was already there
        page.clock.fast_forward(31000)
        page.wait_for_selector('.sale-toast')
        text=page.locator('.sale-toast').first.text_content()
        assert 'Getafe' in text and '120.05' in text and 'Cash' in text and '3 items' in text
        assert page.locator('.sale-toast-av').first.text_content()=='G'
        box=page.locator('.sale-toasts').bounding_box()
        middle=page.evaluate('document.documentElement.getBoundingClientRect().width/2')   # the middle of what is visible, a scrollbar excluded
        assert abs((box['x']+box['width']/2)-middle)<2 and box['y']<40  # top centre
        browser.close()


def test_clicking_a_sale_notice_opens_that_transaction(sync,fake,session,client):
    sales=[{'id':9,'store':'Getafe','total':120.05,'items':3,'pay':'Cash'}]
    sync_playwright,serve,state=_notice_page(client,session,sync,fake,'toast_click','admin',sales)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':900});page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        page.clock.install()
        page.goto('http://qa.test/')
        page.wait_for_function("()=>window.SaleToast&&document.querySelector('#live-sales-today .rn-sr')")
        page.clock.fast_forward(31000)
        page.wait_for_selector('.sale-toast.is-link')
        page.locator('.sale-toast').first.click()
        page.wait_for_url('**/sales/tx/9')
        assert '/sales/tx/9' in state['opened']
        browser.close()


def test_a_sale_notice_leaves_by_itself_and_the_close_button_works(sync,fake,session,client):
    sales=[{'id':9,'store':'Getafe','total':120.05,'items':3,'pay':'Cash'},{'id':10,'store':'Bantayan','total':80.5,'items':1,'pay':'GCash'}]
    sync_playwright,serve,state=_notice_page(client,session,sync,fake,'toast_leave','admin',sales)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':900});page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        page.clock.install()
        page.goto('http://qa.test/')
        page.wait_for_function("()=>window.SaleToast&&document.querySelector('#live-sales-today .rn-sr')")
        page.clock.fast_forward(31000)
        page.wait_for_function("()=>document.querySelectorAll('.sale-toast').length===2")
        page.locator('.sale-toast').first.hover()
        page.locator('.sale-toast-x').first.click()                    # the close button leaves without opening the sale
        page.clock.run_for(100)
        assert page.locator('.sale-toast.out').count()==1
        page.clock.run_for(600)
        assert page.locator('.sale-toast').count()==1 and page.url=='http://qa.test/'
        page.mouse.move(5,500)                                         # pointer away: the other one leaves after its short wait
        page.clock.run_for(3200)
        page.wait_for_function("()=>document.querySelectorAll('.sale-toast').length===0")
        browser.close()


def test_a_sale_notice_is_not_a_link_without_permission_to_open_sales(sync,fake,session,client):
    sales=[{'id':9,'store':'Getafe','total':120.05,'items':3,'pay':'Cash'}]
    sync_playwright,serve,state=_notice_page(client,session,sync,fake,'toast_nolink','admin',sales,can_open=False)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':900});page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        page.clock.install()
        page.goto('http://qa.test/')
        page.wait_for_function("()=>window.SaleToast&&document.querySelector('#live-sales-today .rn-sr')")
        page.clock.fast_forward(31000)
        page.wait_for_selector('.sale-toast')
        assert page.locator('.sale-toast.is-link').count()==0
        page.locator('.sale-toast').first.click()
        assert page.url=='http://qa.test/'
        browser.close()


def test_the_top_bar_lines_up_with_the_page_content(sync,fake,session,client):
    """The business switch starts where the page content starts, however wide the screen is."""
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    seeded(sync,fake,session)
    add_user(session,'align_admin','admin');login(client,'align_admin')
    def serve(route):
        u=urlsplit(route.request.url)
        r=client.get(u.path)
        route.fulfill(status=r.status_code,body=r.content,headers={k:v for k,v in r.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1900,'height':900});page.route('http://qa.test/**',serve);page.route('https://**',lambda r:r.abort())
        page.goto('http://qa.test/')
        for width in (1900,1500,1300,1100):
            page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(100)
            got=page.evaluate("""()=>{const m=document.querySelector('.main').getBoundingClientRect(),c=document.querySelector('.content'),t=document.querySelector('.topbar');
              const cs=getComputedStyle(c),ts=getComputedStyle(t),r=c.getBoundingClientRect();
              return [r.left-m.left+parseFloat(cs.paddingLeft),parseFloat(ts.paddingLeft),m.right-r.right+parseFloat(cs.paddingRight),parseFloat(ts.paddingRight)]}""")
            assert abs(got[0]-got[1])<1.5 and abs(got[2]-got[3])<1.5,(width,got)
        browser.close()
