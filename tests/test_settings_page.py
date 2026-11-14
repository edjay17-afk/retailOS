"""The redesigned /settings page: grouped typed controls, validation per control type, reset to default, audit trail."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import settings_service as cfg
from app.models import AuditEvent
from tests.test_web import PW, add_user, csrf_of, login


@pytest.fixture()
def client(cfg):
    from app.web.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def post(client, path, data):
    return client.post(path, data=data)


def test_page_groups_every_setting_and_shows_typed_controls(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    html = client.get("/settings").text
    assert html.count('class="card setgroup') >= 8
    for key in cfg.DEFAULTS:                       # every setting is on the page exactly once as a hidden key field
        assert html.count(f'name="key" value="{key}"') >= 1, key
    assert 'name="value" value="1"' in html        # boolean toggle
    assert 'name="p0_ack"' in html and 'name="p3_due"' in html   # sla grid
    assert 'data-confirm=' in html                 # dangerous settings are confirmed
    assert 'id="settings-filter"' in html


def test_bool_toggle_saves_checkbox_state(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    tok = csrf_of(client, "/settings")
    assert "ok=" in post(client, "/settings/value", {"key": "ai.enabled", "csrf": tok}).headers["location"]  # unchecked: no value field
    assert cfg.get(session, "ai.enabled") is False
    assert "ok=" in post(client, "/settings/value", {"key": "ai.enabled", "value": "1", "csrf": tok}).headers["location"]
    assert cfg.get(session, "ai.enabled") is True


def test_select_and_range_validation(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    tok = csrf_of(client, "/settings")
    r = post(client, "/settings/value", {"key": "repl.mode", "value": "everything", "csrf": tok})
    assert "err=" in r.headers["location"]
    assert "ok=" in post(client, "/settings/value", {"key": "repl.mode", "value": "sales_only", "csrf": tok}).headers["location"]
    assert cfg.get(session, "repl.mode") == "sales_only"
    assert "err=" in post(client, "/settings/value", {"key": "po.stale_days", "value": "0", "csrf": tok}).headers["location"]   # below min
    assert cfg.get(session, "po.stale_days") == 7


def test_lines_and_int_lists(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    tok = csrf_of(client, "/settings")
    assert "ok=" in post(client, "/settings/value", {"key": "repl.reason_codes", "value": "One\n\nTwo\n", "csrf": tok}).headers["location"]
    assert cfg.get(session, "repl.reason_codes") == ["One", "Two"]
    assert "err=" in post(client, "/settings/value", {"key": "repl.reason_codes", "value": "  \n ", "csrf": tok}).headers["location"]
    assert "ok=" in post(client, "/settings/value", {"key": "expiry.warn_days", "value": "30\n14\n3", "csrf": tok}).headers["location"]
    assert cfg.get(session, "expiry.warn_days") == [30, 14, 3]
    assert "err=" in post(client, "/settings/value", {"key": "expiry.warn_days", "value": "soon\n3", "csrf": tok}).headers["location"]


def test_sla_grid_saves_a_dict_and_is_audited(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    tok = csrf_of(client, "/settings")
    fields = {"key": "issue.sla", "csrf": tok, "p0_ack": "5", "p0_due": "2", "p1_ack": "30", "p1_due": "24",
              "p2_ack": "240", "p2_due": "72", "p3_ack": "1440", "p3_due": "168"}
    assert "ok=" in post(client, "/settings/value", fields).headers["location"]
    assert cfg.get(session, "issue.sla")["P0"] == [5, 2]
    session.expire_all()
    ev = session.scalars(select(AuditEvent).where(AuditEvent.action == "setting.update", AuditEvent.entity_id == "issue.sla")).all()
    assert ev and ev[-1].after == {"value": {"P0": [5, 2], "P1": [30, 24], "P2": [240, 72], "P3": [1440, 168]}}
    bad = dict(fields, p0_ack="-1")
    assert "err=" in post(client, "/settings/value", bad).headers["location"]


def test_reset_restores_default_and_is_audited(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    tok = csrf_of(client, "/settings")
    assert "ok=" in post(client, "/settings/value", {"key": "po.stale_days", "value": "12", "csrf": tok}).headers["location"]
    html = client.get("/settings").text
    assert "changed from default" in html and "Last changed by" in html and 'value="12"' in html
    assert "ok=" in post(client, "/settings/reset", {"key": "po.stale_days", "csrf": tok}).headers["location"]
    assert cfg.get(session, "po.stale_days") == 7
    session.expire_all()
    ev = session.scalars(select(AuditEvent).where(AuditEvent.action == "setting.reset")).all()
    assert len(ev) == 1 and ev[0].after == {"value": 7} and ev[0].before == {"value": 12}
    html = client.get("/settings").text
    assert "changed from default" not in html
