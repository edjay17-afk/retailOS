"""The Lark mirror, against a fake Lark Open API."""
import json

import httpx
import pytest
from sqlalchemy import func, select

from app import lark
from app.config import Config, set_config
from app.models import Employee, LarkLink


class FakeLark:
    def __init__(self):
        self.tables = {}
        self.records = {}
        self.fields = {}
        self.calls = []
        self.messages = []
        self.n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, body = request.url.path, (json.loads(request.content) if request.content else {})
        self.calls.append((request.method, path))
        if path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
        if path.endswith("/im/v1/messages"):
            self.messages.append(json.loads(body["content"])["text"])
            return httpx.Response(200, json={"code": 0, "data": {}})
        if path.endswith("/tables") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"name": n, "table_id": i} for n, i in self.tables.items()], "has_more": False}})
        if path.endswith("/tables") and request.method == "POST":
            tid = f"tbl{len(self.tables)}"
            self.tables[body["table"]["name"]] = tid
            self.records[tid] = {}
            self.fields[tid] = [f["field_name"] for f in body["table"]["fields"]]
            return httpx.Response(200, json={"code": 0, "data": {"table_id": tid}})
        if path.endswith("/fields") and request.method == "GET":
            tid = path.split("/tables/")[1].split("/")[0]
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"field_name": n, "type": 1} for n in self.fields[tid]], "has_more": False}})
        if path.endswith("batch_create"):
            tid = path.split("/tables/")[1].split("/")[0]
            out = []
            for r in body["records"]:
                self.n += 1
                rid = f"rec{self.n}"
                self.records[tid][rid] = r["fields"]
                out.append({"record_id": rid})
            return httpx.Response(200, json={"code": 0, "data": {"records": out}})
        if path.endswith("batch_update"):
            tid = path.split("/tables/")[1].split("/")[0]
            for r in body["records"]:
                self.records[tid][r["record_id"]] = r["fields"]
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(404, json={"code": 1, "msg": "nope"})


@pytest.fixture()
def fl(cfg):
    c = Config(data_dir=cfg.data_dir, lark_app_id="id", lark_app_secret="sec", lark_base_token="base", base_url="http://shop:8000",
               scheduler_enabled=False)
    set_config(c)
    f = FakeLark()
    return f, lark.LarkClient(c, transport=httpx.MockTransport(f.handler), sleep=lambda s: None)


def test_disabled_without_credentials(cfg):
    blank = Config(data_dir=cfg.data_dir, lark_app_id="", lark_app_secret="", lark_base_token="", scheduler_enabled=False)  # never the real .env
    assert lark.mirror(lark.LarkClient(blank)) == {"enabled": False}


def test_setup_creates_tables_once(fl):
    f, client = fl
    ids = lark.setup(client)
    assert set(f.tables) == {s.table for s in lark.SPECS} and len(ids) == len(lark.SPECS)
    n = len(f.tables)
    lark.setup(client)
    assert len(f.tables) == n  # existing tables are reused


def _staff(session, name="Ann Lee"):
    e = Employee(storehub_id="e1", first_name=name.split()[0], last_name=name.split()[1], email="a@x.com")
    session.add(e); session.commit()
    return e


def test_mirror_is_idempotent_and_sends_only_changes(fl, session):
    f, client = fl
    lark.setup(client)
    e = _staff(session)
    out = lark.mirror(client)
    assert out["created"] == 1 and out["errors"] == 0
    tid = f.tables["员工名录 Staff Directory"]
    assert list(f.records[tid].values())[0]["Name 姓名"] == "Ann Lee"
    again = lark.mirror(client)
    assert again["created"] == 0 and again["updated"] == 0  # nothing changed, nothing sent
    e.email = "new@x.com"; session.commit()
    assert lark.mirror(client)["updated"] == 1
    assert len(f.records[tid]) == 1 and list(f.records[tid].values())[0]["Email Address  电子邮件"] == "new@x.com"
    assert session.scalar(select(func.count()).select_from(LarkLink)) == 1


def test_a_lark_failure_does_not_create_phantom_links(fl, session):
    f, client = fl
    lark.setup(client)
    _staff(session)
    orig = f.handler
    f.handler = lambda req: httpx.Response(200, json={"code": 1254, "msg": "bad field"}) if req.url.path.endswith("batch_create") else orig(req)
    client.http = httpx.Client(base_url=lark.HOST, transport=httpx.MockTransport(f.handler))
    out = lark.mirror(client)
    assert out["errors"] >= 1 and session.scalar(select(func.count()).select_from(LarkLink)) == 0


def test_alerts_carry_a_deep_link(fl, session):
    from app.services import notifications
    f, client = fl
    notifications.notify(session, title="Inventory data is stale", body="last success 90 min ago", level="critical",
                         link="/sync", dedupe_key="t1")
    notifications.notify(session, title="No link on this one", level="warning", dedupe_key="t2")
    session.commit()
    out = lark.forward_alerts(client, chat_id="oc_1")
    assert out == {"enabled": True, "sent": 2, "errors": 0}
    assert f.messages[0].endswith("\nhttp://shop:8000/sync")
    assert "http" not in f.messages[1]  # no link, no URL


def test_po_total_row_carries_only_the_total_and_is_worked_out_when_storehub_says_zero(session):
    from datetime import datetime
    from app.models import PoLine, PurchaseOrder
    po = PurchaseOrder(sh_po_id=16, status="completed", total=0.0, tax=0.0, discount=0.0, created_sh=datetime(2026, 10, 1))
    po.lines = [PoLine(product_sh_id="p1", ordered_qty=24, supplier_price=34.5), PoLine(product_sh_id="p2", ordered_qty=10, supplier_price=2.0)]
    ok = PurchaseOrder(sh_po_id=17, status="completed", total=100.5, created_sh=datetime(2026, 10, 1))
    ok.lines = [PoLine(product_sh_id="p3", ordered_qty=1, supplier_price=99.0)]
    session.add_all([po, ok]); session.commit()
    rows = lark.rows_purchase_orders(session)
    keys = [k for k, _ in rows]
    assert keys[2].startswith("total:") and keys[-1].startswith("total:")  # a closing row right after each PO's lines
    totals = [f for k, f in rows if k.startswith("total:")]
    assert totals[0] == {"PO # 采购订单号": "", "Supplier 供应商": "", "Deliver to 配送至": "", "Product 产品": "", "Total Amount 总金额": "848.00"}  # 24*34.5 + 10*2, not 0
    assert totals[1]["Total Amount 总金额"] == "100.50"  # StoreHub's own total wins when it has one
    assert all(f["Total Amount 总金额"] == "" for k, f in rows if not k.startswith("total:"))  # never repeated on product rows


def test_daily_inspection_photos_go_to_the_matching_columns(session, cfg):
    from datetime import date, datetime
    from app.models import Inspection, InspectionResult, InspectionTemplate, Location
    loc = Location(storehub_id="S_INS", name="The Daily Centro - Tabunoc", type="store", monitored=True)
    session.add(loc); session.flush()
    tpl = session.scalar(select(InspectionTemplate).where(InspectionTemplate.code == "store_daily"))
    by = {i.label.split(" ")[0]: i for i in tpl.items}
    insp = Inspection(template_id=tpl.id, location_id=loc.id, business_date=date(2026, 10, 10), due_at=datetime(2026, 10, 10, 4, 0), status="completed")
    insp.results = [InspectionResult(item_id=by["Store"].id, passed=True, photo_paths=["a/entrance1.jpg", "a/entrance2.jpg"]),
                    InspectionResult(item_id=by["Restroom"].id, passed=True, photo_paths=["a/rest.jpg"]),
                    InspectionResult(item_id=by["Video"].id, photo_path="a/walk.mp4")]
    pending = Inspection(template_id=tpl.id, location_id=loc.id, business_date=date(2026, 10, 9), due_at=datetime(2026, 10, 9, 4, 0), status="pending")
    session.add_all([insp, pending]); session.commit()
    rows = lark.rows_daily_inspection(session)
    assert len(rows) == 1  # only completed inspections are sent
    f = rows[0][1]
    assert f["Store 店铺"] == "The Daily Centro Tabunoc" and f["Date 日期"] == lark._ms(date(2026, 10, 10))
    assert list(f["Store Entrance"]) == ["a/entrance1.jpg", "a/entrance2.jpg"] and list(f["Restroom"]) == ["a/rest.jpg"] and list(f["Video Walkthrough"]) == ["a/walk.mp4"]
    assert "Counter Area" not in f  # no photo, no cell


def test_columns_renamed_in_lark_are_still_found():
    actual = ["Store 店铺", "Date 日期", "Store Entrance 商店入口", "Counter Area 柜台区", "Floors & Tables 地板与餐桌", "Product  产品", "Created 创建人"]
    kinds = {"Created 创建人": 1003}

    def handler(request):
        return httpx.Response(200, json={"code": 0, "data": {"items": [{"field_name": n, "type": kinds.get(n, 17)} for n in actual], "has_more": False}})
    c = Config(lark_app_id="id", lark_app_secret="sec", lark_base_token="base", scheduler_enabled=False)
    client = lark.LarkClient(c, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200}) if "tenant_access" in r.url.path else handler(r)),
                             sleep=lambda s: None)
    m = client.field_map("tbl", {"Store 店铺", "Date 日期", "Store Entrance", "Counter Area", "Floors & Tables", "Product Name 产品名称", "Machines", "Created 创建人"})
    assert m["Store 店铺"] == "Store 店铺" and m["Store Entrance"] == "Store Entrance 商店入口" and m["Floors & Tables"] == "Floors & Tables 地板与餐桌"
    assert m["Product Name 产品名称"] == "Product  产品"  # a renamed column is still matched by its English words
    assert "Machines" not in m and "Created 创建人" not in m  # no such column / never write a computed one


def test_one_bad_record_does_not_block_the_rest_of_its_batch(fl, session):
    from app.models import Employee
    f, client = fl
    lark.setup(client)
    for n in range(3):
        session.add(Employee(storehub_id=f"e{n}", first_name="Ann", last_name=f"L{n}"))
    session.commit()
    orig = f.handler

    def handler(req):  # Lark refuses any create that carries the bad record, as a dropdown would
        if req.url.path.endswith("batch_create") and any("L1" in json.dumps(r) for r in json.loads(req.content)["records"]):
            return httpx.Response(200, json={"code": 1254062, "msg": "SingleSelectFieldConvFail"})
        return orig(req)
    f.handler = handler
    client.http = httpx.Client(base_url=lark.HOST, transport=httpx.MockTransport(f.handler))
    out = lark.mirror(client)
    assert out["created"] == 2  # the two good records went through, one by one, after the batch was refused


def test_a_row_deleted_in_lark_is_written_again_instead_of_failing_every_run(fl, session):
    f, client = fl
    lark.setup(client)
    session.add(Employee(storehub_id="e1", first_name="Ann", last_name="Lee"))
    session.commit()
    assert lark.mirror(client)["created"] == 1
    tid = next(iter(f.records))
    staff_tid = f.tables["员工名录 Staff Directory"]
    f.records[staff_tid].clear()  # somebody deleted the row in Lark
    emp = session.scalar(select(Employee))
    emp.first_name = "Anne"
    session.commit()
    orig = f.handler

    def handler(req):
        if req.url.path.endswith("batch_update"):
            body = json.loads(req.content)
            if any(r["record_id"] not in f.records[staff_tid] for r in body["records"]):
                return httpx.Response(200, json={"code": 1254043, "msg": "record not found"})
        return orig(req)
    f.handler = handler
    client.http = httpx.Client(base_url=lark.HOST, transport=httpx.MockTransport(f.handler))
    out = lark.mirror(client)
    assert out["errors"] == 0 and len(f.records[staff_tid]) == 1 and "Anne" in json.dumps(list(f.records[staff_tid].values()))
