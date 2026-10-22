"""Turn an internal page URL into a bounded, permission-checked chat scope."""
import re
import json
from urllib.parse import urlsplit, parse_qsl, urlencode

from ..models import Location, Product, Supplier
from ..security import has_perm, allowed_location_ids
from . import report_tools

PAGES = {
    "/sales/products": ("Sales by product", "sales.view", ["Explain sales for these filters", "Which products lead these sales?"]),
    "/sales/product": ("Product sales", "sales.view", ["Explain this product's sales", "Show its daily sales"]),
    "/sales": ("Sales", "sales.view", ["Summarize sales for this period", "Compare suppliers for this period"]),
    "/inventory": ("Stock", "inventory.view", ["Which products are running low here?", "Find negative stock here"]),
    "/replenishment": ("Auto replenishment", "repl.view", ["Explain the replenishment suggestions for this store", "Which products should I review first?"]),
    "/transfer-sheets": ("Transfer sheets", "transfer.sheet", ["Summarize this transfer sheet", "What should I check before saving?"]),
    "/counts": ("Stock counts", "count.enter", ["Show the count differences", "Which counts need attention?"]),
    "/expiry": ("Expiry dates", "expiry.view", ["Which batches expire soon?", "What should I handle first?"]),
    "/purchase-orders": ("Purchase orders", "po.view", ["Which purchase orders are overdue?", "What stock is still due to arrive?"]),
    "/inspections": ("Store inspections", "inspection.do", ["Check today's inspection reports in Lark", "Which inspections need attention?"]),
}


def resolve(db, user, url):
    from .tools import ToolError
    parsed = urlsplit(str(url or "")[:3000])
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return {}
    path = parsed.path.rstrip("/") or "/"
    match = re.fullmatch(r"(/sales/product|/transfer-sheets|/counts)/(\d+)", path)
    base = match[1] if match else path
    if base not in PAGES:
        return {}
    title, permission, suggestions = PAGES[base]
    if not has_perm(user, permission):
        raise ToolError("This page is outside your access.")
    params = {k: v[:150] for k, v in parse_qsl(parsed.query) if k in {"loc", "days", "frm", "to", "supplier", "category", "q", "sort"}}
    chips = [title]
    if params.get("loc") not in (None, "", "0"):
        loc = db.get(Location, int(params["loc"]))
        if not loc or loc.id not in allowed_location_ids(db, user):
            raise ToolError("That store is outside your access.")
        chips.append(loc.name)
    else:
        chips.append("All accessible stores")
    args = dict(params)
    if base.startswith("/sales"):
        if params.get("days") != "custom":
            args.pop("frm", None); args.pop("to", None)
            args.setdefault("days", "30" if base == "/sales/product" else "14")
        start, end = report_tools.period(args)
        args.update(frm=start.isoformat(), to=end.isoformat())
        chips.append(f"{start} to {end}")
        params.update(days="custom", frm=start.isoformat(), to=end.isoformat())
    if params.get("supplier") not in (None, "", "0"):
        supplier = db.get(Supplier, int(params["supplier"]))
        if not supplier:
            raise ToolError("That supplier no longer exists.")
        chips.append(supplier.name)
    for key in ("category", "q"):
        if params.get(key):
            chips.append(f"{key}: {params[key]}")
    if match:
        args[{"/sales/product": "product_id", "/counts": "count_id", "/transfer-sheets": "sheet_id"}[base]] = int(match[2])
        if base == "/sales/product":
            product = db.get(Product, int(match[2]))
            if not product:
                raise ToolError("That product no longer exists.")
            chips.append(product.name)
        else:
            chips.append(f"#{match[2]}")
    return {"path": path, "base": base, "url": path + ("?" + urlencode(params) if params else ""), "label": title,
            "chips": chips, "args": args, "suggestions": suggestions}


def add_facts(db, user, ctx, page):
    if not page:
        return
    ctx.meta["page"] = page
    ctx.add("page_scope", "Selected page scope: " + "; ".join(page["chips"]) + ". Use this scope unless the person explicitly changes it. Lookup arguments: " + json.dumps(page["args"]), page["url"])
    lookup = {"/sales": report_tools.sales_history, "/sales/products": report_tools.product_sales, "/sales/product": report_tools.product_sales,
              "/transfer-sheets": report_tools.transfers, "/counts": report_tools.counts, "/expiry": report_tools.expiry}.get(page["base"])
    if lookup:
        for fact in lookup(db, user, page["args"])["facts"]:
            ctx.add("selected_report", fact, page["url"])
