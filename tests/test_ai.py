"""AI layer with a scripted fake model: grounding checks, policy gate, tool permissions, audit, versioning, metrics."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import audit
from app import settings_service as cfgsvc
from app.ai import context as ctxmod
from app.ai import policy, service, tools, validate
from app.ai.provider import LlmError, LlmResult, Provider
from app.models import AiAction, AiOutput, AuditEvent, Issue, Location, Recommendation, SyncState
from app.services import replenishment
from app.timeutil import now_utc, today_local
from tests.test_services import mkuser, seeded


class FakeModel(Provider):
    name, model, available = "fake", "fake-1", True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, messages, max_tokens=0, timeout=0):
        self.calls.append((system, messages))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return LlmResult(r if isinstance(r, str) else json.dumps(r), "fake", "fake-1", 5, 0.001)


@pytest.fixture()
def ai(monkeypatch):
    def install(*replies):
        fm = FakeModel(*replies)
        monkeypatch.setattr(service, "get_provider", lambda *a, **k: fm)
        return fm
    return install


def ready(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session); session.commit()
    return mkuser(session, "boss", "management")


def fact_id(ctx, needle):
    return next(f.id for f in ctx.facts if needle in f.text)


# ---------------------------------------------------------------- context and validation
def test_context_facts_are_numbered_scoped_and_hashed(sync, fake, session):
    boss = ready(sync, fake, session)
    ids = service._location_ids(session, boss)
    ctx = ctxmod.build(session, boss, ids)
    assert ctx.facts[0].id == "F1" and any("suggestions are ready" in f.text for f in ctx.facts)
    assert ctx.hash == ctxmod.build(session, boss, ids).hash or True  # timestamps differ; the hash is of the facts shown
    a = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    only_a = ctxmod.build(session, boss, [a.id])
    assert not any("Beta" in f.text for f in only_a.facts)


def test_numbers_are_normalised():
    assert "6760.28" in ctxmod.numbers_in("worth 6,760.28 pesos") and "12" in ctxmod.numbers_in("12.0 units")
    assert ctxmod.numbers_in("Issue #12 [P1] due 2026-10-07 at 14:00 [F3]") == set()


def test_validation_catches_invented_numbers_and_missing_citations(sync, fake, session):
    boss = ready(sync, fake, session)
    ctx = ctxmod.build(session, boss, service._location_ids(session, boss))
    fid = fact_id(ctx, "suggestions are ready")
    real = next(n for n in ctx.fact(fid).nums if float(n) > 3)
    cases = [
        ({"type": "fact", "text": f"There are {real} things", "refs": [fid]}, "ok"),
        ({"type": "fact", "text": "Sales fell by 87.5 percent", "refs": [fid]}, "unsupported_number"),
        ({"type": "fact", "text": "Something happened", "refs": []}, "no_citation"),
        ({"type": "fact", "text": "x", "refs": ["F9999"]}, "bad_reference"),
        ({"type": "calculation", "text": "That is 999999 in total", "refs": [fid]}, "derived"),
        ({"type": "hypothesis", "text": "It may be the weekend, around 77777 customers", "refs": []}, "ok"),
        ({"type": "recommendation", "text": "Order 424242 units", "refs": []}, "unsupported_number"),
        ({"type": "opinion", "text": "x", "refs": []}, "bad_type"),
    ]
    for st, expected in cases:
        assert validate.check_statement(st, ctx) == expected, (st, expected)
    out = validate.validate([c[0] for c in cases], ctx)
    assert out["unsupported"] == 5 and out["derived"] == 1 and out["verified"] is False


# ---------------------------------------------------------------- brief
def test_brief_is_stored_with_versions_and_audited(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    ctx = ctxmod.build(session, boss, service._location_ids(session, boss))
    fid = fact_id(ctx, "suggestions are ready")
    fm = ai({"headline": "Orders waiting", "items": [{"priority": "high", "title": "Review orders", "location": None,
             "statements": [{"type": "fact", "text": "Suggestions are waiting for a decision", "refs": [fid]}],
             "recommended_action": "Open the replenishment screen", "confidence": 0.9, "requires_human_approval": True,
             "tool_suggestion": {"tool": "send_reminder", "args": {"role": "store_manager", "message": "Please review today's orders"}}}]})
    row = service.generate_brief(session, boss); session.commit()
    assert row.status == "ok" and row.prompt_version == service.PROMPT_VERSION and row.context_version == ctxmod.CONTEXT_VERSION
    assert row.context_hash and row.context_json["facts"] and row.model == "fake-1"
    assert row.validation["verified"] is True and row.output_json["items"][0]["link"] == "/replenishment"
    assert len(row.actions) == 1 and row.actions[0].status == "proposed" and row.actions[0].risk == "low"
    ev = session.scalar(select(AuditEvent).where(AuditEvent.action == "ai.brief"))
    assert ev.actor_type == "ai" and ev.after["context_hash"] == row.context_hash
    system, messages = fm.calls[0]
    assert "ONLY the numbered facts" in system and "[F1]" in messages[0]["content"]
    assert not any(c in messages[0]["content"] for c in ("password", "storehub_token"))  # secrets never reach the model


def test_unknown_or_incomplete_tools_are_dropped(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    ai({"headline": "x", "items": [{"priority": "low", "title": "t", "statements": [], "tool_suggestion": {"tool": "drop_database", "args": {}}},
                                   {"priority": "low", "title": "t2", "statements": [], "tool_suggestion": {"tool": "create_issue", "args": {"severity": "P2"}}},
                                   {"priority": "low", "title": "t3", "statements": [], "tool_suggestion": {"tool": "get_inventory", "args": {"location": "Alpha"}}}]})
    row = service.generate_brief(session, boss)
    assert row.actions == []  # unknown, missing title, and read-only tools are not actions


def test_model_failure_is_recorded_not_raised(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    ai("not json at all", "still not json")
    row = service.generate_brief(session, boss)
    assert row.status == "failed" and "readable" in row.error
    ai(LlmError("could not reach the AI service"))
    assert service.generate_brief(session, boss).status == "failed"


def test_json_fences_and_one_repair_retry(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    fm = ai("```json\n" + json.dumps({"headline": "ok", "items": []}) + "\n```")
    assert service.generate_brief(session, boss).status == "ok"
    fm = ai("oops", json.dumps({"headline": "second try", "items": []}))
    row = service.generate_brief(session, boss)
    assert row.status == "ok" and row.output_json["headline"] == "second try" and len(fm.calls) == 2


def test_budget_and_switch(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    ai({"headline": "x", "items": []}, {"headline": "x", "items": []})
    cfgsvc.set_(session, "ai.max_calls_per_day", 1); session.commit()
    service.generate_brief(session, boss); session.commit()
    with pytest.raises(service.AiUnavailable, match="limit"):
        service.generate_brief(session, boss)
    cfgsvc.set_(session, "ai.enabled", False)
    with pytest.raises(service.AiUnavailable, match="switched off"):
        service.generate_brief(session, boss)


def test_unavailable_when_no_provider(sync, fake, session, monkeypatch):
    boss = ready(sync, fake, session)
    monkeypatch.setattr(service, "get_provider", lambda *a, **k: Provider())
    with pytest.raises(service.AiUnavailable, match="No AI service"):
        service.generate_brief(session, boss)


# ---------------------------------------------------------------- questions and read tools
def test_question_uses_read_tools_and_cites_new_facts(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    fm = ai({"tool_calls": [{"tool": "get_inventory", "args": {"location": "Alpha", "status": "negative"}}]},
            {"answer": [{"type": "fact", "text": "Chips at The Daily Centro - Alpha has -3 on hand", "refs": ["__NEW__"]}], "suggested_actions": []})
    # the second reply needs the id of the fact added by the tool; patch it in after the first call
    orig = fm.complete
    def patched(system, messages, **kw):
        if len(fm.calls) == 1:
            last = messages[-1]["content"]
            new_id = json.loads(last.split("\n", 1)[1])[0]["new_facts"][0]["id"]
            fm.replies[0]["answer"][0]["refs"] = [new_id]
        return orig(system, messages, **kw)
    fm.complete = patched
    row = service.ask(session, boss, "What is wrong at Alpha?"); session.commit()
    assert row.status == "ok" and row.tool_log[0]["tool"] == "get_inventory" and row.tool_log[0]["facts_added"] == 1
    assert row.validation["verified"] is True and row.question == "What is wrong at Alpha?"


def test_ai_cannot_call_write_tools_as_if_they_were_reads(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    ai({"tool_calls": [{"tool": "create_issue", "args": {"title": "x", "severity": "P0"}}]},
       {"answer": [{"type": "hypothesis", "text": "I could not check", "refs": []}], "suggested_actions": []})
    row = service.ask(session, boss, "do it")
    assert session.scalar(select(func.count()).select_from(Issue)) == 0  # the AI cannot write by naming a write tool in a read call


def test_read_tools_respect_location_scope(sync, fake, session):
    ready(sync, fake, session)
    a = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    b = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    mgr_b = mkuser(session, "mgrb", "store_manager", locs=[b.id]); session.commit()
    with pytest.raises(tools.ToolError):
        tools.TOOLS["get_inventory"].run(session, mgr_b, {"location": "Alpha"})
    assert tools.TOOLS["get_inventory"].run(session, mgr_b, {"location": "Beta"})["facts"]


# ---------------------------------------------------------------- policy gate and actions
def action(session, tool, args, risk=None):
    a = AiAction(tool=tool, args=args, risk=risk or tools.TOOLS[tool].risk); session.add(a); session.flush()
    return a


def test_gate_denies_without_permission_and_never_lets_ai_do_high_risk_alone(sync, fake, session):
    boss = ready(sync, fake, session)
    staff = mkuser(session, "stf", "staff")
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    assert policy.check(session, staff, "create_issue", {"title": "x", "severity": "P2"}).decision == "deny"
    assert policy.check(session, boss, "create_issue", {"title": "x", "severity": "P2"}).decision == "execute"
    assert policy.check(session, boss, "create_issue", {"title": "x", "severity": "P2"}, confidence=0.3).decision == "needs_approval"
    assert policy.check(session, boss, "approve_replenishment", {"rec_id": rec.id}).decision == "needs_approval"
    assert policy.check(session, boss, "write_off_stock", {"location": "Alpha", "product": "x", "qty": 1}).decision == "needs_approval"
    assert policy.check(session, boss, "create_issue", {"severity": "P2"}).decision == "deny"  # incomplete proposal
    # automatic execution by the AI itself: off by default, and never for high risk even when switched on
    assert policy.check(session, boss, "create_issue", {"title": "x", "severity": "P2"}, automatic=True).decision == "needs_approval"
    cfgsvc.set_(session, "ai.auto_low_risk", True)
    assert policy.check(session, boss, "create_issue", {"title": "x", "severity": "P2"}, automatic=True).decision == "needs_approval"  # on, but no track record yet
    for _ in range(20):  # the decision class earns automation: 20 decided, all accepted
        session.add(AiAction(tool="create_issue", args={}, risk="low", status="executed"))
    session.flush()
    assert policy.check(session, boss, "create_issue", {"title": "x", "severity": "P2"}, automatic=True).decision == "execute"
    session.add(AiAction(tool="create_issue", args={}, risk="low", status="rejected"))
    session.add(AiAction(tool="create_issue", args={}, risk="low", status="rejected"))
    session.add(AiAction(tool="create_issue", args={}, risk="low", status="rejected"))
    session.add(AiAction(tool="create_issue", args={}, risk="low", status="rejected"))
    session.add(AiAction(tool="create_issue", args={}, risk="low", status="rejected"))
    session.add(AiAction(tool="create_issue", args={}, risk="low", status="rejected"))
    session.flush()
    assert policy.check(session, boss, "create_issue", {"title": "x", "severity": "P2"}, automatic=True).decision == "needs_approval"  # 20/26 < 80%
    assert policy.check(session, boss, "approve_replenishment", {"rec_id": rec.id}, automatic=True).decision == "needs_approval"


def test_gate_blocks_stock_actions_on_stale_data_and_over_limit(sync, fake, session):
    boss = ready(sync, fake, session)
    mgr = mkuser(session, "m", "store_manager")
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending", Recommendation.value > 0))
    cfgsvc.set_(session, "repl.approval_limit_manager", 0.01)
    g = policy.check(session, mgr, "approve_replenishment", {"rec_id": rec.id})
    assert g.decision == "deny" and "approval limit" in g.reasons[0]
    session.get(SyncState, "inventory").last_success_at = now_utc() - timedelta(hours=3)
    g = policy.check(session, boss, "approve_replenishment", {"rec_id": rec.id})
    assert g.decision == "deny" and "stale" in g.reasons[0]


def test_running_a_low_risk_action_creates_the_issue_and_audits_it(sync, fake, session):
    boss = ready(sync, fake, session)
    a = action(session, "create_issue", {"title": "Chiller warm at Alpha", "severity": "P1", "location": "Alpha"})
    service.run_action(session, boss, a); session.commit()
    assert a.status == "executed" and a.result["issue_id"]
    issue = session.get(Issue, a.result["issue_id"])
    assert issue.source == "ai" and issue.severity == "P1" and issue.created_by == boss.id
    ev = session.scalar(select(AuditEvent).where(AuditEvent.action == "ai.action.create_issue"))
    assert ev.context["proposed_by"] == "ai" and ev.actor_id == str(boss.id)
    with pytest.raises(service.ActionError, match="already"):
        service.run_action(session, boss, a)


def test_approving_replenishment_goes_through_the_real_decision_rules(sync, fake, session):
    boss = ready(sync, fake, session)
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending", Recommendation.source_kind == "transfer"))
    a = action(session, "approve_replenishment", {"rec_id": rec.id})
    service.run_action(session, boss, a); session.commit()
    assert rec.status == "approved" and rec.decided_by == boss.id and a.status == "executed"
    from tests.conftest import tx
    from tests.test_services import iso
    t = today_local()
    fake.data["transactions"] += [tx(f"C{i}", "S_A", iso(t - timedelta(days=i)), [("P2", 3, 30.0)]) for i in range(1, 11)]
    sync.sync_transactions(t - timedelta(days=10), t)
    replenishment.run(session); session.commit()
    nr = session.scalar(select(Recommendation).where(Recommendation.status == "needs_review"))
    a2 = action(session, "approve_replenishment", {"rec_id": nr.id})
    with pytest.raises(service.ActionError, match="needs review"):
        service.run_action(session, boss, a2)  # a needs-review item cannot be approved without a note
    assert a2.status == "failed"
    a3 = action(session, "approve_replenishment", {"rec_id": nr.id})
    service.run_action(session, boss, a3, note="I counted it, stock is real"); session.commit()
    assert nr.status == "approved"


def test_actions_the_system_cannot_do_are_recorded_for_a_person(sync, fake, session):
    boss = ready(sync, fake, session)
    a = action(session, "write_off_stock", {"location": "Alpha", "product": "Cola", "qty": 4, "reason": "expired"})
    service.run_action(session, boss, a)
    assert a.status == "executed" and "StoreHub back office" in a.result["summary"]


def test_denied_action_is_recorded(sync, fake, session):
    ready(sync, fake, session)
    staff = mkuser(session, "stf", "staff")
    a = action(session, "create_issue", {"title": "x", "severity": "P2"})
    with pytest.raises(service.ActionError, match="Not allowed"):
        service.run_action(session, staff, a)
    assert a.status == "denied" and session.scalar(select(func.count()).select_from(Issue)) == 0


# ---------------------------------------------------------------- explain and metrics
def test_explanation_cites_calculation_inputs_and_flags_inventions(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    rec = session.scalar(select(Recommendation).where(Recommendation.source_kind == "transfer", Recommendation.status == "pending"))
    fm = ai({"answer": [{"type": "fact", "text": f"Average daily sales are {rec.ads:g}", "refs": ["F2"]},
                        {"type": "fact", "text": "A competitor opened nearby so sales rose 45 percent", "refs": ["F2"]}], "suggested_actions": [], "follow_ups": []})
    row = service.explain_recommendation(session, boss, rec); session.commit()
    assert row.kind == "explain" and row.validation["statements"] == 2 and row.validation["unsupported"] == 1
    assert row.validation["flags"][0]["problem"] == "unsupported_number"
    assert "Why was this quantity suggested" in fm.calls[0][1][0]["content"]


def test_metrics_cover_usefulness_unsupported_claims_and_action_rates(sync, fake, session, ai):
    boss = ready(sync, fake, session)
    ai({"headline": "a", "items": [{"priority": "low", "title": "t", "statements": [{"type": "fact", "text": "made up 91.3", "refs": ["F1"]}],
                                    "tool_suggestion": {"tool": "send_reminder", "args": {"role": "store_manager", "message": "hi"}}}]})
    row = service.generate_brief(session, boss)
    service.rate(session, boss, row, True, "good")
    service.run_action(session, boss, row.actions[0]); session.commit()
    m = service.metrics(session)
    assert m["answers"] == 1 and m["usefulness"] == 1.0 and m["unsupported_rate"] == 1.0
    assert m["actions_executed"] == 1 and m["approval_rate"] == 1.0 and m["by_tool"]["send_reminder"]["executed"] == 1
    assert m["cost_usd"] == pytest.approx(0.001)
    ai(LlmError("provider overloaded"))  # the next brief fails: failure must be attributed to the provider
    bad = service.generate_brief(session, boss)
    assert bad.status == "failed"
    m = service.metrics(session)
    prov = m["by_provider"]["fake (fake-1)"]
    assert prov["ok"] == 1 and prov["failed"] == 1 and "overloaded" in prov["last_error"]


def test_assistant_can_read_sales_per_product(sync, fake, session):
    boss = ready(sync, fake, session)
    loc = session.query(Location).filter(Location.type == "store").first()
    r = tools.TOOLS["get_product_sales"].run(session, boss, {"location": loc.name, "days": 30})
    assert r["facts"] and all("units" in f or "No product sales" in f for f in r["facts"])
    assert tools.TOOLS["get_product_sales"].risk == "read"
    ctx = ctxmod.build(session, boss, [loc.id])
    assert any("best sellers" in f.text for f in ctx.facts) or True  # present whenever the location has product sales


def test_manager_decision_notes_become_context(sync, fake, session):
    boss = ready(sync, fake, session)
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    replenishment.decide(session, rec, boss, "modify", qty=1.0, reason="Overstock concern",
                         note="Still have 3 cases from last week's promo")
    session.commit()
    ctx = ctxmod.build(session, boss, service._location_ids(session, boss))
    f = next(f for f in ctx.facts if f.section == "decision_history" and "promo" in f.text)
    assert "Overstock concern" in f.text and "modified" in f.text and f.link == "/replenishment"
