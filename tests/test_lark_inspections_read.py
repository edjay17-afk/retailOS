from datetime import date, datetime, timezone
from app.services.lark_inspections import parse_records, store_key
from tests.test_web import client, add_user, login
from app.models import Location

def test_store_and_philippine_date_match():
    stamp=int(datetime(2026,10,18,16,30,tzinfo=timezone.utc).timestamp()*1000)
    rows=[{"record_id":"rec1","fields":{"Store Store":"The Daily Centro Bantayan","Date Date":stamp,"Photos":[{"file_token":"file"}]}}]
    reports=parse_records(rows,date(2026,10,19))
    assert len(reports)==1 and reports[0]["time"]=="00:30" and reports[0]["attachments"]==1
    assert store_key("The Daily Centro - Bantayan")==reports[0]["store"]
    assert not parse_records(rows,date(2026,10,18))
    assert not parse_records([{"fields":{"Store":"Bantayan","Date":"bad"}}],date(2026,10,19))

def test_endpoint_scopes_reports(client,session,monkeypatch):
    from app.services import lark_inspections
    a=Location(name="The Daily Centro - Bantayan",storehub_id="LARK_A",type="store",monitored=True)
    b=Location(name="The Daily Centro - Tabunoc",storehub_id="LARK_B",type="store",monitored=True)
    session.add_all([a,b]);session.commit()
    add_user(session,"lark_reader","staff",[a.id]);login(client,"lark_reader")
    monkeypatch.setattr(lark_inspections,"reports_for_day",lambda day:{"available":True,"reports":[{"store":"bantayan","id":"a"},{"store":"tabunoc","id":"b"}],"checked_at":"10:00"})
    r=client.get('/inspections/lark-reports?on=2026-10-19')
    assert r.status_code==200
    assert set(r.json()["reports"])=={str(a.id)}
    assert client.get('/inspections/lark-reports?loc='+str(b.id)).status_code==403
    def fail(day):raise RuntimeError('private error')
    monkeypatch.setattr(lark_inspections,"reports_for_day",fail)
    r=client.get('/inspections/lark-reports').json()
    assert not r['available'] and 'private' not in r['message']


def test_lark_submission_persists_only_matching_daily_task(client, session, sync, monkeypatch):
    from sqlalchemy import select
    from app.models import Inspection, InspectionResult, AuditEvent
    from app.services import inspections, lark_inspections
    from app.timeutil import today_local
    from tests.test_web import csrf_of
    sync.sync_stores()
    day = today_local()
    inspections.generate_tasks(session, day)
    session.commit()
    daily = list(session.scalars(select(Inspection).join(Inspection.template).where(Inspection.template.has(code='store_daily'))))
    target = daily[0]
    target.status = 'missed'
    session.commit()
    add_user(session, 'lark_submitter', 'staff', [target.location_id])
    login(client, 'lark_submitter')
    report = {'store': lark_inspections.store_key(target.location.name), 'id': 'report-proof', 'attachments': 1, 'time': '10:00', 'url': 'https://example.com/report'}
    monkeypatch.setattr(lark_inspections, 'reports_for_day', lambda day: {'available': True, 'reports': [report], 'checked_at': '10:00'})
    token = csrf_of(client, '/inspections')
    assert client.post('/inspections/lark-reports').status_code == 403
    assert client.get('/inspections/lark-reports').status_code == 200
    session.refresh(target)
    assert target.status == 'missed'  # read-only callers do not submit anything
    response = client.post('/inspections/lark-reports', data={'csrf':token})
    assert response.status_code == 200 and target.id in response.json()['submitted']
    session.refresh(target)
    assert target.status == 'submitted' and target.completed_at is None and target.completed_by is None
    assert not list(session.scalars(select(InspectionResult).where(InspectionResult.inspection_id == target.id)))
    assert all(i.status != 'submitted' for i in daily[1:])
    assert 'Submitted via Lark' in client.get(f'/inspections/{target.id}').text
    assert 'results not reviewed' in client.get('/inspections').text
    client.post('/inspections/lark-reports', data={'csrf':token})
    logs=list(session.scalars(select(AuditEvent).where(AuditEvent.action=='inspection.lark_submitted')))
    assert len(logs)==1
    monkeypatch.setattr(lark_inspections, 'reports_for_day', lambda day: {'available':False,'reports':[]})
    client.post('/inspections/lark-reports', data={'csrf':token})
    session.refresh(target)
    assert target.status=='submitted'
    target.status='completed';session.commit()
    assert lark_inspections.mark_submitted(session,day,{str(target.location_id):[report]},None)==[]
    assert target.status=='completed'


def test_zenwok_excluded_from_inspections(client, session, sync):
    from sqlalchemy import select
    from app.models import Inspection
    from app.services import inspections
    from app.timeutil import today_local
    sync.sync_stores()
    warehouse=session.scalar(select(Location).where(Location.type=='warehouse'))
    warehouse.name='Zenwok Warehouse'
    session.commit()
    inspections.generate_tasks(session,today_local())
    session.commit()
    assert not list(session.scalars(select(Inspection).where(Inspection.location_id==warehouse.id)))
    add_user(session,'inspection_locations','admin');login(client,'inspection_locations')
    page=client.get('/inspections')
    assert page.status_code==200 and 'Zenwok Warehouse' not in page.text
    assert client.get('/').status_code in (200,303,307)
