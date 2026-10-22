import pytest
from sqlalchemy import select
from app.models import CycleCount, Location, Product
from app.services import counts
from tests.test_web import client, add_user, login, csrf_of

@pytest.fixture
def scan_setup(client,session,monkeypatch):
    loc=Location(storehub_id="SCAN_STORE",name="Scan store",type="store",monitored=True)
    prod=Product(storehub_id="SCAN_PRODUCT",name="Scan product",barcode="123456789",in_api=True)
    session.add_all([loc,prod]);session.commit()
    add_user(session,"scanner_admin","admin");login(client,"scanner_admin")
    csrf=csrf_of(client,"/counts")
    r=client.post("/counts/create",data={"location_id":loc.id,"mode":"scan","csrf":csrf})
    url=r.headers["location"]
    monkeypatch.setattr(counts,"live_quantity",lambda loc,prod:12.0)
    return url,csrf,prod.id,loc.id

def test_scan_save_rescan_and_submit(client,session,scan_setup):
    url,csrf,pid,_=scan_setup
    cid=int(url.rsplit("/",1)[1]);assert len(session.get(CycleCount,cid).lines)==0
    r=client.post(url+"/scan",data={"code":"123456789","csrf":csrf})
    assert r.json()["system"]==12
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":0,"note":"Empty shelf","csrf":csrf}).status_code==200
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":10,"note":"Two missing","csrf":csrf}).status_code==200
    session.expire_all();cc=session.get(CycleCount,cid)
    assert len(cc.lines)==1 and cc.lines[0].variance==-2
    lid=cc.lines[0].id
    r=client.post(url+"/save",data={"csrf":csrf,"submit":"1",f"c_{lid}":10,f"n_{lid}":"Two missing"})
    assert r.status_code==303
    session.expire_all();assert session.get(CycleCount,cid).status=="submitted"
    assert client.post(url+"/scan",data={"code":"123456789","csrf":csrf}).status_code==409

@pytest.mark.parametrize("qty",["-1","nan","inf","100000001"])
def test_invalid_count(client,scan_setup,qty):
    url,csrf,pid,_=scan_setup
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":qty,"csrf":csrf}).status_code==400

def test_failure_csrf_and_scope(client,session,scan_setup,monkeypatch):
    url,csrf,pid,loc=scan_setup
    assert client.post(url+"/scan",data={"code":"123456789","csrf":"bad"}).status_code in (400,403)
    assert client.post(url+"/scan",data={"code":"unknown","csrf":csrf}).status_code==404
    def fail(*args):raise counts.CountError("Unavailable")
    monkeypatch.setattr(counts,"live_quantity",fail)
    assert client.post(url+"/scan",data={"code":"123456789","csrf":csrf}).status_code==503
    assert client.post(url+"/scan-save",data={"product_id":pid,"counted":10,"csrf":csrf}).status_code==400
    session.expire_all();assert len(session.get(CycleCount,int(url.rsplit("/",1)[1])).lines)==0
    client.post("/logout",data={"csrf":csrf})
    add_user(session,"other_scanner","staff",[999999]);login(client,"other_scanner")
    assert client.post(url+"/scan",data={"code":"123456789","csrf":csrf_of(client,"/counts")}).status_code==403
