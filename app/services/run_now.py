"""'Run now' on the Sync page: one job or all of them, with a step list the page can poll (waiting, running, done, failed)."""
from __future__ import annotations

import threading
import time
import uuid

from .. import jobs

ALL = ("reference", "products", "inventory", "sales", "purchase_orders", "people", "dq")   # every data sync, then the data checks
_runs: dict[str, dict] = {}
_lock = threading.Lock()


NAMES = {"reference": "Stores and staff", "products": "Products", "inventory": "Stock levels", "sales": "Sales", "purchase_orders": "Purchase orders",
         "people": "Timesheets", "dq": "Data checks", "replenish": "Order suggestions", "reconcile": "Reconcile"}   # what the pop-up shows (translated by the page)


def plan(job: str) -> list[str]:
    return list(ALL) if job == "all" else [job]


def start(job: str, labels: dict[str, str]) -> tuple[str, bool]:
    """Register a run. Returns (token, created): if a run is still going, that one is returned instead of starting a second."""
    with _lock:
        for t, r in _runs.items():
            if not r["done"]:
                return t, False
        for t in [t for t, r in _runs.items() if time.time() - r["started"] > 3600]:
            _runs.pop(t, None)
        token = uuid.uuid4().hex[:12]
        _runs[token] = {"started": time.time(), "done": False, "job": job,
                        "steps": [{"key": k, "label": labels.get(k, k), "status": "waiting", "error": "", "seconds": None} for k in plan(job)]}
        return token, True


def state(token: str) -> dict | None:
    with _lock:
        r = _runs.get(token)
        if r is None:
            return None
        return {"token": token, "done": r["done"], "job": r["job"], "elapsed": int(time.time() - r["started"]), "steps": [dict(s) for s in r["steps"]],
                "ok": r["done"] and all(s["status"] == "ok" for s in r["steps"])}


def execute(token: str, actions: dict) -> None:
    """Runs in the background: each step in turn, noting how it ended."""
    r = _runs.get(token)
    if r is None:
        return
    for step in r["steps"]:
        fn = actions.get(step["key"])
        step["status"] = "running"
        began = time.time()
        if fn is None:
            step["status"], step["error"] = "failed", "Unknown job"
            continue
        try:
            fn()
            last = jobs.LAST.get(getattr(fn, "__name__", ""), {"ok": True, "error": ""})
            step["status"], step["error"] = ("ok", "") if last.get("ok", True) else ("failed", last.get("error", ""))
        except Exception as e:  # noqa: BLE001
            step["status"], step["error"] = "failed", (str(e) or type(e).__name__)[:160]
        step["seconds"] = round(time.time() - began, 1)
    r["done"] = True
