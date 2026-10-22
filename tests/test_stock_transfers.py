"""Real StoreHub export layout: preview/confirm, atomic validation, scope, replay and canonical-stock isolation."""
import csv
import hashlib
import io
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import audit
from app.models import InventoryCurrent, OrderBatch, StockTransfer, StockTransferImport, StockTransferLine
from app.services import stock_transfers as svc
from app.timeutil import now_utc
from tests.test_web import add_user, client, csrf_of, login, seeded

HEADERS = ['S.T ID','Created Date','Shipped Date','Received Date','Source Store','Target Store','No.','Product Name','SKU',
           'Serial No.','Category','Ordered Qty','Unit','Cost (RM)','SubTotal (RM)','Total (RM)','Status','Sent By','Cancelled By','Cancelled Date','Received By']


def export(reference='ST0050', status='Created', qty='10', target='The Daily Centro - Alpha', source='TheDailyCentro Warehouse', product='Cola 500ml', extra=None):
    base = {'S.T ID':reference,'Created Date':'09/28/2026 09:57','Source Store':source,'Target Store':target,'Status':status}
    if status in ('Shipped','Completed'):
        base['Shipped Date'] = '09/28/2026 11:00'
    if status == 'Completed':
        base['Received Date'] = '09/28/2026 13:00'
    if extra: base.update(extra)
    rows = [{**base,'Total (RM)':'120.00'}, {**base,'No.':'1','Product Name':product,'Ordered Qty':qty,'Cost (RM)':'12.00','SubTotal (RM)':'120.00'}]
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out,fieldnames=HEADERS)
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode('utf-8-sig')


def import_source(session, user, raw):
    from secrets import token_hex
    src = StockTransferImport(token=token_hex(16), filename='Stock_Transfer.csv',sha256=hashlib.sha256(raw).hexdigest(),raw_csv=raw,created_by=user.id)
    session.add(src); session.flush()
    return src


def test_export_header_and_item_rows_dates_currency_and_unicode():
    groups = svc.parse(export(product='可乐 Cola'))
    assert len(groups)==1 and len(groups[0]['lines'])==1
    assert groups[0]['created_sh'].hour == 1  # merchant 09:57 is UTC 01:57
    assert groups[0]['currency']=='PHP' and groups[0]['lines'][0]['quantity']==10  # RM export label is normalized to PHP
    assert groups[0]['lines'][0]['product_name']=='可乐 Cola'


@pytest.mark.parametrize('qty',['NaN','inf','-2','no'])
def test_invalid_quantities_are_rejected(qty):
    with pytest.raises(svc.ImportError): svc.parse(export(qty=qty))


def test_import_is_idempotent_updates_and_never_changes_stock(sync,fake,session):
    seeded(sync,fake,session)
    u=add_user(session,'wh_import','warehouse')
    ids=list(session.scalars(select(InventoryCurrent.location_id).distinct()))
    before=list(session.execute(select(InventoryCurrent.location_id,InventoryCurrent.product_id,InventoryCurrent.on_hand)))
    s=import_source(session,u,export())
    assert svc.apply(session,s,u,ids)['new']==1
    session.commit()
    assert svc.apply(session,s,u,ids)['new']==1  # repeated confirmation is a no-op
    s2=import_source(session,u,export())
    assert svc.apply(session,s2,u,ids)['unchanged']==1
    session.commit()
    s3=import_source(session,u,export(status='Completed',qty='8'))
    assert svc.apply(session,s3,u,ids)['updated']==1
    session.commit()
    assert session.scalar(select(func.count()).select_from(StockTransfer))==1
    assert session.scalar(select(func.count()).select_from(StockTransferLine))==1
    transfer=session.scalar(select(StockTransfer))
    assert transfer.status=='Completed' and transfer.lines[0].quantity==8
    s4=import_source(session,u,export())
    assert svc.apply(session,s4,u,ids)['skipped']==1
    session.commit()
    assert session.scalar(select(StockTransfer)).status=='Completed'
    assert list(session.execute(select(InventoryCurrent.location_id,InventoryCurrent.product_id,InventoryCurrent.on_hand)))==before
    assert session.scalar(select(func.count()).select_from(OrderBatch))==0
    assert audit.verify_chain(session)[0]


def test_validation_is_atomic_and_scope_is_checked(sync,fake,session):
    seeded(sync,fake,session)
    u=add_user(session,'wh_scope','warehouse',locs=[1])
    raw=export()+export(reference='ST0051',source='Unknown',target='Unknown target').split(b'\r\n',1)[1]
    source=import_source(session,u,raw)
    with pytest.raises(svc.ImportError,match='assigned locations'): svc.apply(session,source,u,[1])
    assert session.scalar(select(func.count()).select_from(StockTransfer))==0
    source.created_at=now_utc()-timedelta(minutes=31)
    with pytest.raises(svc.ImportError,match='expired'): svc.apply(session,source,u,[1])


def test_unknown_product_and_target_are_retained_without_new_master_data(sync,fake,session):
    from app.models import Location,Product
    seeded(sync,fake,session)
    before=session.scalar(select(func.count()).select_from(Product))
    groups,counts=svc.plan(session,export(target='Old store 688',product='Historical item'),[1])
    assert counts['unmapped_locations']==1 and counts['unmapped_products']==1
    assert groups[0]['target_name']=='Old store 688' and groups[0]['lines'][0]['product_id'] is None
    assert session.scalar(select(func.count()).select_from(Product))==before
    assert session.scalar(select(Location).where(Location.name=='Old store 688')) is None


def test_conflicting_duplicate_lines_and_headers_are_rejected():
    raw=export()
    repeated=raw+export(qty='11').split(b'\r\n',2)[2]
    with pytest.raises(svc.ImportError,match='conflicting item'): svc.parse(repeated)
    with pytest.raises(svc.ImportError,match='dates are out of order'):
        svc.parse(export(status='Completed',extra={'Received Date':'09/27/2026 13:00'}))


def test_web_preview_confirmation_permissions_and_chinese(sync,fake,session,client):
    seeded(sync,fake,session)
    add_user(session,'adm_transfers','admin')
    add_user(session,'staff_transfers','staff',locs=[2])
    login(client,'adm_transfers')
    csrf=csrf_of(client,'/stock-transfers')
    raw=export(product='<script>alert(1)</script>')
    r=client.post('/stock-transfers/import/preview',data={'csrf':csrf},files={'file':('StoreHub.csv',raw,'text/csv')})
    assert r.status_code==200 and 'Preview stock-transfer import' in r.text
    assert session.scalar(select(func.count()).select_from(StockTransfer))==0
    src=session.scalar(select(StockTransferImport))
    assert src.raw_csv==raw
    r=client.post(f'/stock-transfers/import/{src.token}',data={'csrf':csrf})
    assert r.status_code==303 and 'err=' not in r.headers['location']
    detail=client.get('/stock-transfers/1').text
    assert '&lt;script&gt;' in detail and '<script>alert(1)</script>' not in detail
    client.cookies.set('lang','zh')
    chinese_list=client.get('/stock-transfers').text
    assert '库存调拨' in chinese_list and '已创建' in chinese_list and '应用筛选' in chinese_list
    assert '门店已确认' in client.get('/stock-transfers/1').text  # progress strip on the reworked sheet
    login(client,'staff_transfers')
    assert client.get('/stock-transfers').status_code==200  # V2.1: store staff see transfers for their own store
    assert client.get('/stock-transfers/1').status_code==200  # ST0050 targets their store
    assert client.post('/stock-transfers/import/preview',data={'csrf':csrf},files={'file':('x.csv',raw)}).status_code==403


def test_web_scope_tokens_csrf_and_search(sync,fake,session,client):
    seeded(sync,fake,session)
    add_user(session,'adm_t','admin')
    add_user(session,'second_t','admin')
    add_user(session,'store_beta_t','staff',locs=[3])
    login(client,'adm_t')
    csrf=csrf_of(client,'/stock-transfers')
    assert client.post('/stock-transfers/import/preview',files={'file':('x.csv',export())}).status_code==403
    assert 'err=' in client.post('/stock-transfers/import/preview',data={'csrf':csrf},files={'file':('x.csv',b'bad')}).headers['location']
    client.post('/stock-transfers/import/preview',data={'csrf':csrf},files={'file':('x.csv',export())})
    src=session.scalar(select(StockTransferImport))
    login(client,'second_t')
    csrf2=csrf_of(client,'/stock-transfers')
    assert client.post(f'/stock-transfers/import/{src.token}',data={'csrf':csrf2}).status_code==404
    login(client,'adm_t')
    csrf=csrf_of(client,'/stock-transfers')
    result=client.post(f'/stock-transfers/import/{src.token}',data={'csrf':csrf})
    assert result.status_code==303 and 'err=' not in result.headers['location']
    assert 'ST0050' in client.get('/stock-transfers?q=Cola').text
    assert 'ST0050' not in client.get('/stock-transfers?status=Shipped').text
    login(client,'store_beta_t')
    assert client.get('/stock-transfers').status_code==200  # list is scoped to their own store
    assert 'ST0050' not in client.get('/stock-transfers').text  # ST0050 involves Alpha, not Beta
    assert client.get('/stock-transfers/1').status_code==404  # not visible outside their locations
    assert client.get('/stock-transfers?loc=2').status_code==403


def test_store_rename_in_storehub_updates_mirror_instead_of_blocking(sync,fake,session):
    seeded(sync,fake,session)
    u=add_user(session,'wh_rename','warehouse')
    ids=list(session.scalars(select(InventoryCurrent.location_id).distinct()))
    s=import_source(session,u,export())
    assert svc.apply(session,s,u,ids)['new']==1
    session.commit()
    renamed=import_source(session,u,export(source='Zenwok Warehouse'))
    counts=svc.apply(session,renamed,u,ids)
    assert counts['updated']==1 and counts['renamed']==1
    session.commit()
    transfer=session.scalar(select(StockTransfer))
    assert transfer.source_name=='Zenwok Warehouse'


def test_stock_transfer_permissions_split_warehouse_from_store():
    from app.security import PERMS
    # warehouse-only: import, dispatch and the StoreHub-side receive
    for perm in ("transfer.import", "transfer.dispatch", "transfer.receive"):
        assert PERMS[perm] == {"warehouse", "admin"}, perm
    # stores see their own transfers so they can count what arrived (V2.1 store receipt confirmation)
    assert PERMS["transfer.view"] == {"staff", "store_manager", "warehouse", "management", "admin"}
    # the store confirms receipt; the warehouse never closes its own loop
    assert PERMS["transfer.confirm"] == {"staff", "store_manager", "management", "admin"}


def test_inbox_folder_imports_exports_automatically_and_survives_bad_files(sync, fake, session, cfg):
    from app.models import Notification
    from app.services import transfer_inbox
    seeded(sync, fake, session)
    add_user(session, "inbox_admin", "admin")
    inbox, done, failed = transfer_inbox.folders()
    (inbox / "ST_export.csv").write_bytes(export())
    out = transfer_inbox.process_inbox(session)
    assert out["imported"] == 1 and out["failed"] == 0 and not list(inbox.glob("*.csv")) and list(done.glob("*ST_export.csv"))
    assert session.scalar(select(func.count()).select_from(StockTransfer)) == 1
    # the same file again changes nothing
    (inbox / "again.csv").write_bytes(export())
    again = transfer_inbox.process_inbox(session)
    assert again["duplicate"] == 1 and again["imported"] == 0 and session.scalar(select(func.count()).select_from(StockTransfer)) == 1
    # a file that is not a StoreHub export goes to failed/ with a reason, and the warehouse team and admins are told
    (inbox / "junk.csv").write_bytes(b"a,b\n1,2\n")
    bad = transfer_inbox.process_inbox(session)
    assert bad["failed"] == 1 and list(failed.glob("*junk.csv")) and list(failed.glob("*junk.csv.txt"))
    assert session.scalar(select(func.count()).select_from(Notification).where(Notification.title.like("%could not be imported%"))) >= 1
    # freshness: fine now, stale after the limit, and the alert is sent once a day
    assert transfer_inbox.check_freshness(session) is False
    src = session.scalar(select(StockTransferImport).where(StockTransferImport.imported_at.is_not(None)))
    src.imported_at = now_utc() - timedelta(hours=40)
    session.commit()
    assert transfer_inbox.check_freshness(session) is True
    assert transfer_inbox.check_freshness(session) is True
    assert session.scalar(select(func.count()).select_from(Notification).where(Notification.title.like("%not been updated%"), Notification.role == "admin")) == 1


def test_robot_exports_and_imports_and_warns_when_it_fails(sync, fake, session, cfg, monkeypatch):
    from app.config import Config, set_config
    from app.models import Notification
    from app import robot
    from app.services import transfer_inbox
    seeded(sync, fake, session)
    add_user(session, "robot_admin", "admin")
    set_config(Config(data_dir=cfg.data_dir, storehub_bo_email="x@example.com", storehub_bo_password="pw", scheduler_enabled=False, secret_key="k" * 32))
    inbox, done, failed = transfer_inbox.folders()

    def fake_export():
        p = inbox / "StockTransfers-test.csv"
        p.write_bytes(export(reference="ST0099"))
        return p
    out = transfer_inbox.run_robot(session, export=fake_export)
    assert out["ok"] is True and out["imported"] == 1 and session.scalar(select(StockTransfer).where(StockTransfer.reference == "ST0099")) is not None

    def broken_export():
        raise robot.RobotLoginFailed("StoreHub did not accept the sign-in. Check the email and password in .env")
    bad = transfer_inbox.run_robot(session, export=broken_export)
    assert bad["ok"] is False and "sign-in" in bad["error"]
    assert session.scalar(select(func.count()).select_from(Notification).where(Notification.title.like("%robot could not export%"))) == 2  # warehouse + admin
    transfer_inbox.run_robot(session, export=broken_export)
    assert session.scalar(select(func.count()).select_from(Notification).where(Notification.title.like("%robot could not export%"))) == 2  # once a day
    # no login configured: the robot stays idle
    set_config(Config(data_dir=cfg.data_dir, storehub_bo_email="", storehub_bo_password="", scheduler_enabled=False, secret_key="k" * 32))
    assert transfer_inbox.run_robot(session, export=broken_export) == {"ran": False}


def test_robot_runs_every_twenty_minutes_and_catches_up_after_sleep(sync, fake, session, cfg):
    from app.services import transfer_inbox
    seeded(sync, fake, session)
    add_user(session, "robot_admin2", "admin")
    assert transfer_inbox.robot_due(session) is True  # nothing imported yet
    inbox, done, failed = transfer_inbox.folders()
    (inbox / "a.csv").write_bytes(export())
    transfer_inbox.process_inbox(session)
    assert transfer_inbox.robot_due(session) is False  # imported just now: wait
    src = session.scalar(select(StockTransferImport).where(StockTransferImport.imported_at.is_not(None)))
    src.imported_at = now_utc() - timedelta(minutes=19)
    session.commit()
    assert transfer_inbox.robot_due(session) is False
    src.imported_at = now_utc() - timedelta(minutes=21)
    session.commit()
    assert transfer_inbox.robot_due(session) is True  # twenty minutes passed (or the PC was asleep)
    transfer_inbox.mark_robot_attempt(session)
    assert transfer_inbox.robot_due(session) is False  # it tried a moment ago: no hammering if StoreHub is down
    src.imported_at = now_utc() - timedelta(hours=14)  # the PC was off overnight
    session.commit()
    assert transfer_inbox.robot_due(session) is False  # still inside the 20-minute retry gap
    from app.models import Setting
    session.get(Setting, "robot.last_attempt").value = (now_utc() - timedelta(minutes=21)).isoformat()
    session.commit()
    assert transfer_inbox.robot_due(session) is True


def _import_transfer(client, session, raw, admin):
    login(client, admin)
    csrf = csrf_of(client, '/stock-transfers')
    assert client.post('/stock-transfers/import/preview', data={'csrf': csrf}, files={'file': ('x.csv', raw, 'text/csv')}).status_code == 200
    src = session.scalar(select(StockTransferImport).order_by(StockTransferImport.id.desc()))
    r = client.post(f'/stock-transfers/import/{src.token}', data={'csrf': csrf})
    assert r.status_code == 303 and 'err=' not in r.headers['location']
    session.expire_all()
    return session.scalar(select(StockTransfer).order_by(StockTransfer.id.desc()))


def test_store_receipt_confirmation_exact_and_variance(sync, fake, session, client):
    """V2.1 Gate 3: the store's own count closes the transfer loop; a difference opens an issue for the warehouse."""
    from app.models import InventoryEvent, Issue, Notification, StockTransferReceipt
    seeded(sync, fake, session)
    add_user(session, 'adm_rc', 'admin')
    add_user(session, 'alpha_staff_rc', 'staff', locs=[2])
    add_user(session, 'wh_rc', 'warehouse')
    tr = _import_transfer(client, session, export(reference='ST0060', status='Shipped', qty='10'), 'adm_rc')
    line = tr.lines[0]
    # the receiving store counts exactly what was sent: receipt stored, trace event, no issue
    login(client, 'alpha_staff_rc')
    detail = client.get(f'/stock-transfers/{tr.id}')
    assert detail.status_code == 200 and 'Store receipt confirmation' in detail.text
    csrf = csrf_of(client, f'/stock-transfers/{tr.id}')
    r = client.post(f'/stock-transfers/{tr.id}/confirm', data={'csrf': csrf, f'q_{line.id}': '10', 'note': 'all complete'})
    assert r.status_code == 303 and 'err=' not in r.headers['location']
    session.expire_all()
    receipt = session.scalar(select(StockTransferReceipt).where(StockTransferReceipt.transfer_id == tr.id))
    assert receipt is not None and receipt.differences == 0 and receipt.location_id == 2
    kinds = dict(session.execute(select(InventoryEvent.event_type, func.count()).group_by(InventoryEvent.event_type)).all())
    assert kinds.get('store_receiving') == 1 and 'transfer_variance' not in kinds
    assert session.scalar(select(func.count()).select_from(Issue)) == 0
    detail = client.get(f'/stock-transfers/{tr.id}').text
    assert 'Store confirmed' in detail and 'Confirm receipt' not in detail
    r = client.post(f'/stock-transfers/{tr.id}/confirm', data={'csrf': csrf, f'q_{line.id}': '10'})
    assert 'err=' in r.headers['location']  # one confirmation per transfer
    # a short count: one issue for the warehouse, a variance event, a warehouse notification
    tr2 = _import_transfer(client, session, export(reference='ST0061', status='Shipped', qty='8'), 'adm_rc')
    line2 = tr2.lines[0]
    login(client, 'alpha_staff_rc')
    csrf = csrf_of(client, f'/stock-transfers/{tr2.id}')  # fresh session after re-login
    r = client.post(f'/stock-transfers/{tr2.id}/confirm', data={'csrf': csrf, f'q_{line2.id}': '5'})
    assert 'err=' not in r.headers['location']
    session.expire_all()
    receipt2 = session.scalar(select(StockTransferReceipt).where(StockTransferReceipt.transfer_id == tr2.id))
    assert receipt2.differences == 1
    issue = session.scalar(select(Issue).where(Issue.dedupe_key == f'st:{tr2.id}'))
    assert issue is not None and issue.severity == 'P2' and issue.location_id == 1  # owned by the warehouse side
    assert session.scalar(select(func.count()).select_from(InventoryEvent).where(InventoryEvent.event_type == 'transfer_variance')) == 1
    assert session.scalar(select(func.count()).select_from(Notification).where(Notification.role == 'warehouse')) == 1
    assert 'Differences' in client.get('/stock-transfers').text


def test_store_receipt_scope_and_guards(sync, fake, session, client):
    from app.models import StockTransferReceipt
    seeded(sync, fake, session)
    add_user(session, 'adm_rg', 'admin')
    add_user(session, 'beta_staff_rg', 'staff', locs=[3])  # Beta — not the transfer target
    add_user(session, 'wh_rg', 'warehouse')
    tr = _import_transfer(client, session, export(reference='ST0062', status='Shipped', qty='6'), 'adm_rg')
    line = tr.lines[0]
    # the warehouse never closes its own loop
    login(client, 'wh_rg')
    assert client.get(f'/stock-transfers/{tr.id}').status_code == 200
    assert client.post(f'/stock-transfers/{tr.id}/confirm', data={'csrf': csrf_of(client, f'/stock-transfers/{tr.id}'), f'q_{line.id}': '6'}).status_code == 403
    # a store that is not the target cannot see or confirm it
    login(client, 'beta_staff_rg')
    assert client.get(f'/stock-transfers/{tr.id}').status_code == 404
    assert client.post(f'/stock-transfers/{tr.id}/confirm', data={'csrf': csrf_of(client), f'q_{line.id}': '6'}).status_code == 404
    # missing counts are rejected; nothing is recorded
    login(client, 'adm_rg')
    r = client.post(f'/stock-transfers/{tr.id}/confirm', data={'csrf': csrf_of(client, f'/stock-transfers/{tr.id}')})
    assert 'err=' in r.headers['location']
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(StockTransferReceipt)) == 0


def test_filtered_csv_export_all_pages_and_scope(sync, fake, session, client):
    seeded(sync, fake, session)
    admin = add_user(session, 'csv_admin', 'admin')
    add_user(session, 'csv_beta', 'staff', locs=[3])
    ids = list(session.scalars(select(InventoryCurrent.location_id).distinct()))
    for i in range(52):
        src = import_source(session, admin, export(reference=f'STCSV{i:04}',
                            target='The Daily Centro - Alpha' if i < 51 else 'The Daily Centro - Beta',
                            status='Created' if i < 51 else 'Completed'))
        svc.apply(session, src, admin, ids)
    session.commit()
    login(client, 'csv_admin')
    def download(params):
        response = client.get('/stock-transfers', params={**params, 'export': 'csv'})
        assert response.status_code == 200
        assert 'attachment' in response.headers['content-disposition']
        assert response.content.startswith(b'\xef\xbb\xbf')
        return list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    rows = download({'q': 'Cola', 'status': 'Created', 'loc': 2, 'page': 2})
    assert len(rows) == 51
    assert all(r['To'] == 'The Daily Centro - Alpha' and r['Export total (PHP)'] == '120.00' for r in rows)
    assert rows[0]['Created (Philippine time)'] == '2026-09-28 09:57'
    assert len(download({'q': 'STCSV0051', 'status': 'Completed', 'loc': 3})) == 1
    assert download({'q': 'not-found'}) == []
    assert client.get('/stock-transfers?export=csv&status=bad').status_code == 400
    login(client, 'csv_beta')
    rows = download({})
    assert len(rows) == 1 and rows[0]['Transfer ID'] == 'STCSV0051'
    assert client.get('/stock-transfers?export=csv&loc=2').status_code == 403


def test_import_modal_responsive(sync, fake, session, client):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    seeded(sync, fake, session)
    add_user(session, 'modal_admin', 'admin')
    login(client, 'modal_admin')
    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        response = client.get(url.path + ('?' + url.query if url.query else ''))
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items() if k.lower() not in
                               ('content-length', 'content-encoding', 'transfer-encoding')})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page()
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda r: r.abort())
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            page.goto('http://qa.test/stock-transfers')
            assert not page.locator('#transfer-import').is_visible()
            page.locator('[data-modal="transfer-import"]').click()
            assert page.locator('#transfer-import').is_visible()
            box = page.locator('#transfer-import').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width
            assert page.locator('#transfer-file').is_visible()
            styles = page.locator('#transfer-import').evaluate("el => ({padding:parseFloat(getComputedStyle(el).paddingLeft),animation:getComputedStyle(el).animationName,backdrop:getComputedStyle(el,'::backdrop').animationName})")
            assert styles['padding'] >= 18
            assert styles['animation'] == 'transfer-import-open' and styles['backdrop'] == 'none'

            assert page.evaluate("() => document.querySelector('[name=export]').form.id") == 'stock-transfer-filters'
            page.locator('#transfer-import [data-modal-close]').click()
            assert not page.locator('#transfer-import').is_visible()
        browser.close()
