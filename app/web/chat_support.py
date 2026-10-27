"""Shared chat request processing and safe HTML rendering."""
from fastapi import HTTPException
from ..ai import conversations, page_context, service, tools, validate
from ..models import Location, Product
from ..security import allowed_location_ids
from .deps import templates


def answer_html(c, row):
    output = row.output_json or {}
    facts = {f["id"]: f for f in (row.context_json or {}).get("facts", [])}
    page = (row.context_json or {}).get("meta", {}).get("page", {})
    cited = {ref for st in validate.collect_statements(output) for ref in st.get("refs", []) if isinstance(ref, str)}
    previews = []
    for action in row.actions:
        if action.tool != "prepare_transfer_sheet":
            previews.append({"title": action.tool.replace("_", " ").capitalize(), "description": action.why or "", "lines": []})
            continue
        args = action.args or {}
        try:
            loc = c.db.get(Location, int(args.get("location", 0)))
            if not loc or loc.id not in allowed_location_ids(c.db, c.user):
                continue
            lines = []
            for line in args.get("lines", [])[:50]:
                product = c.db.get(Product, int(line.get("product_id", 0)))
                lines.append({"product": product.name if product else "Unknown product", "qty": str(line.get("qty", ""))})
            previews.append({"title": "Transfer draft to " + loc.name, "description": "After approval, quantities are added to the destination's draft. Nothing is submitted to StoreHub.", "lines": lines})
        except (ValueError, TypeError, AttributeError):
            continue
    return templates.get_template("_chat_reply.html").render(
        t=c.t, lang=c.lang, statements=output.get("answer", []), out=row, n_actions=len(row.actions),
        facts=facts, cited_facts={k: v for k, v in facts.items() if k in cited}, previews=previews, table=output.get("table"), page=page,
        follow_ups=(output.get("follow_ups") or [])[:3])


def respond(c, question, history=None, page_url="", parent_id=0, progress=None):
    try:
        previous = conversations.chain(c.db, c.user, parent_id) if parent_id else []
        if not page_url and previous:
            page_url = (previous[-1].context_json or {}).get("meta", {}).get("page", {}).get("url", "")
        page = page_context.resolve(c.db, c.user, page_url)
    except PermissionError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (tools.ToolError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    if page:
        page["pos_sources"] = sorted([list(pair) for pair in c.db.info.get("source_pairs", [])])
        page["source_note"] = "Data is restricted to the selected POS accounts and authorized stores. Loyverse purchase orders/transfers/timesheets are unavailable; replenishment remains sales-only."
    meta = {"parent_id": parent_id or None, "title": question[:100]}
    if previous:
        old = (previous[-1].context_json or {}).get("meta", {}).get("conversation", {})
        meta.update(root_id=old.get("root_id") or previous[0].id, title=old.get("title") or previous[0].question)
        history = conversations.memory(previous)
    try:
        row = service.ask(c.db, c.user, question, history, page=page, conversation=meta, progress=progress)
    except (service.AiUnavailable, tools.ToolError, ValueError, TypeError) as exc:
        c.db.rollback()
        return {"ok": False, "error": c.t(str(exc))}
    c.db.commit()
    if row.status != "ok":
        return {"ok": False, "error": "The AI could not answer. Please retry or open the Assistant page.", "id": row.id}
    return {"ok": True, "html": answer_html(c, row), "id": row.id,
            "text": " ".join(str(s.get("text", "")) for s in (row.output_json or {}).get("answer", []) if isinstance(s, dict))[:6000],
            "page": page}
