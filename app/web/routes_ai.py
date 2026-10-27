"""AI assistant screens: brief, questions, explanations, proposed actions, ratings, evaluation."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..ai import policy, service, tools, conversations, page_context, validate
from ..models import AiAction, AiOutput, Recommendation
from ..security import has_perm
from .deps import Ctx, need, redirect, render, templates
from . import chat_support

router = APIRouter()

SUGGESTED_QUESTIONS = [
    "What should I deal with first today?",
    "Which items are most likely to run out this week?",
    "Why are so many order suggestions marked needs review?",
    "Which purchase orders should we chase?",
    "How are the stores doing compared with their usual sales?",
]


def _visible(c: Ctx, out: AiOutput) -> bool:
    return out.user_id in (None, c.user.id) or c.can("ai.metrics")


@router.get("/assistant")
def assistant(c: Ctx = Depends(need("ai.use"))):
    db = c.db
    st = service.status(db)
    brief = service.latest_brief(db, c.user)
    # the last good brief stays visible with its timestamp when the newest attempt failed (benchmark principle 7)
    last_q = select(AiOutput).where(AiOutput.kind == "brief")
    if c.user.location_ids:
        last_q = last_q.where(AiOutput.user_id == c.user.id)
    last_attempt = db.scalar(last_q.order_by(AiOutput.id.desc()).limit(1))
    brief_notice = None
    if brief and last_attempt and last_attempt.status != "ok" and last_attempt.id != brief.id:
        brief_notice = last_attempt.error
    mine = list(db.scalars(select(AiOutput).where(AiOutput.user_id == c.user.id, AiOutput.kind.in_(("chat", "explain"))).order_by(AiOutput.id.desc()).limit(8)))
    pending = list(db.scalars(select(AiAction).where(AiAction.status.in_(("proposed", "needs_approval"))).order_by(AiAction.id.desc()).limit(40)))
    pending = [a for a in pending if policy.check(db, c.user, a.tool, a.args).decision != "deny"][:12]
    questions = [q for q in SUGGESTED_QUESTIONS if "purchase orders" not in q or has_perm(c.user, "po.view")]
    return render(c, "assistant.html", st=st, brief=brief, brief_notice=brief_notice, brief_facts=_facts_of(brief) if brief else {}, mine=mine, pending=pending, questions=questions, tools=tools.TOOLS,
                  gate=lambda a: policy.check(db, c.user, a.tool, a.args), conversations=conversations.recent(db, c.user))


@router.post("/assistant/brief")
def brief_generate(csrf: str = Form(""), c: Ctx = Depends(need("ai.use"))):
    c.check_csrf(csrf)
    try:
        row = service.generate_brief(c.db, c.user)
    except service.AiUnavailable as e:
        return redirect("/assistant", err=str(e))
    c.db.commit()
    if row.status != "ok":
        return redirect("/assistant", err=f"The AI could not produce a brief: {row.error}")
    return redirect("/assistant", ok="New brief ready")


@router.post("/assistant/ask")
def ask(question: str = Form(""), csrf: str = Form(""), parent_id: int = Form(0), c: Ctx = Depends(need("ai.use"))):
    c.check_csrf(csrf)
    if parent_id:
        result = chat_support.respond(c, question, parent_id=parent_id)
        return redirect(f"/assistant/output/{result['id']}") if result.get("id") else redirect("/assistant", err=result.get("error", "Could not answer"))
    try:
        row = service.ask(c.db, c.user, question)
    except service.AiUnavailable as e:
        return redirect("/assistant", err=str(e))
    c.db.commit()
    return redirect(f"/assistant/output/{row.id}")


@router.get("/assistant/context")
def chat_context(url: str = "", c: Ctx = Depends(need("ai.use"))):
    try:
        return {"ok": True, "page": page_context.resolve(c.db, c.user, url)}
    except (tools.ToolError, ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "This page context is unavailable."}, status_code=400)


@router.get("/assistant/conversations")
def chat_conversations(c: Ctx = Depends(need("ai.use"))):
    return {"conversations": conversations.recent(c.db, c.user)}


@router.get("/assistant/conversations/{oid}")
def chat_conversation(oid: int, c: Ctx = Depends(need("ai.use"))):
    try:
        rows = conversations.chain(c.db, c.user, oid)
    except PermissionError:
        raise HTTPException(404, "Conversation not found")
    return {"id": oid, "page": (rows[-1].context_json or {}).get("meta", {}).get("page", {}) if rows else {}, "messages": [{"question": row.question, "id": row.id,
            "html": chat_support.answer_html(c, row) if row.status == "ok" else "",
            "error": row.status != "ok"} for row in rows]}


@router.post("/assistant/chat")
def chat(question: str = Form(""), history: str = Form(""), csrf: str = Form(""), page_url: str = Form(""),
         parent_id: int = Form(0), c: Ctx = Depends(need("ai.use"))):
    c.check_csrf(csrf)
    try:
        raw = json.loads(history[:30000]) if history else []
        turns = [t for t in raw if isinstance(t, dict)][-24:] if isinstance(raw, list) else []
    except ValueError:
        turns = []
    return JSONResponse(chat_support.respond(c, question, turns, page_url, parent_id))


@router.post("/assistant/chat-stream")
def chat_stream(question: str = Form(""), csrf: str = Form(""), page_url: str = Form(""),
                parent_id: int = Form(0), c: Ctx = Depends(need("ai.use"))):
    from queue import Queue, Empty
    from threading import Thread, Event
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from fastapi.responses import StreamingResponse
    from ..models import User
    c.check_csrf(csrf)
    if parent_id:
        try:
            conversations.chain(c.db, c.user, parent_id)
        except PermissionError:
            raise HTTPException(404, "Conversation not found")
    factory = sessionmaker(bind=c.db.get_bind())
    source_pairs, location_scope = c.db.info.get("source_pairs"), c.location_ids
    allow_unassigned_pos = c.db.info.get("allow_unassigned_pos", False)
    user_id, translate, lang = c.user.id, c.t, c.lang
    events, stopped = Queue(), Event()

    def progress(message):
        if stopped.is_set():
            raise service.AiUnavailable("Stopped.")
        events.put({"type": "progress", "message": message})

    def work():
        try:
            with factory() as db:
                db.info.update(source_pairs=source_pairs, location_scope=location_scope, allow_unassigned_pos=allow_unassigned_pos)
                user = db.get(User, user_id)
                context = SimpleNamespace(db=db, user=user, t=translate, lang=lang)
                result = chat_support.respond(context, question, None, page_url, parent_id, progress)
                events.put({"type": "result", **result})
        except Exception:
            events.put({"type": "result", "ok": False, "error": "The assistant could not finish. Please retry."})
        finally:
            events.put(None)

    def stream():
        Thread(target=work, daemon=True).start()
        try:
            while True:
                try:
                    item = events.get(timeout=10)
                except Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield "data: " + json.dumps(item) + "\n\n"
        finally:
            stopped.set()
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/replenishment/{rec_id}/explain")
def explain(rec_id: int, request: Request, csrf: str = Form(""), c: Ctx = Depends(need("ai.use"))):
    c.check_csrf(csrf)
    rec = c.db.scalar(select(Recommendation).where(Recommendation.id == rec_id).options(selectinload(Recommendation.product), selectinload(Recommendation.location)))
    if rec is None:
        raise HTTPException(404)
    c.check_loc(rec.location_id)
    try:
        row = service.explain_recommendation(c.db, c.user, rec)
    except service.AiUnavailable as e:
        if request.headers.get("x-requested-with") == "fetch":
            return JSONResponse({"ok": False, "error": c.t(str(e))})
        return redirect("/replenishment", err=str(e))
    c.db.commit()
    if request.headers.get("x-requested-with") == "fetch":
        # the drawer stays open: the explanation renders in place instead of navigating away
        if row.status != "ok":
            return JSONResponse({"ok": False, "error": f"The AI could not explain: {row.error}"})
        html = chat_support.answer_html(c, row)
        return JSONResponse({"ok": True, "html": html})
    return redirect(f"/assistant/output/{row.id}")


@router.get("/assistant/output/{oid}")
def output(oid: int, c: Ctx = Depends(need("ai.use"))):
    out = c.db.scalar(select(AiOutput).where(AiOutput.id == oid).options(selectinload(AiOutput.actions)))
    if out is None or not _visible(c, out):
        raise HTTPException(404)
    facts = {f["id"]: f for f in (out.context_json or {}).get("facts", [])}
    cited = []
    for st in validate.collect_statements(out.output_json or {}):
        for r in st.get("refs") or []:
            if r in facts and r not in cited:
                cited.append(r)
    return render(c, "ai_output.html", out=out, facts=facts, cited=cited, statements=_statements(out), gate=lambda a: policy.check(c.db, c.user, a.tool, a.args),
                  tools=tools.TOOLS, can_rate=True)


def _statements(out: AiOutput) -> list[dict]:
    o = out.output_json or {}
    return list(o.get("answer") or []) if isinstance(o.get("answer"), list) else []


def _facts_of(out: AiOutput) -> dict:
    """fact id -> fact, so citations can show what they point at (hover/tap tooltip)."""
    return {f["id"]: f for f in (out.context_json or {}).get("facts", [])}


@router.post("/assistant/action/{aid}/run")
def action_run(aid: int, note: str = Form(""), back: str = Form("/assistant"), csrf: str = Form(""), c: Ctx = Depends(need("ai.use"))):
    c.check_csrf(csrf)
    a = c.db.get(AiAction, aid)
    if a is None:
        raise HTTPException(404)
    try:
        service.run_action(c.db, c.user, a, note.strip() or None)
    except service.ActionError as e:
        c.db.commit()  # keep the failed/denied record
        return redirect(back, err=str(e))
    return redirect(back, ok=(a.result or {}).get("summary", "Done"))


@router.post("/assistant/action/{aid}/reject")
def action_reject(aid: int, back: str = Form("/assistant"), csrf: str = Form(""), c: Ctx = Depends(need("ai.use"))):
    c.check_csrf(csrf)
    a = c.db.get(AiAction, aid)
    if a is None:
        raise HTTPException(404)
    try:
        service.reject_action(c.db, c.user, a)
    except service.ActionError as e:
        return redirect(back, err=str(e))
    return redirect(back, ok="Dismissed")


@router.post("/assistant/output/{oid}/rate")
def rate(oid: int, useful: int = Form(...), note: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("ai.use"))):
    c.check_csrf(csrf)
    out = c.db.get(AiOutput, oid)
    if out is None or not _visible(c, out):
        raise HTTPException(404)
    service.rate(c.db, c.user, out, bool(useful), note.strip() or None)
    return redirect(f"/assistant/output/{oid}" if out.kind != "brief" else "/assistant", ok="Thanks, your rating helps us measure the assistant")


@router.get("/assistant/metrics")
def metrics(c: Ctx = Depends(need("ai.metrics"))):
    return render(c, "ai_metrics.html", m=service.metrics(c.db), st=service.status(c.db),
                  recent=list(c.db.scalars(select(AiOutput).order_by(AiOutput.id.desc()).limit(25))))
