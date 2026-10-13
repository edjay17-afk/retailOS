"""The AI assistant: daily brief, questions and answers, explanations, proposed actions, evaluation.

Rules (from the V2 spec): numbers come from code and are re-checked after the model answers; the model proposes, the policy gate and a person decide;
every answer is stored with its context snapshot, prompt version and model so it can be audited; usefulness and unsupported claims are measured.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from .. import audit
from .. import settings_service as cfg
from ..models import AiAction, AiOutput, Recommendation, User
from ..security import allowed_location_ids
from ..timeutil import now_utc, today_local
from . import context as ctxmod
from . import policy, tools, validate
from .provider import LlmError, Provider, get_provider

log = logging.getLogger("retailos.ai")
PROMPT_VERSION = "p-1.0"
LINKS_OK = ("/issues", "/replenishment", "/inventory", "/sales", "/purchase-orders", "/counts", "/expiry", "/inspections", "/data-quality", "/sync")

RULES = """You are the operations analyst inside Retail OS, the system of a group of convenience stores and a warehouse in Cebu, Philippines.
You help managers see what needs attention today. Currency is Philippine pesos.

HARD RULES
1. Use ONLY the numbered facts you are given (each looks like [F12] (section) text). Never use outside knowledge about this business.
2. Every statement has a type: "fact", "calculation", "hypothesis" or "recommendation".
   - "fact" and "calculation" must cite the fact ids they rest on in "refs" (for example ["F3","F7"]).
   - Never write a number that is not in a fact you cite. If you add or compare numbers yourself, use type "calculation" and cite the facts used.
   - A "hypothesis" must be worded as a possibility ("may", "could") and never as a fact.
3. You cannot do anything yourself. You may only PROPOSE an action using a tool from the list, and a person decides. Never say you did something.
4. Management by exception: mention only what needs a person's attention, ranked by urgency, business impact, confidence and how actionable it is.
5. Reply with ONE JSON object and nothing else (no markdown, no commentary).
6. Answer exactly what the person asked, using only the facts that matter for that question. Do NOT bring up other stores, problems or numbers they did not ask about.
7. For a greeting, thanks or small talk, answer in one short friendly sentence with type "message" (no facts, no numbers, no actions). For a data question, give the data asked for, with citations. If asked what you can do, say you can look up sales, stock, purchase orders, expiry dates and issues."""

BRIEF_SCHEMA = """JSON shape:
{"headline": "one sentence",
 "items": [ {"priority": "high|medium|low", "title": "short", "location": "name or null",
             "statements": [ {"type": "fact|calculation|hypothesis|recommendation", "text": "one sentence", "refs": ["F1"]} ],
             "recommended_action": "what the manager should do",
             "confidence": 0.0-1.0, "requires_human_approval": true|false,
             "tool_suggestion": {"tool": "tool_name", "args": {...}} or null } ] }
Give at most 6 items. If nothing needs attention, return an empty items list and say so in the headline."""

ASK_SCHEMA = """Types for the answer: "fact", "calculation", "hypothesis", "recommendation", or "message" (plain conversation, no numbers, no refs).
To answer you may first ask for more facts by replying {"tool_calls":[{"tool":"get_inventory","args":{...}}]} using ONLY the read tools below. The new facts will be added with new ids.
When you are ready reply with the final answer in this JSON shape:
{"answer": [ {"type": "fact|calculation|hypothesis|recommendation", "text": "one sentence", "refs": ["F1"]} ],
 "suggested_actions": [ {"tool": "tool_name", "args": {...}, "why": "short reason", "confidence": 0.0-1.0} ],
 "follow_ups": ["optional short question the person may ask next"]}
If the facts do not contain the answer, say so in one statement and do not guess."""


class AiUnavailable(Exception):
    pass


def provider(db: Session | None = None) -> Provider:
    return get_provider()


def status(db: Session) -> dict:
    p = get_provider()
    today = now_utc().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)
    used = db.scalar(select(func.count()).select_from(AiOutput).where(AiOutput.created_at >= today, AiOutput.provider != "built-in")) or 0
    return {"available": p.available and bool(cfg.get(db, "ai.enabled")), "provider": p.name, "model": p.model, "used_today": used,
            "limit": int(cfg.get(db, "ai.max_calls_per_day")), "enabled": bool(cfg.get(db, "ai.enabled"))}


def _guard(db: Session) -> Provider:
    st = status(db)
    if not st["enabled"]:
        raise AiUnavailable("The AI assistant is switched off in Settings.")
    if not st["available"]:
        raise AiUnavailable("No AI service is connected. Set GEMINI_API_KEY or ANTHROPIC_API_KEY in .env, or install and sign in to Claude on this computer.")
    if st["used_today"] >= st["limit"]:
        raise AiUnavailable(f"The daily limit of {st['limit']} AI requests has been reached.")
    return get_provider()


def parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


def _call_json(p: Provider, system: str, messages: list[dict], max_tokens: int = 3500) -> tuple[dict, str, object]:
    """One model call that must return JSON; one repair retry if it does not."""
    res = p.complete(system, messages, max_tokens=max_tokens)
    try:
        return parse_json(res.text), res.text, res
    except ValueError:
        res2 = p.complete(system, messages + [{"role": "assistant", "content": res.text}, {"role": "user", "content": "That was not valid JSON. Reply again with ONLY the JSON object."}], max_tokens=max_tokens)
        try:
            return parse_json(res2.text), res2.text, res2
        except ValueError as e:
            raise LlmError("the AI did not return a readable answer") from e


def _location_ids(db: Session, user: User | None) -> list[int]:
    from ..models import Location
    if user is None:
        return list(db.scalars(select(Location.id).where(Location.monitored.is_(True))))
    return allowed_location_ids(db, user)


def _store(db: Session, *, kind: str, user: User | None, p: Provider, res, ctx: ctxmod.Context, output: dict | None, raw: str | None, val: dict | None,
           question: str | None = None, tool_log: list | None = None, error: str | None = None) -> AiOutput:
    row = AiOutput(kind=kind, user_id=user.id if user else None, provider=getattr(res, "provider", None) or p.name, model=getattr(res, "model", p.model), prompt_version=PROMPT_VERSION,
                   context_version=ctxmod.CONTEXT_VERSION, context_hash=ctx.hash, context_json=ctx.as_json(), question=question, output_json=output, raw_text=raw,
                   validation=val, tool_log=tool_log, duration_ms=getattr(res, "duration_ms", None), cost_usd=getattr(res, "cost_usd", None),
                   status="failed" if error else "ok", error=error)
    db.add(row)
    db.flush()
    audit.log(db, actor_type="ai", actor_id=f"{p.name}:{row.model}", action=f"ai.{kind}", entity_type="ai_output", entity_id=row.id,
              after={"context_hash": ctx.hash, "prompt_version": PROMPT_VERSION, "status": row.status, "unsupported": (val or {}).get("unsupported")},
              context={"asked_by": user.id if user else None})
    return row


def _sanitize_actions(db: Session, output: dict, ctx: ctxmod.Context) -> list[dict]:
    """Collect tool proposals the model made, dropping anything that is not a known action tool with complete arguments."""
    props = []
    for item in output.get("items", []) or []:
        ts = item.get("tool_suggestion")
        if isinstance(ts, dict):
            props.append({"tool": ts.get("tool"), "args": ts.get("args") or {}, "why": item.get("recommended_action") or item.get("title"), "confidence": item.get("confidence")})
    for a in output.get("suggested_actions", []) or []:
        if isinstance(a, dict):
            props.append({"tool": a.get("tool"), "args": a.get("args") or {}, "why": a.get("why"), "confidence": a.get("confidence")})
    out = []
    for pr in props:
        t = tools.TOOLS.get(str(pr["tool"]))
        if t is None or t.risk == "read" or not isinstance(pr["args"], dict) or tools.check_args(t, pr["args"]):
            continue
        out.append(pr)
    return out


def _attach_links(output: dict, ctx: ctxmod.Context) -> None:
    for item in output.get("items", []) or []:
        link = None
        for st in item.get("statements", []) or []:
            for r in st.get("refs") or []:
                f = ctx.fact(r) if isinstance(r, str) else None
                if f and f.link and not link:
                    link = f.link
        item["link"] = link if link and link.startswith(LINKS_OK) else None


def _finish(db: Session, row: AiOutput, user: User | None, output: dict, ctx: ctxmod.Context) -> AiOutput:
    for pr in _sanitize_actions(db, output, ctx):
        row.actions.append(AiAction(tool=pr["tool"], args=pr["args"], risk=tools.TOOLS[pr["tool"]].risk, why=str(pr["why"])[:400] if pr["why"] else None,
                                    proposed_for=user.id if user else None))
    db.flush()
    return row


# ======================================================================================= daily brief
def generate_brief(db: Session, user: User | None = None) -> AiOutput:
    p = _guard(db)
    ids = _location_ids(db, user)
    ctx = ctxmod.build(db, user, ids)
    system = RULES + "\n\n" + BRIEF_SCHEMA + "\n\nACTION TOOLS you may propose (a person decides):\n" + tools.prompt_catalog(("low", "high"))
    msg = f"Business date {today_local()}.\n\nFACTS\n{ctx.as_prompt()}\n\nWrite today's management brief as JSON."
    try:
        out, raw, res = _call_json(p, system, [{"role": "user", "content": msg}])
    except LlmError as e:
        row = _store(db, kind="brief", user=user, p=p, res=None, ctx=ctx, output=None, raw=None, val=None, error=str(e))
        return row
    val = validate.validate(validate.collect_statements(out), ctx)
    _attach_links(out, ctx)
    row = _store(db, kind="brief", user=user, p=p, res=res, ctx=ctx, output=out, raw=raw, val=val)
    return _finish(db, row, user, out, ctx)


def latest_brief(db: Session, user: User | None = None) -> AiOutput | None:
    """Newest successful brief. Store-scoped users only ever see briefs generated for their own account
    (their context contains only their stores); HQ roles see the latest company-wide brief, never a store one."""
    q = select(AiOutput).where(AiOutput.kind == "brief", AiOutput.status == "ok")
    if user is not None and user.location_ids:
        q = q.where(AiOutput.user_id == user.id)
    else:
        # location_ids is a JSON column: unrestricted users store SQL NULL or the JSON text 'null' depending on how the row was written
        unrestricted = User.location_ids.is_(None) | (func.cast(User.location_ids, String) == "null")
        q = q.outerjoin(User, User.id == AiOutput.user_id).where(AiOutput.user_id.is_(None) | unrestricted)
    return db.scalar(q.order_by(AiOutput.id.desc()).limit(1))


# ======================================================================================= questions
_GREET = re.compile(r"^\s*(hi+|hello+|hey+|heya|yo|good\s*(morning|afternoon|evening|day)|howdy|sup|what'?s up|how are you|how r u|are you there|anyone there|"
                    r"hola|kumusta|musta|你好|您好|嗨|哈喽|早上好|下午好|晚上好)[\s!.?,~]*$", re.I)
_THANKS = re.compile(r"^\s*(thanks?( you)?|thank u|thx|ty|salamat|ok(ay)?|got it|great|nice|cool|noted|alright|bye|goodbye|see you|谢谢|多谢|好的|收到|再见)[\s!.?,~]*$", re.I)
_HELP = re.compile(r"^\s*(help|what can you do|who are you|what are you|what do you do|how do you work|你能做什么|你是谁|帮助)[\s!.?,~]*$", re.I)


def smalltalk(question: str, user: User) -> str | None:
    """Greetings, thanks and 'what can you do' are answered directly, with no model call, so they never turn into an unrelated report."""
    q = question.strip()
    zh = bool(re.search(r"[一-鿿]", q))
    name = (user.full_name or user.username or "").split(" ")[0]
    if _GREET.match(q):
        return (f"你好{('，' + name) if name else ''}！我可以查询销售、库存、采购订单、到期日期和待处理问题。想了解什么？" if zh else
                f"Hi{(' ' + name) if name else ''}! I can look up sales, stock, purchase orders, expiry dates and open issues. What would you like to know?")
    if _THANKS.match(q):
        return "不客气！还有什么需要查的吗？" if zh else "You're welcome! Ask me anything else about sales, stock or orders."
    if _HELP.match(q):
        return ("我可以查询各门店的销售、库存、采购订单、到期日期和待处理问题，也可以说明每日补货建议。我只使用系统里的数据，不会自己执行操作。" if zh else
                "I can look up sales, stock, purchase orders, expiry dates and open issues for your stores, and explain order suggestions. I only use the data in this system, and I never do anything myself: I propose, a person decides.")
    return None


def ask(db: Session, user: User, question: str, history: list[dict] | None = None) -> AiOutput:
    question = question.strip()[:600]
    if not question:
        raise AiUnavailable("Type a question first.")
    hello = smalltalk(question, user)
    if hello:  # no model call, no daily-limit use, and it works even when the AI service is down
        from types import SimpleNamespace
        zh = bool(re.search(r"[一-鿿]", question))
        out = {"answer": [{"type": "message", "text": hello, "refs": [], "check": "ok"}], "suggested_actions": [],
               "follow_ups": ["今天有什么需要注意的？", "昨天各门店销售额是多少？"] if zh else ["What needs attention today?", "Net sales yesterday by store"]}
        return _store(db, kind="chat", user=user, p=get_provider(), res=SimpleNamespace(provider="built-in", model="rules", duration_ms=0, cost_usd=0), ctx=ctxmod.Context(),
                      output=out, raw=None, val={"statements": 1, "unsupported": 0, "derived": 0, "unsupported_rate": 0.0, "verified": True, "flags": []}, question=question)
    p = _guard(db)
    ids = _location_ids(db, user)
    ctx = ctxmod.build(db, user, ids)
    system = RULES + "\n\n" + ASK_SCHEMA + "\n\nREAD TOOLS:\n" + tools.prompt_catalog(("read",)) + "\n\nACTION TOOLS you may propose (a person decides):\n" + tools.prompt_catalog(("low", "high"))
    earlier = ""
    if history:  # recent turns of the chat window so follow-ups make sense; facts must still come from FACTS
        turns = [f"{'Person' if h.get('role') == 'user' else 'Assistant'}: {str(h.get('text', ''))[:400]}" for h in history[-6:] if h.get("text")]
        if turns:
            earlier = "EARLIER IN THIS CHAT (for context only; not facts)\n" + "\n".join(turns) + "\n\n"
    messages = [{"role": "user", "content": f"FACTS\n{ctx.as_prompt()}\n\n{earlier}QUESTION from {user.full_name or user.username} ({user.role}): {question}"}]
    log_: list[dict] = []
    res = None
    raw = ""
    out: dict = {}
    try:
        for step in range(4):
            out, raw, res = _call_json(p, system, messages)
            calls = out.get("tool_calls")
            if not calls or step == 3:
                break
            results = []
            for c in calls[:3]:
                t = tools.TOOLS.get(str(c.get("tool")))
                if t is None or t.risk != "read":
                    results.append({"tool": c.get("tool"), "error": "not a read tool"})
                    continue
                try:
                    r = t.run(db, user, c.get("args") or {})
                    new_ids = [ctx.add(f"tool:{t.name}", f) for f in r["facts"]]
                    results.append({"tool": t.name, "new_facts": [{"id": i, "text": ctx.fact(i).text} for i in new_ids]})
                    log_.append({"tool": t.name, "args": c.get("args"), "facts_added": len(new_ids)})
                except tools.ToolError as e:
                    results.append({"tool": t.name, "error": str(e)})
                    log_.append({"tool": t.name, "args": c.get("args"), "error": str(e)})
            messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": "TOOL RESULTS (new facts you may cite):\n" + json.dumps(results, default=str)}]
        if "answer" not in out:
            raise LlmError("the AI did not give a final answer")
    except LlmError as e:
        return _store(db, kind="chat", user=user, p=p, res=res, ctx=ctx, output=None, raw=raw or None, val=None, question=question, tool_log=log_, error=str(e))
    val = validate.validate(validate.collect_statements(out), ctx)
    row = _store(db, kind="chat", user=user, p=p, res=res, ctx=ctx, output=out, raw=raw, val=val, question=question, tool_log=log_)
    return _finish(db, row, user, out, ctx)


def explain_recommendation(db: Session, user: User, rec: Recommendation) -> AiOutput:
    """Why was this quantity suggested? The answer must cite the calculation inputs and may not invent reasons (spec acceptance test 6)."""
    p = _guard(db)
    ctx = ctxmod.Context()
    ctx.meta = {"recommendation_id": rec.id}
    e = rec.explanation or {}
    i = e.get("inputs", {})
    add = lambda t: ctx.add("calculation_inputs", t, "/replenishment")  # noqa: E731
    add(f"Suggestion #{rec.id}: {rec.product.name} at {rec.location.name}, {e.get('source')}")
    add(f"Units sold over {i.get('trading_days')} trading days: {i.get('units_sold'):g}; average daily sales {rec.ads:g}")
    add(f"Lead time {rec.lead_time:g} days plus review period {rec.review_period:g} days; safety stock {rec.safety_stock:g} units ({rec.safety_days:g} days of sales)")
    add(f"Target stock {rec.target_stock:g}; on hand {rec.on_hand:g}; incoming {rec.incoming:g}; position {rec.position:g}; raw order {rec.raw_qty:g}")
    for s in e.get("steps", []):
        if s.get("step") != "raw":
            add(f"Adjustment {s['step'].replace('_', ' ')}: from {s.get('from'):g} to {s.get('to'):g}")
    add(f"Suggested quantity {rec.final_qty:g}, value {rec.value:,.2f}, confidence {rec.confidence:.2f}, status {rec.status}")
    for n in e.get("notes", []):
        add(n)
    for r in rec.needs_review_reasons or []:
        add(f"Needs review because: {r.replace('_', ' ')}")
    for pen in e.get("confidence_penalties", []):
        add(f"Confidence lowered by {int(pen['penalty'] * 100)} points: {pen['reason']}")
    system = RULES + "\n\n" + (
        'Reply with {"answer":[statements],"suggested_actions":[],"follow_ups":[]} where each statement has the shape {"type","text","refs"}. '
        "Explain in plain language why this quantity was suggested, using only these calculation inputs. Do not invent reasons.")
    msg = f"FACTS\n{ctx.as_prompt()}\n\nQUESTION: Why was this quantity suggested, and is there anything I should check before approving?"
    try:
        out, raw, res = _call_json(p, system, [{"role": "user", "content": msg}], max_tokens=1500)
    except LlmError as ex:
        return _store(db, kind="explain", user=user, p=p, res=None, ctx=ctx, output=None, raw=None, val=None, question=f"Explain suggestion #{rec.id}", error=str(ex))
    val = validate.validate(validate.collect_statements(out), ctx)
    row = _store(db, kind="explain", user=user, p=p, res=res, ctx=ctx, output=out, raw=raw, val=val, question=f"Explain suggestion #{rec.id}")
    return row


# ======================================================================================= actions
class ActionError(Exception):
    pass


def gate_for(db: Session, user: User, action: AiAction, confidence: float | None = None) -> policy.Gate:
    return policy.check(db, user, action.tool, action.args, confidence=confidence)


def run_action(db: Session, user: User, action: AiAction, note: str | None = None) -> AiAction:
    """A person clicks Run or Approve. The policy gate is checked again at that moment, with that person's own permissions."""
    if action.status not in ("proposed", "needs_approval"):
        raise ActionError(f"This action is already {action.status}")
    tool = tools.TOOLS[action.tool]
    g = policy.check(db, user, action.tool, action.args)
    action.gate = g.as_dict()
    if g.decision == "deny":
        action.status, action.decided_by, action.decided_at = "denied", user.id, now_utc()
        audit.log(db, actor_type="human", actor_id=user.id, action="ai.action_denied", entity_type="ai_action", entity_id=action.id, after=action.gate)
        raise ActionError("Not allowed: " + "; ".join(g.reasons))
    try:
        args = {**action.args, "note": note} if (action.tool == "approve_replenishment" and note) else action.args
        with db.begin_nested():  # a failed action undoes only its own changes
            result = tool.run(db, user, args)
        action.args = args
        action.status, action.result = "executed", result
    except Exception as e:  # noqa: BLE001 - a failed action must be recorded, not crash the page
        action.status, action.result = "failed", {"error": str(e)}
        action.decided_by, action.decided_at = user.id, now_utc()
        audit.log(db, actor_type="human", actor_id=user.id, action="ai.action_failed", entity_type="ai_action", entity_id=action.id, after={"error": str(e)})
        db.flush()
        raise ActionError(str(e)) from e
    action.decided_by, action.decided_at = user.id, now_utc()
    audit.log(db, actor_type="human", actor_id=user.id, action=f"ai.action.{action.tool}", entity_type="ai_action", entity_id=action.id,
              after=action.result, context={"proposed_by": "ai", "output_id": action.output_id, "gate": action.gate, "risk": action.risk}, reason=note)
    return action


def reject_action(db: Session, user: User, action: AiAction) -> AiAction:
    if action.status not in ("proposed", "needs_approval"):
        raise ActionError(f"This action is already {action.status}")
    action.status, action.decided_by, action.decided_at = "rejected", user.id, now_utc()
    audit.log(db, actor_type="human", actor_id=user.id, action="ai.action_rejected", entity_type="ai_action", entity_id=action.id, after={"tool": action.tool})
    return action


def rate(db: Session, user: User, output: AiOutput, useful: bool, note: str | None = None) -> AiOutput:
    output.rating, output.rating_note = (1 if useful else 0), (note or None)
    audit.log(db, actor_type="human", actor_id=user.id, action="ai.rate", entity_type="ai_output", entity_id=output.id, after={"useful": useful})
    return output


# ======================================================================================= evaluation (spec section 15.3)
def metrics(db: Session, days: int = 60) -> dict:
    since = now_utc() - timedelta(days=days)
    outs = list(db.scalars(select(AiOutput).where(AiOutput.created_at >= since)))
    ok = [o for o in outs if o.status == "ok"]
    rated = [o for o in ok if o.rating is not None]
    stmts = sum((o.validation or {}).get("statements", 0) for o in ok)
    bad = sum((o.validation or {}).get("unsupported", 0) for o in ok)
    acts = list(db.scalars(select(AiAction).where(AiAction.created_at >= since)))
    by_tool: dict[str, dict] = {}
    for a in acts:
        d = by_tool.setdefault(a.tool, {"risk": a.risk, "proposed": 0, "executed": 0, "rejected": 0, "failed": 0, "denied": 0})
        d["proposed"] += 1
        if a.status in d:
            d[a.status] += 1
    by_provider: dict[str, dict] = {}
    for o in outs:  # reliability is managed, not discovered: failures counted per provider/model
        key = f"{o.provider} ({o.model})" if o.model and o.model != o.provider else o.provider
        d = by_provider.setdefault(key, {"ok": 0, "failed": 0, "last_error": None})
        d["ok" if o.status == "ok" else "failed"] += 1
        if o.status != "ok":
            d["last_error"] = o.error
    executed = sum(1 for a in acts if a.status == "executed")
    failed = sum(1 for a in acts if a.status == "failed")
    return {"answers": len(ok), "failed_answers": len(outs) - len(ok), "rated": len(rated),
            "usefulness": (sum(o.rating for o in rated) / len(rated)) if rated else None,
            "statements": stmts, "unsupported": bad, "unsupported_rate": (bad / stmts) if stmts else None,
            "actions_proposed": len(acts), "actions_executed": executed, "action_success_rate": (executed / (executed + failed)) if (executed + failed) else None,
            "approval_rate": (executed / len(acts)) if acts else None, "by_tool": by_tool, "by_provider": by_provider,
            "avg_seconds": (sum(o.duration_ms or 0 for o in ok) / len(ok) / 1000) if ok else None, "cost_usd": sum(o.cost_usd or 0 for o in ok)}
