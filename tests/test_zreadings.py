"""Official Z-readings: GET-only StoreHub reads, idempotent local copies, and access-controlled report pages."""
import inspect
from datetime import date, timedelta

from sqlalchemy import inspect as sql_inspect, select

from app import robot
from app.db import get_engine
from app.models import Location, ZReading
from app.services import zreadings
from app.timeutil import today_local
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


DETAIL = {
    "sections": [
        {"title": "General", "tables": [{"rows": [["Beg. SI #:", "00400000702"], ["End. SI #:", "00400000711"]]}]},
        {"title": "Collections Summary", "tables": [{"rows": [["Payment Method", "Transactions", "Amount (PHP)"], ["Cash", "10", "769.00"]]}]},
    ],
    "source_text": "FULL OFFICIAL SOURCE TEXT",
}


def row(sid, closed, store, z_count="4", register="5", tx="10", gross="769.00"):
    return {"0": closed, "1": z_count, "2": store, "3": register, "4": tx, "5": gross, "DT_RowId": sid}


def test_zreading_schema_is_created(cfg):
    tables = sql_inspect(get_engine()).get_table_names()
    assert "z_reading" in tables
    cols = {c["name"] for c in sql_inspect(get_engine()).get_columns("z_reading")}
    assert {"sh_id", "close_at", "z_count", "detail_json", "source_text"} <= cols


def test_reports_are_saved_updated_and_never_duplicated(sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A")).name
    old_day = today_local() - timedelta(days=10)
    calls = []

    def fetch(start, end, skip, per_run):
        calls.append((start, end, set(skip), per_run))
        rows = [row("z1", f"{old_day:%m/%d/%Y} 23:59", alpha), row("other", f"{old_day:%m/%d/%Y} 23:59", "Some Other Shop")]
        return rows, {"z1": DETAIL}

    out = zreadings.run(session, fetch=fetch)
    assert out["ok"] and out["listed"] == 2 and out["new"] == 1 and out["detailed"] == 1
    saved = session.scalar(select(ZReading).where(ZReading.sh_id == "z1"))
    assert saved.z_count == 4 and saved.register == "5" and saved.tx_count == 10 and saved.gross_sales == 769.0
    assert saved.detail_json == DETAIL["sections"] and saved.source_text == DETAIL["source_text"] and saved.detail_ok
    assert zreadings.pending(session) == 0

    out = zreadings.run(session, fetch=fetch)
    assert out["new"] == 0 and out["updated"] == 1
    assert calls[-1][2] == {"z1"}  # an old, fully read official report is not downloaded again
    assert session.query(ZReading).count() == 1


def test_missing_detail_stays_pending_until_read(sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A")).name
    stamp = f"{today_local():%m/%d/%Y} 23:59"
    zreadings.apply(session, [row("pending", stamp, alpha)], {})
    assert zreadings.pending(session) == 1
    saved = session.scalar(select(ZReading).where(ZReading.sh_id == "pending"))
    assert not saved.detail_ok and saved.detail_json is None
    zreadings.apply(session, [row("pending", stamp, alpha)], {"pending": {"sections": [], "source_text": "error page"}})
    session.expire_all()
    saved = session.scalar(select(ZReading).where(ZReading.sh_id == "pending"))
    assert not saved.detail_ok and zreadings.pending(session) == 1  # an error/empty page is not accepted as the official detail
    zreadings.apply(session, [row("pending", stamp, alpha)], {"pending": DETAIL})
    session.expire_all()
    saved = session.scalar(select(ZReading).where(ZReading.sh_id == "pending"))
    assert saved.detail_ok and saved.detail_json[0]["title"] == "General" and zreadings.pending(session) == 0


class FakeReadOnlyPage:
    """Only read methods exist. Any click/fill/select/submit in the robot would raise AttributeError."""

    def __init__(self, rows):
        self.rows = rows
        self.gotos = []
        self.evals = []
        self.waits = []

    def goto(self, url, **kwargs):
        self.gotos.append((url, kwargs))

    def wait_for_load_state(self, state, **kwargs):
        self.waits.append((state, kwargs))

    def wait_for_selector(self, selector, **kwargs):
        self.waits.append((selector, kwargs))

    def evaluate(self, script, argument=None):
        self.evals.append((script, argument))
        if argument and "ajaxZreadings" in argument:
            assert 'method: "GET"' in script
            return {"aaData": self.rows}
        assert argument is None
        return DETAIL["sections"]

    def inner_text(self, selector):
        assert selector == "body"
        return DETAIL["source_text"]


def test_robot_helper_uses_only_get_navigation_and_get_fetch():
    rows = [row("z1", "10/25/2026 23:59", "The Daily Centro - Alpha"), row("z2", "10/24/2026 23:59", "The Daily Centro - Alpha")]
    page = FakeReadOnlyPage(rows)
    listed, details = robot._read_zreadings(page, "https://example.test", date(2026, 10, 1), date(2026, 10, 26), {"z2"}, 40)
    assert listed == rows and details == {"z1": DETAIL}
    assert [u for u, _ in page.gotos] == ["https://example.test/reports/bir/zreadings", "https://example.test/reports/bir/zreading/id=z1"]
    assert all(kw.get("wait_until") == "domcontentloaded" for _, kw in page.gotos)
    assert len(page.evals) == 2 and "ajaxZreadings" in page.evals[0][1]

    source = inspect.getsource(robot._read_zreadings)
    for forbidden in (".click(", ".fill(", ".check(", ".select_option(", ".press(", ".submit("):
        assert forbidden not in source
    assert 'method: "GET"' in robot._GET_JSON_JS


def test_report_pages_csv_and_access_control(client, sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    beta = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    stamp = f"{today_local():%m/%d/%Y} 23:59"
    zreadings.apply(session, [row("z1", stamp, alpha.name)], {"z1": DETAIL})
    session.commit()
    add_user(session, "boss", "admin")
    login(client, "boss")
    page = client.get(f"/sales/zreadings?loc={alpha.id}&days=7")
    assert page.status_code == 200 and "Z-Reading Reports" in page.text and "769.00" in page.text and "Detail pending" not in page.text
    assert 'class="drp"' in page.text and "/static/daterange.js" in page.text and "/static/daterange.css" in page.text
    assert 'name="days" value="7"' in page.text
    assert f'data-from="{today_local() - timedelta(days=6)}" data-to="{today_local()}" data-today="{today_local()}"' in page.text
    custom = client.get(f"/sales/zreadings?loc={alpha.id}&days=custom&frm={today_local()}&to={today_local()}")
    assert custom.status_code == 200 and 'name="days" value="custom"' in custom.text and "769.00" in custom.text
    assert "Z-Reading Reports" in client.get("/sales").text
    detail = client.get("/sales/zreadings/z1")
    assert detail.status_code == 200 and "Beg. SI #:" in detail.text and "00400000702" in detail.text and "Cash" in detail.text
    csv = client.get(f"/sales/zreadings?loc={alpha.id}&days=7&export=csv")
    assert csv.status_code == 200 and "z1" in csv.text and "769.00" in csv.text and "z-readings" in csv.headers["content-disposition"]

    add_user(session, "staff", "staff")
    login(client, "staff")
    assert client.get("/sales/zreadings").status_code == 403
    assert client.get("/sales/zreadings/z1").status_code == 403

    add_user(session, "scoped", "admin", locs=[beta.id])
    login(client, "scoped")
    assert client.get(f"/sales/zreadings?loc={alpha.id}").status_code == 403
    assert client.get("/sales/zreadings/z1").status_code == 403


def test_report_pages_need_a_sign_in(client):
    response = client.get("/sales/zreadings")
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
