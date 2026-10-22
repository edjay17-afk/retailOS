"""QA regression checks use temporary databases and mocked StoreHub only."""
import html
import re
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from sqlalchemy import select, func
from app import settings_service
from app.ai import context, service
from app.models import Location, Product, Recommendation, ReplRun, SalesDayProduct, SyncState
from app.services import brief, replenishment
from app.timeutil import today_local, now_utc
from tests.test_services import seeded
from tests.test_web import client, add_user, login


def setup_user(session):
    add_user(session, 'qa_admin', 'admin')


@pytest.mark.parametrize('path', ['/sales', '/sales/products', '/sales/product/1', '/sales/transactions', '/sales/employees'])
@pytest.mark.parametrize('start,end', [('bad', 'bad'), ('2026-02-10', '2026-02-01'), ('2099-01-01', '2099-01-02')])
def test_bad_report_ranges_are_errors(client, session, sync, fake, path, start, end):
    seeded(sync, fake, session)
    setup_user(session); login(client, 'qa_admin')
    response = client.get(path + '?' + urlencode({'days': 'custom', 'frm': start, 'to': end}))
    assert response.status_code == 400
    assert 'Invalid date range' in response.text


def test_product_detail_keeps_one_day_and_location(client, session, sync, fake):
    seeded(sync, fake, session)
    setup_user(session); login(client, 'qa_admin')
    a = session.scalar(select(Location).where(Location.storehub_id == 'S_A'))
    b = session.scalar(select(Location).where(Location.storehub_id == 'S_B'))
    p = session.scalar(select(Product).where(Product.storehub_id == 'P1'))
    for loc, net in [(a, 123.45), (b, 876.54)]:
        row = session.get(SalesDayProduct, (loc.id, today_local(), p.id))
        if row is None:
            session.add(SalesDayProduct(location_id=loc.id, product_id=p.id, business_date=today_local(), units=1, net_sales=net))
        else:
            row.net_sales = net
    session.commit()
    listing = client.get(f'/sales/products?loc={a.id}&days=1')
    link = html.unescape(re.search(r'href="(/sales/product/'+str(p.id)+r'\?[^"]+)"', listing.text).group(1))
    assert 'days=1&loc=' in link
    detail = client.get(link)
    assert detail.status_code == 200 and '123.45' in detail.text and '876.54' not in detail.text
    assert f'name="loc" value="{a.id}"' in detail.text
    day = today_local().isoformat()
    custom = client.get(f'/sales/product/{p.id}?loc={a.id}&days=custom&frm={day}&to={day}')
    assert custom.status_code == 200 and '123.45' in custom.text and '876.54' not in custom.text
    add_user(session, 'qa_store', 'store_manager', [a.id]); login(client, 'qa_store')
    assert client.get(f'/sales/product/{p.id}?loc={b.id}&days=1').status_code == 403


def test_opening_dashboard_generates_suggestions_without_sync(client, session, sync, fake):
    seeded(sync, fake, session)
    settings_service.set_(session, 'repl.mode', 'sales_only'); session.commit()
    setup_user(session); login(client, 'qa_admin')
    assert session.scalar(select(func.count()).select_from(ReplRun)) == 0
    calls = len(fake.calls)
    r = client.get('/')
    assert r.status_code == 200
    assert session.scalar(select(func.count()).select_from(Recommendation)) > 0
    assert 'No open suggestions.' not in r.text
    assert len(fake.calls) == calls
    count = session.scalar(select(func.count()).select_from(ReplRun))
    assert client.get('/replenishment').status_code == 200
    assert session.scalar(select(func.count()).select_from(ReplRun)) == count
    rec = session.scalar(select(Recommendation).where(Recommendation.status == 'pending'))
    if rec:
        rec.saved_qty = 42; session.commit()
        latest = session.scalar(select(ReplRun).order_by(ReplRun.id.desc()))
        latest.run_at = now_utc() - timedelta(minutes=6); session.commit()
        assert client.get('/replenishment').status_code == 200
        session.refresh(rec); assert rec.saved_qty == 42


def test_auto_generation_respects_stale_data(client, session):
    setup_user(session); login(client, 'qa_admin')
    assert client.get('/replenishment').status_code == 200
    row = session.scalar(select(ReplRun))
    assert row.status == 'blocked'
    assert session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_sales_only_brief_and_ai_facts(session, sync, fake):
    seeded(sync, fake, session)
    settings_service.set_(session, 'repl.mode', 'sales_only'); session.commit()
    replenishment.run(session); session.commit()
    ids = list(session.scalars(select(Location.id)))
    result = brief.build(session, ids)
    item = next(i for i in result['items'] if i['link'] == '/replenishment')
    assert 'cover days - incoming' in item['calculations'][0]
    assert not any('less stock than their lead time' in f for f in item['facts'])
    ctx = context.build(session, None, ids)
    assert ctx.meta['replenishment_policy']['mode'] == 'sales_only'
    assert any('Days of stock cover is unavailable, not zero' in f.text for f in ctx.facts)
    assert not any('0.0 days of cover' in f.text for f in ctx.facts)


def test_https_gate_and_session_flags(client, session, cfg):
    setup_user(session)
    cfg.require_https = True; cfg.base_url = 'https://retail.example.test'
    plain = client.get('/login', headers={'host': 'untrusted.example', 'x-forwarded-proto': 'https'})
    assert plain.status_code == 307 and plain.headers['location'] == cfg.base_url + '/login'
    assert client.post('/login', data={}).status_code == 400
    from tests.test_web import PW
    secure = client.post('https://retail.example.test/login', data={'username': 'qa_admin', 'password': PW})
    assert secure.status_code == 303
    cookie = secure.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=lax' in cookie
    assert secure.headers['strict-transport-security'] == 'max-age=31536000'


def test_stock_change_text_clean(client, session):
    setup_user(session); login(client, 'qa_admin')
    page = client.get('/stock-events')
    assert page.status_code == 200
    assert 'recorded here: who, why' in page.text
    assert '\u00e2\u20ac' not in page.text and '\u00c3\u00a2' not in page.text


def test_staff_replenishment_is_scoped_and_sync_is_denied(client, session, sync, fake):
    seeded(sync, fake, session)
    a = session.scalar(select(Location).where(Location.storehub_id == 'S_A'))
    b = session.scalar(select(Location).where(Location.storehub_id == 'S_B'))
    add_user(session, 'qa_staff', 'staff', [a.id])
    login(client, 'qa_staff')
    assert client.get('/').status_code == 200
    assert client.get('/replenishment').status_code == 200
    assert client.get(f'/replenishment?loc={b.id}').status_code == 403
    assert client.get('/sync').status_code == 403
    assert client.get('/sales').status_code == 403


def test_brief_from_old_policy_is_not_current(session, sync, fake, monkeypatch):
    from tests.test_ai import FakeModel, ready
    boss = ready(sync, fake, session)
    monkeypatch.setattr(service, 'get_provider', lambda *a, **k: FakeModel({'headline': 'QA example', 'items': []}))
    row = service.generate_brief(session, boss); session.commit()
    assert service.latest_brief(session, boss).id == row.id
    settings_service.set_(session, 'repl.mode', 'sales_only'); session.commit()
    assert service.latest_brief(session, boss) is None


def test_range_limit_and_single_day(client, session, sync, fake):
    seeded(sync, fake, session)
    setup_user(session); login(client, 'qa_admin')
    today = today_local()
    for endpoint in ['/sales', '/sales/products', '/sales/product/1', '/sales/transactions', '/sales/employees']:
        assert client.get(endpoint + '?' + urlencode({'days': 'custom', 'frm': str(today - timedelta(days=401)), 'to': str(today)})).status_code == 400
        assert client.get(endpoint + '?' + urlencode({'days': 'custom', 'frm': str(today), 'to': str(today)})).status_code == 200
