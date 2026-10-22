import json
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app.ai import service, tools, page_context, report_tools, policy
from app.models import Product, Supplier, Location, AiOutput, TransferSheet
from app.security import hash_password
from tests.test_ai import ai, ready
from tests.test_services import mkuser
from tests.test_web import client, login, csrf_of, PW


def signed_in(client, session, sync, fake):
    user = ready(sync, fake, session)
    user.password_hash = hash_password(PW)
    session.commit()
    login(client, "boss")
    return user, csrf_of(client, "/assistant")


def reply(text="Checked the available records."):
    return {"answer": [{"type": "message", "text": text, "refs": []}], "suggested_actions": []}


def test_filters_are_in_prompt_and_full_matching_totals(client, session, sync, fake, ai):
    user, csrf = signed_in(client, session, sync, fake)
    supplier = Supplier(name="Only supplier")
    session.add(supplier); session.flush()
    product = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    product.supplier_id = supplier.id; session.commit()
    page = "/sales/products?" + urlencode({"days": 60, "supplier": supplier.id, "q": product.name, "sort": "units"})
    model = ai(reply())
    response = client.post("/assistant/chat", data={"csrf": csrf, "question": "Explain these sales", "page_url": page}).json()
    assert response["ok"]
    prompt = model.calls[0][1][0]["content"]
    assert "Only supplier" in prompt and '"supplier":' in prompt and "Filtered product sales" in prompt
    assert response["page"]["args"]["supplier"] == str(supplier.id)
    data = report_tools.product_sales(session, user, {"days": 60, "supplier": supplier.id, "q": "no match"})
    assert "0 products; total units 0" in data["facts"][0]
    assert "export=" not in page_context.resolve(session, user, page + "&export=csv")["url"]


def test_owned_history_resumes_full_answers_and_isolated(client, session, sync, fake, ai):
    user, csrf = signed_in(client, session, sync, fake)
    long_answer = "Remember the selected store. " * 35
    model = ai(reply(long_answer), reply("Follow-up answer"))
    first = client.post("/assistant/chat", data={"csrf": csrf, "question": "Explain sales", "page_url": "/sales/products?days=60"}).json()
    second = client.post("/assistant/chat", data={"csrf": csrf, "question": "Which product leads?", "parent_id": first["id"]}).json()
    assert second["ok"] and second["page"]["base"] == "/sales/products"
    assert long_answer in model.calls[1][1][0]["content"]
    history = client.get(f"/assistant/conversations/{second['id']}").json()
    assert len(history["messages"]) == 2
    assert len(client.get("/assistant/conversations").json()["conversations"]) == 1
    other = mkuser(session, "other_admin", "admin"); other.password_hash = hash_password(PW); session.commit()
    login(client, "other_admin")
    assert client.get(f"/assistant/conversations/{first['id']}").status_code == 404
    other_csrf = csrf_of(client, "/assistant")
    assert client.post("/assistant/chat", data={"csrf": other_csrf, "question": "Continue", "parent_id": first["id"]}).status_code == 404
    assert client.get("/assistant/conversations").json()["conversations"] == []


def test_context_rejects_foreign_locations_and_external_urls(session, sync, fake):
    ready(sync, fake, session)
    a = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    b = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    user = mkuser(session, "scoped_ai", "store_manager", [a.id])
    with pytest.raises(tools.ToolError):
        page_context.resolve(session, user, f"/sales/products?loc={b.id}")
    with pytest.raises(tools.ToolError):
        report_tools.product_sales(session, user, {"location": b.id})
    assert page_context.resolve(session, user, "https://example.com/sales") == {}
    assert page_context.resolve(session, user, "//example.com/sales") == {}
    with pytest.raises(tools.ToolError):
        page_context.resolve(session, user, "/transfer-sheets/1")


def test_table_cells_are_checked_and_html_escaped(client, session, sync, fake, ai):
    _, csrf = signed_in(client, session, sync, fake)
    response = reply()
    response["table"] = {"columns": ["Product", "Sales"], "rows": [{"cells": ["<script>alert(1)</script>", "999999999"], "refs": ["F1"], "type": "fact"}]}
    ai(response)
    data = client.post("/assistant/chat", data={"csrf": csrf, "question": "Compare products"}).json()
    assert data["ok"] and "Unverified" in data["html"]
    assert "<script>alert" not in data["html"] and "&lt;script&gt;" in data["html"]
    row = session.get(AiOutput, data["id"])
    assert row.validation["unsupported"] > 0
    assert "999999999" in client.get(f"/assistant/output/{row.id}").text


def test_stream_has_real_progress_and_persists_answer(client, session, sync, fake, ai):
    _, csrf = signed_in(client, session, sync, fake)
    ai(reply("Streamed answer"))
    response = client.post("/assistant/chat-stream", data={"csrf": csrf, "question": "Explain sales", "page_url": "/sales/products?days=60"})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert any(e.get("message") == "Reading current store data" for e in events)
    assert any(e.get("message") == "Checking answer citations" for e in events)
    result = events[-1]
    assert result["type"] == "result" and result["ok"] and "Streamed answer" in result["html"]
    assert session.get(AiOutput, result["id"]).user_id
    assert client.post("/assistant/chat-stream", data={"csrf": "wrong", "question": "Hello"}).status_code == 403


def test_new_read_tool_permission_is_checked(session, sync, fake, ai, monkeypatch):
    ready(sync, fake, session)
    user = mkuser(session, "manager_tools", "store_manager")
    called = []
    monkeypatch.setattr(tools.TOOLS["get_transfer_sheets"], "run", lambda *a: called.append(True))
    ai({"tool_calls": [{"tool": "get_transfer_sheets", "args": {}}]}, reply())
    service.ask(session, user, "Show the transfer sheets")
    assert called == []


def test_transfer_proposal_requires_approval_and_does_not_create_early(session, sync, fake, ai):
    ready(sync, fake, session)
    user = mkuser(session, "ai_warehouse", "warehouse")
    store = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    product = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    args = {"location": store.id, "lines": [{"product_id": product.id, "qty": 3}]}
    gate = policy.check(session, user, "prepare_transfer_sheet", args)
    assert gate.decision != "execute"
    proposal = reply()
    proposal["suggested_actions"] = [{"tool": "prepare_transfer_sheet", "args": args, "why": "Requested draft", "confidence": .9}]
    ai(proposal)
    output = service.ask(session, user, "Prepare three of this product for the store")
    assert output.actions and session.scalar(select(TransferSheet)) is None


def test_lark_report_lookup_only_describes_accessible_stores(session, sync, fake, monkeypatch):
    from app.services import lark_inspections
    ready(sync, fake, session)
    store = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    user = mkuser(session, "inspect_ai", "store_manager", [store.id])
    monkeypatch.setattr(lark_inspections, "reports_for_day", lambda day: {"available": True, "reports": [
        {"store": lark_inspections.store_key(store.name), "time": "09:00", "attachments": 4},
        {"store": "private-store", "time": "10:00", "attachments": 900}]})
    facts = " ".join(report_tools.lark_reports(session, user, {})["facts"])
    assert "4 attachments" in facts and "900" not in facts and "does not mean" in facts


def test_read_tool_inherits_page_filters_and_explicit_date_overrides(session, sync, fake, ai, monkeypatch):
    user = ready(sync, fake, session)
    page = page_context.resolve(session, user, "/sales/products?days=60&category=Drinks&q=Cola")
    received = []
    monkeypatch.setattr(tools.TOOLS["get_filtered_product_sales"], "run", lambda db, user, args: (received.append(args) or {"facts": ["No matches"]}))
    ai({"tool_calls": [{"tool": "get_filtered_product_sales", "args": {}}]}, reply())
    service.ask(session, user, "Which product leads?", page=page)
    assert received[0]["category"] == "Drinks" and received[0]["q"] == "Cola" and received[0]["frm"] == page["args"]["frm"]
    ai({"tool_calls": [{"tool": "get_filtered_product_sales", "args": {"days": 7}}]}, reply())
    service.ask(session, user, "Show the last seven days instead", page=page)
    assert received[1]["days"] == 7 and "frm" not in received[1] and "to" not in received[1]


def test_stop_between_model_and_tool_does_not_run_tool(session, sync, fake, ai, monkeypatch):
    user = ready(sync, fake, session)
    called = []
    monkeypatch.setattr(tools.TOOLS["get_supplier_sales"], "run", lambda *args: called.append(True))
    ai({"tool_calls": [{"tool": "get_supplier_sales", "args": {}}]})
    def progress(message):
        if message.startswith("Looking up"):
            raise service.AiUnavailable("Stopped")
    with pytest.raises(service.AiUnavailable):
        service.ask(session, user, "Compare supplier sales", progress=progress)
    assert not called


def test_reports_for_counts_expiry_and_transfer_and_approved_draft(session, sync, fake):
    from app.models import ExpiryBatch, CycleCount, CycleCountLine
    from app.timeutil import today_local
    ready(sync, fake, session)
    user = mkuser(session, "reports_admin", "admin")
    store = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    product = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    session.add(ExpiryBatch(location_id=store.id, product_id=product.id, qty=4, expiry_date=today_local()))
    count = CycleCount(location_id=store.id, business_date=today_local(), status="open")
    count.lines = [CycleCountLine(product_id=product.id, reason="scanned", system_qty=5, counted_qty=4, variance=-1)]
    session.add(count); session.flush()
    assert "4 units expire" in " ".join(report_tools.expiry(session, user, {"location": store.id})["facts"])
    assert "difference -1" in " ".join(report_tools.counts(session, user, {"count_id": count.id})["facts"])
    result = report_tools.prepare_transfer(session, user, {"location": store.id, "lines": [{"product_id": product.id, "qty": 3}]})
    sheet = session.get(TransferSheet, result["sheet_id"])
    assert sheet.status == "draft" and sheet.lines[0].qty == 3
    assert "quantity 3" in " ".join(report_tools.transfers(session, user, {"sheet_id": sheet.id})["facts"])
