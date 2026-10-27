"""An interim Z-report (a shift closed mid-day) is already inside the day's final report, so it is shown but not counted twice."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Location, ZReading
from app.services import zreadings
from app.timeutil import today_local
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


def _saved(session, store, closed, z_count, tx, gross, begin_si, sid):
    z = ZReading(sh_id=sid, location_id=store.id, close_at=closed, z_count=z_count, register="5", tx_count=tx, gross_sales=gross, detail_ok=True,
                 source_text=f"General\nBeg. SI #:\t{begin_si}\nEnd. SI #:\t00400000899")
    session.add(z); session.flush()
    return z


def test_the_interim_report_is_not_counted_twice(sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    d = today_local() - timedelta(days=2)
    day = lambda h, m: __import__("datetime").datetime.combine(d, __import__("datetime").time(h, m))
    early = _saved(session, alpha, day(5, 51), 4, 10, 769.0, "00400000702", "early")
    final = _saved(session, alpha, day(23, 59), 4, 198, 19864.0, "00400000702", "final")
    other = _saved(session, alpha, day(23, 59) - timedelta(days=1), 3, 264, 28870.0, "00400000438", "prev")
    rows = zreadings.readings(session, d - timedelta(days=1), d, [alpha.id])
    replaced = zreadings.interim(session, rows)
    assert set(replaced) == {early.id} and replaced[early.id].id == final.id
    t = zreadings.totals(rows, set(replaced))
    assert t == {"reports": 2, "tx": 198 + 264, "sales": round(19864.0 + 28870.0, 2), "interim": 1}


def test_a_report_alone_or_with_a_different_first_invoice_is_never_dropped(sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    import datetime as dt
    d = today_local() - timedelta(days=3)
    a = _saved(session, alpha, dt.datetime.combine(d, dt.time(5, 51)), 7, 10, 769.0, "00400000702", "a")
    _saved(session, alpha, dt.datetime.combine(d, dt.time(23, 59)), 7, 50, 5000.0, "00400000999", "b")     # a different first invoice: not a superset, keep both
    rows = zreadings.readings(session, d, d, [alpha.id])
    assert zreadings.interim(session, rows) == {}
    assert zreadings.totals(rows)["reports"] == 2


def test_the_page_marks_the_interim_report_and_leaves_it_out_of_the_total(client, sync, fake, session):
    import datetime as dt
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    d = today_local() - timedelta(days=2)
    _saved(session, alpha, dt.datetime.combine(d, dt.time(5, 51)), 4, 10, 769.0, "00400000702", "early")
    _saved(session, alpha, dt.datetime.combine(d, dt.time(23, 59)), 4, 198, 19864.0, "00400000702", "final")
    session.commit()
    add_user(session, "boss", "admin"); login(client, "boss")
    html = client.get(f"/sales/zreadings?loc={alpha.id}&days=7").text
    assert "Interim, included in the report closed 23:59" in html
    assert "19,864.00" in html and "20,633.00" not in html                    # 19,864 + 769 would be the double count
    csv = client.get(f"/sales/zreadings?loc={alpha.id}&days=7&export=csv").text
    assert "Interim: included in the report closed" in csv
