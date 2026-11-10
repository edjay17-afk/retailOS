"""Sync > Run now: 'all' plus a step list that shows each job waiting, running, done or failed."""
from app import jobs
from app.services import run_now
from app.web import routes_core
from tests.test_web import add_user, client, csrf_of, login  # noqa: F401


def _fake(name, ok=True):
    def fn():
        jobs.LAST[name] = {"ok": ok, "error": "" if ok else "StoreHub timed out"}
    fn.__name__ = name
    return fn


def test_all_runs_every_sync_and_reports_each_step(client, session, monkeypatch):
    fakes = {k: _fake("fake_" + k, ok=(k != "sales")) for k in run_now.ALL}
    monkeypatch.setattr(routes_core, "JOB_ACTIONS", fakes)
    monkeypatch.setattr(run_now, "_runs", {})
    add_user(session, "sync_admin", "admin"); login(client, "sync_admin")
    csrf = csrf_of(client, "/sync")
    r = client.post("/sync/run", data={"csrf": csrf, "job": "all"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and [s["key"] for s in r.json()["steps"]] == list(run_now.ALL)
    state = client.get("/sync/run/" + r.json()["token"]).json()
    assert state["done"] and not state["ok"]
    by = {s["key"]: s for s in state["steps"]}
    assert by["products"]["status"] == "ok" and by["sales"]["status"] == "failed" and by["sales"]["error"] == "StoreHub timed out"
    assert client.get("/sync/run/nope").status_code == 404


def test_one_job_and_an_unknown_job(client, session, monkeypatch):
    monkeypatch.setattr(routes_core, "JOB_ACTIONS", {"sales": _fake("fake_sales_one")})
    monkeypatch.setattr(run_now, "_runs", {})
    add_user(session, "sync_admin2", "admin"); login(client, "sync_admin2")
    csrf = csrf_of(client, "/sync")
    r = client.post("/sync/run", data={"csrf": csrf, "job": "sales"}, headers={"X-Requested-With": "fetch"})
    assert [s["key"] for s in r.json()["steps"]] == ["sales"] and client.get("/sync/run/" + r.json()["token"]).json()["ok"] is True
    assert client.post("/sync/run", data={"csrf": csrf, "job": "nonsense"}, headers={"X-Requested-With": "fetch"}).status_code == 400
    assert 'value="all"' in client.get("/sync").text and 'id="run-panel"' in client.get("/sync").text
