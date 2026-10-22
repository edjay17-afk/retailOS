"""Policy gate: runs before any write action proposed by the AI (spec section 13.2).

Checks the person's permission, the action's risk class, the amount against approval limits, store scope, data freshness and the AI's
confidence. The result is one of: execute (allowed now), needs_approval (a person must approve), deny (not allowed, with reasons).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import settings_service as cfg
from ..models import Recommendation, User
from ..security import allowed_location_ids, has_perm
from ..services import freshness
from .tools import TOOLS, Tool, check_args

STOCK_TOOLS = {"prepare_transfer_sheet", "approve_replenishment", "create_count_list", "write_off_stock", "submit_supplier_order"}


@dataclass
class Gate:
    decision: str  # execute | needs_approval | deny
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"decision": self.decision, "reasons": self.reasons}


def check(db: Session, user: User, tool_name: str, args: dict, *, confidence: float | None = None, automatic: bool = False) -> Gate:
    """automatic=True means the AI itself wants to run it with no person clicking; only low-risk actions may ever do that."""
    tool: Tool | None = TOOLS.get(tool_name)
    if tool is None or tool.risk == "read":
        return Gate("deny", ["unknown or non-action tool"])
    problem = check_args(tool, args)
    if problem:
        return Gate("deny", [f"the proposal is incomplete: {problem}"])
    reasons: list[str] = []
    if not has_perm(user, tool.perm):
        return Gate("deny", [f"your role is not allowed to do this ({tool.perm})"])
    # store scope
    for key in ("location", "location_id"):
        if args.get(key) not in (None, ""):
            ids = allowed_location_ids(db, user)
            ref = str(args[key])
            if ref.isdigit() and int(ref) not in ids:
                return Gate("deny", ["that location is outside your access"])
    if tool_name == "approve_replenishment":
        rec = db.get(Recommendation, int(args["rec_id"]))
        if rec is None:
            return Gate("deny", ["suggestion not found"])
        if rec.location_id not in allowed_location_ids(db, user):
            return Gate("deny", ["that suggestion belongs to a location outside your access"])
        value = (rec.final_qty or 0) * (rec.unit_cost or 0)
        limit = float(cfg.get(db, "repl.approval_limit_manager"))
        if value > limit and user.role == "store_manager":
            return Gate("deny", [f"order value {value:,.2f} is above your approval limit of {limit:,.2f}; management must approve"])
        if rec.status == "needs_review":
            reasons.append("the suggestion needs review because of a data problem; the approver must write what they checked")
    # data freshness
    if tool_name in STOCK_TOOLS:
        stale = freshness.blocking_domains(db)
        if stale:
            return Gate("deny", ["data is stale: " + ", ".join(f"{s['label']} ({s['status']})" for s in stale)])
    min_conf = float(cfg.get(db, "ai.min_confidence"))
    if confidence is not None and confidence < min_conf:
        reasons.append(f"AI confidence {confidence:.2f} is below {min_conf:.2f}")
        return Gate("needs_approval", reasons)
    if tool.risk == "high":
        return Gate("needs_approval", reasons + ["high-risk actions always need a person's approval"])
    if automatic:
        if not cfg.get(db, "ai.auto_low_risk"):
            return Gate("needs_approval", reasons + ["automatic low-risk actions are switched off"])
        return _auto_gate(db, tool_name, reasons)
    return Gate("execute", reasons)  # a person is clicking Run on a low-risk action they are allowed to do


def _auto_gate(db: Session, tool_name: str, reasons: list[str]) -> Gate:
    """Autonomy is earned per decision class: risk class and amount limits above, plus enough decided history and a
    proven acceptance rate here (benchmark principle 5). The class must earn automation; it is never the default."""
    from sqlalchemy import func, select
    from ..models import AiAction
    min_n = int(cfg.get(db, "ai.auto_min_samples"))
    min_rate = float(cfg.get(db, "ai.auto_min_acceptance"))
    rows = db.execute(select(AiAction.status, func.count()).where(AiAction.tool == tool_name).group_by(AiAction.status)).all()
    n = {s: c for s, c in rows}
    decided = n.get("executed", 0) + n.get("rejected", 0)
    if decided < min_n:
        return Gate("needs_approval", reasons + [f"automatic {tool_name.replace('_', ' ')} needs a track record first ({decided} of {min_n} decided)"])
    rate = n.get("executed", 0) / decided
    if rate < min_rate:
        return Gate("needs_approval", reasons + [f"automatic {tool_name.replace('_', ' ')} has an acceptance rate of {rate:.0%}, below the required {min_rate:.0%}"])
    return Gate("execute", reasons)
