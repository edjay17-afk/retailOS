"""Chart tooltips (every value is readable on hover) and the floating chat window's JSON endpoint."""
import json

from app.ai import service
from app.web import charts
from tests.test_ai import FakeModel, ai, ready  # noqa: F401
from tests.test_web import add_user, csrf_of, login, client  # noqa: F401


def test_every_bar_point_and_slice_carries_its_value():
    b = str(charts.bars([("01 Oct", 1200.0), ("02 Oct", 3400.5)], fmt=lambda v: f"P{v:,.2f}"))
    assert 'data-tip="01 Oct: P1,200.00"' in b and 'data-tip="02 Oct: P3,400.50"' in b
    a = str(charts.area([("Mon", 100.0), ("Tue", 150.0), ("Wed", 90.0)]))
    assert a.count("data-tip=") == 3 and "+50% vs previous" in a and "-40% vs previous" in a
    s = str(charts.sparkline([1, 5, 3], labels=["a", "b", "c"], fmt=lambda v: f"{v:g}"))
    assert 'data-tip="b: 5"' in s
    d = str(charts.donut([("Cash", 75, "#000"), ("Other", 25, "#111")], fmt=lambda v: f"{v:g}"))
    assert "Cash: 75 (75%)" in d and "Other: 25 (25%)" in d
    assert "3 of 4 done (75%)" in str(charts.ring(3, 4))
    assert "units" in str(charts.hbars([("Coke", 500.0, "40 units")]))


def test_empty_and_single_point_charts_do_not_crash():
    assert str(charts.bars([])) == "" and str(charts.area([("a", 1.0)])) == "" and str(charts.donut([("x", 0, "#000")])) == ""
    assert "svg" in str(charts.bars([("a", 0.0)]))  # all zero is drawn, not divided by zero


def test_chat_widget_and_tooltip_script_are_on_every_page(client, session):
    add_user(session, "boss", "management")
    login(client, "boss")
    html = client.get("/").text
    assert 'id="chat-fab"' in html and 'id="tip"' in html and "/static/app.js" in html
    assert client.get("/static/app.js").status_code == 200


def test_staff_with_ai_permission_gets_the_chat(client, session):
    add_user(session, "cashier", "staff")
    login(client, "cashier")
    r = client.get("/", follow_redirects=True)
    assert 'id="chat-fab"' in r.text
    assert client.post("/assistant/chat", data={"question": "hi", "csrf": "x"}).status_code == 403  # bad CSRF, not bad role


def test_chat_endpoint_answers_with_checked_statements(client, session, ai, sync, fake):
    boss = ready(sync, fake, session)
    from app.ai import context as ctxmod
    fm = ai({"answer": [{"type": "fact", "text": "Nothing is cited here.", "refs": []}], "suggested_actions": [], "follow_ups": ["And the warehouse?"]})
    from tests.test_web import PW
    from app.security import hash_password
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    tok = csrf_of(client, "/assistant")
    r = client.post("/assistant/chat", data={"question": "What first?", "csrf": tok, "history": json.dumps([{"role": "user", "text": "earlier"}])})
    d = r.json()
    assert d["ok"] and "Nothing is cited here." in d["html"] and "And the warehouse?" in d["html"] and "supporting details" in d["html"]
    assert "EARLIER IN THIS CHAT" in fm.calls[0][1][0]["content"]  # the previous turns are passed on as context only
    assert "unverified" in d["html"].lower() or "statements verified" in d["html"]  # the fact-checker result is shown


def test_chat_endpoint_rejects_missing_csrf_and_reports_model_errors(client, session, ai, sync, fake):
    boss = ready(sync, fake, session)
    from tests.test_web import PW
    from app.security import hash_password
    from app.ai.provider import LlmError
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    assert client.post("/assistant/chat", data={"question": "x", "csrf": "bad"}).status_code == 403
    ai(LlmError("quota"))
    tok = csrf_of(client, "/assistant")
    d = client.post("/assistant/chat", data={"question": "x", "csrf": tok}).json()
    assert d["ok"] is False and "could not" in d["error"].lower()


def test_sales_by_product_page_and_csv(client, session, sync, fake):
    from tests.test_ai import ready
    from app.security import hash_password
    from tests.test_web import PW
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    r = client.get("/sales/products?days=60")
    assert r.status_code == 200 and "Sales by product" in r.text
    csv_r = client.get("/sales/products?days=60&export=csv")
    assert csv_r.status_code == 200 and csv_r.text.lstrip("﻿").startswith("Product,Barcode,Category,Units sold,Net sales")
    assert client.get("/sales/products?days=60&q=zzzznothing").status_code == 200


def test_inventory_shows_every_open_po_as_incoming_and_filters(client, session, sync, fake):
    from tests.test_ai import ready
    from app.models import PurchaseOrder, PoLine, Location
    from app.security import hash_password
    from tests.test_web import PW
    from app.services import purchasing
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    open_pos = purchasing.open_po_detail(session)
    # whatever the seeded data holds, detail covers every open PO line, and overdue quantity never exceeds the total
    for d in open_pos.values():
        assert d["qty"] >= d["late_qty"] >= 0 and d["pos"]
    login(client, "boss")
    loc = session.query(Location).first().id
    for inc in ("", "yes", "ontime", "late", "none"):
        r = client.get(f"/inventory?loc={loc}&inc={inc}")
        assert r.status_code == 200 and "Incoming (open POs)" in r.text
    csv_r = client.get(f"/inventory?loc={loc}&export=csv")
    assert "Incoming (open POs),Of which overdue,Open PO numbers" in csv_r.text


def test_supplier_names_can_be_imported_from_csv(client, session, sync, fake):
    from tests.test_ai import ready
    from app.models import Supplier
    from app.security import hash_password
    from tests.test_web import PW
    boss = ready(sync, fake, session)
    boss.role = "admin"; boss.password_hash = hash_password(PW); session.commit()
    sup = Supplier(storehub_id="6a4f48c4bfbdec00077817fc", name="Supplier 7817fc", name_source="auto", active=True)
    session.add(sup); session.commit()
    code = sup.storehub_id[-6:]
    login(client, "boss")
    tok = csrf_of(client, "/suppliers")
    assert "Fill in the real supplier names" in client.get("/suppliers").text
    assert "Real supplier name" in client.get("/suppliers?export=csv").text
    r = client.post("/suppliers/import", data={"csrf": tok}, files={"file": ("n.csv", f"code,name\n{code},Mega Distributors Inc.\nzzzzzz,Nobody\n".encode(), "text/csv")})
    assert r.status_code in (302, 303)
    session.expire_all()
    s2 = session.get(Supplier, sup.id)
    assert s2.name == "Mega Distributors Inc." and s2.name_source == "manual"


def test_supplier_names_from_the_storehub_po_export(session):
    from datetime import datetime
    from app.models import PurchaseOrder, Supplier
    from app.services import supplier_names
    a = Supplier(storehub_id="aaaaaaaaaaaaaaaaaaaaa1", name="Supplier aaaaa1", name_source="auto", active=True)
    b = Supplier(storehub_id="bbbbbbbbbbbbbbbbbbbbb2", name="Typed By Hand", name_source="manual", active=True)
    c = Supplier(storehub_id="ccccccccccccccccccccc3", name="Supplier ccccc3", name_source="auto", active=True)
    session.add_all([a, b, c]); session.flush()
    for no, sup in ((150, a), (151, a), (7, b), (8, c)):
        session.add(PurchaseOrder(sh_po_id=no, supplier_id=sup.id, status="completed", subtotal=0, tax=0, discount=0, total=0, missing_from_api=False,
                                  raw_hash="x", first_seen=datetime(2026, 1, 1), last_seen=datetime(2026, 1, 1)))
    session.commit()
    text = '﻿"P.O ID","Supplier","Product Name"\n"PO0150","Ubest Main Warehouse",""\n"PO0150","Ubest Main Warehouse","x"\n"PO0151","Ubest Main Warehouse",""\n"PO0007","Other Name",""\n'
    r = supplier_names.apply_po_export(session, text)
    session.commit()
    assert r["suppliers_named"] == 1 and r["kept_manual"] == 1 and r["still_unnamed"] == 1  # c has no PO in the file
    assert a.name == "Ubest Main Warehouse" and a.name_source == "export"
    assert b.name == "Typed By Hand"  # a name a person typed is never overwritten
    import pytest
    with pytest.raises(ValueError):
        supplier_names.parse_po_export("a,b\n1,2\n")


def test_sales_of_one_product_page(client, session, sync, fake):
    from tests.test_ai import ready
    from app.models import Product
    from app.security import hash_password
    from tests.test_web import PW
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    pid = session.query(Product).first().id
    r = client.get(f"/sales/product/{pid}?days=30")
    assert r.status_code == 200 and "By store" in r.text and "Day by day" in r.text
    assert client.get("/sales/product/999999").status_code == 404
    nav = client.get("/sales/products").text
    assert 'href="/sales/products"' in nav and "Sales by product" in nav


def test_sales_by_product_custom_period(client, session, sync, fake):
    from tests.test_ai import ready
    from app.security import hash_password
    from tests.test_web import PW
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    r = client.get("/sales/products?days=custom&frm=2026-09-20&to=2026-09-30")
    assert r.status_code == 200 and "Period:" in r.text and "2026-09-20" in r.text and "2026-09-30" in r.text
    # an invalid or reversed range falls back to the default preset instead of erroring
    assert client.get("/sales/products?days=custom&frm=2026-10-01&to=2026-09-01").status_code == 200
    assert client.get("/sales/products?days=custom&frm=&to=").status_code == 200
    r2 = client.get("/sales/products?days=custom&frm=2026-09-20&to=2026-09-30&export=csv")
    assert r2.status_code == 200 and "sales-by-product-2026-09-20-to-2026-09-30.csv" in r2.headers["content-disposition"]
    # presets still work
    assert client.get("/sales/products?days=7").status_code == 200


def test_explain_answers_inline_when_called_from_the_drawer(client, session, sync, fake, ai):
    from sqlalchemy import select
    from app.models import Recommendation
    from app.security import hash_password
    from tests.test_web import PW
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    rec = session.scalar(select(Recommendation))
    tok = csrf_of(client, "/replenishment?view=list")
    ai({"answer": [{"type": "calculation", "text": "Because daily sales drive it", "refs": ["F1"]}], "suggested_actions": [], "follow_ups": []})
    r = client.post(f"/replenishment/{rec.id}/explain", data={"csrf": tok}, headers={"X-Requested-With": "fetch"})
    d = r.json()
    assert d["ok"] and "Because daily sales drive it" in d["html"]
    # a plain browser submit still lands on the full evidence page
    ai({"answer": [{"type": "fact", "text": "still works", "refs": ["F1"]}], "suggested_actions": [], "follow_ups": []})
    r2 = client.post(f"/replenishment/{rec.id}/explain", data={"csrf": tok})
    assert r2.status_code in (302, 303) and "/assistant/output/" in r2.headers["location"]


def test_citations_show_the_fact_they_point_to(client, session, sync, fake, ai):
    from app.security import hash_password
    from tests.test_web import PW
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    ai({"answer": [{"type": "fact", "text": "Sales data is fresh", "refs": ["F1"]}], "suggested_actions": [], "follow_ups": []})
    tok = csrf_of(client, "/assistant")
    d = client.post("/assistant/chat", data={"question": "is the data fresh?", "csrf": tok},
                    headers={"X-Requested-With": "fetch"}).json()
    assert d["ok"] and 'data-tip="[' in d["html"]  # the F1 chip carries the fact's section and text
    from sqlalchemy import select
    from app.models import AiOutput
    row = session.scalar(select(AiOutput).order_by(AiOutput.id.desc()))
    f1 = next(f for f in row.context_json["facts"] if f["id"] == "F1")
    assert f1["section"] in d["html"] and f1["text"][:30].split("\n")[0] in d["html"].replace("&quot;", '"')


def test_sales_and_performance_accept_a_custom_range(client, session, sync, fake):
    from app.security import hash_password
    from tests.test_web import PW
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    r = client.get("/sales?days=custom&frm=2026-09-20&to=2026-09-30")
    assert r.status_code == 200 and "2026-09-20" in r.text and "2026-09-30" in r.text
    assert client.get("/sales?days=custom&frm=2026-10-01&to=2026-09-01").status_code == 200  # reversed falls back to preset
    r2 = client.get("/performance?days=custom&frm=2026-09-01&to=2026-09-30")
    assert r2.status_code == 200 and "2026-09-01" in r2.text
    assert client.get("/performance?days=60").status_code == 200
