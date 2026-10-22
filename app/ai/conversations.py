"""Conversation chains use existing audited AI outputs, scoped strictly to their owner."""
from sqlalchemy import select
from ..models import AiOutput


def chain(db, user, parent_id):
    result, seen = [], set()
    while parent_id and len(result) < 12:
        if parent_id in seen:
            break
        seen.add(parent_id)
        row = db.get(AiOutput, int(parent_id))
        if row is None or row.user_id != user.id or row.kind != "chat":
            raise PermissionError("Conversation not found.")
        result.append(row)
        parent_id = (row.context_json or {}).get("meta", {}).get("conversation", {}).get("parent_id")
    return list(reversed(result))


def memory(rows):
    turns = []
    for row in rows:
        page = (row.context_json or {}).get("meta", {}).get("page", {})
        turns.append({"role": "user", "text": (row.question or "") + (" [Page scope: " + "; ".join(page.get("chips", [])) + "]" if page else "")})
        answer = " ".join(str(s.get("text", "")) for s in (row.output_json or {}).get("answer", []) if isinstance(s, dict))
        table = (row.output_json or {}).get("table") or {}
        if table.get("rows"):
            answer += " Table: " + "; ".join(" | ".join(r.get("cells", [])) for r in table["rows"])
        if answer:
            turns.append({"role": "assistant", "text": answer[:3000]})
    return turns


def recent(db, user):
    rows = db.scalars(select(AiOutput).where(AiOutput.user_id == user.id, AiOutput.kind == "chat").order_by(AiOutput.id.desc()).limit(150))
    seen, out = set(), []
    for row in rows:
        meta = (row.context_json or {}).get("meta", {}).get("conversation", {})
        root = meta.get("root_id") or row.id
        if root in seen:
            continue
        seen.add(root)
        out.append({"id": row.id, "title": meta.get("title") or row.question or "Conversation", "created_at": row.created_at.isoformat(), "status": row.status})
        if len(out) == 30:
            break
    return out
