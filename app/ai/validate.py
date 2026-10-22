"""Checks AI output against the facts it was given. The AI may interpret numbers, but must not invent them.

Each statement is one of: fact, calculation, hypothesis, recommendation.
  * fact and calculation must cite fact ids; fact and recommendation numbers must come from the cited facts
  * a calculation may contain a derived number (shown as "derived", not as a verified fact)
  * a hypothesis is never presented as fact and is not number-checked
Anything that fails is labelled in the screen and counted in the unsupported-claim rate.
"""
from __future__ import annotations

from .context import Context, numbers_in, NUM, _NOISE

TYPES = ("fact", "calculation", "hypothesis", "recommendation", "message")  # "message": a plain conversational sentence (greeting, thanks, what I can do); it may not contain figures


def _claim_numbers(text: str) -> set[str]:
    """Numbers a statement asserts, ignoring tiny whole numbers (1 to 3) that are usually wording such as 'two stores'."""
    keep = set()
    for tok in NUM.findall(_NOISE.sub(" ", text)):
        t = tok.replace(",", "").rstrip(".")
        try:
            v = float(t)
        except ValueError:
            continue
        if v == int(v) and abs(v) <= 3:
            continue
        keep.add(f"{v:g}")
    return keep


def check_statement(st: dict, ctx: Context) -> str:
    """Returns ok | derived | unsupported_number | no_citation | bad_reference | bad_type"""
    typ = st.get("type")
    text = str(st.get("text", ""))
    refs = [r for r in (st.get("refs") or []) if isinstance(r, str)]
    if typ not in TYPES:
        return "bad_type"
    if any(ctx.fact(r) is None for r in refs):
        return "bad_reference"
    if typ in ("fact", "calculation") and not refs:
        return "no_citation"
    if typ == "hypothesis":
        return "ok"
    if typ == "message":
        return "unsupported_number" if _claim_numbers(text) else "ok"
    allowed: set[str] = set()
    for r in refs:
        allowed |= ctx.fact(r).nums
    if not refs:
        allowed = ctx.all_numbers()
    claimed = _claim_numbers(text)
    missing = {n for n in claimed if n not in allowed}
    if missing:
        return "derived" if typ == "calculation" else "unsupported_number"
    return "ok"


def validate(statements: list[dict], ctx: Context) -> dict:
    flags = []
    n = bad = derived = 0
    for i, st in enumerate(statements):
        res = check_statement(st, ctx)
        st["check"] = res
        n += 1
        if res == "derived":
            derived += 1
        elif res != "ok":
            bad += 1
            flags.append({"index": i, "text": str(st.get("text", ""))[:160], "problem": res})
    return {"statements": n, "unsupported": bad, "derived": derived, "unsupported_rate": (bad / n) if n else 0.0, "verified": bad == 0, "flags": flags}


def collect_statements(output: dict) -> list[dict]:
    out: list[dict] = []
    for item in output.get("items", []) or []:
        out.extend(s for s in item.get("statements", []) or [] if isinstance(s, dict))
    ans = output.get("answer")
    if isinstance(ans, list):
        out.extend(s for s in ans if isinstance(s, dict))
    table = output.get("table")
    if isinstance(table, dict):
        out.extend(row for row in table.get("rows", []) if isinstance(row, dict))
    return out
