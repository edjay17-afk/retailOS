"""Today page: one card per store (sales, register, out of stock, orders, checklists, issues) and the warehouse jobs, all counted from stored data."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Issue, Location, PurchaseOrder, SalesShift
from app.services import dashboard_cards
from app.timeutil import now_utc, to_local, today_local
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


def test_store_card_numbers_are_counted_not_guessed(sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    t = today_local()
    opened = to_local(now_utc()) - timedelta(hours=2)
    session.add(SalesShift(sh_id="s1", location_id=alpha.id, register="5", opened_at=opened, closed_at=None, tx_count=3, total_sales=300.0))
    session.add(Issue(location_id=alpha.id, title="Freezer", type="equipment", severity="P1", status="open", due_at=now_utc()))
    session.commit()
    labels = [f"d{i}" for i in range(14)]
    stores = [{"id": alpha.id, "name": alpha.name, "today": 150.0, "yesterday": 80.0, "series": [0.0] * 13 + [80.0], "labels": labels}]
    cards = dashboard_cards.store_cards(session, stores, t, lambda ids: {"health": {"ok": 90, "low": 3, "out": 7, "total": 100}})
    c = cards[0]
    assert c["today"] == 150.0 and c["yesterday"] == 80.0 and c["has_sales"] is True
    assert c["register"] == "5" and c["shift_open"] is True and c["out_of_stock"] == 7 and c["issues"] == 1
    none = dashboard_cards.store_cards(session, [{"id": alpha.id, "name": alpha.name, "today": 0.0, "yesterday": 0.0, "series": [0.0] * 14, "labels": labels}], t, lambda ids: {"health": {"ok": 0, "low": 0, "out": 0, "total": 0}})
    assert none[0]["has_sales"] is False        # a store with no sales at all shows "No sales data", not zero


def test_warehouse_jobs_follow_the_rights_of_the_user(sync, fake, session):
    seeded(sync, fake, session)
    jobs = dashboard_cards.warehouse_jobs(session, lambda p: p == "repl.view", (4, 128400.0))
    assert [j["key"] for j in jobs] == ["recs"] and jobs[0]["n"] == 4 and jobs[0]["value"] == 128400.0
    assert dashboard_cards.warehouse_jobs(session, lambda p: False, (4, 1.0)) == []


def test_the_page_shows_the_cards(client, sync, fake, session):
    seeded(sync, fake, session)
    add_user(session, "boss", "admin")
    login(client, "boss")
    html = client.get("/").text
    assert 'class="b-card' in html and "Alpha" in html and "Out of stock" in html and "Warehouse" in html
    assert 'id="live-store-sales"' in html and 'class="store-today"' in html     # the live refresh still finds its numbers


def test_the_stat_boxes_count_from_stored_sales(sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    t = today_local()
    labels = [f"d{i}" for i in range(14)]
    stores = [{"id": alpha.id, "name": alpha.name, "today": 0.0, "yesterday": 0.0, "series": [0.0] * 14, "labels": labels}]
    cards = dashboard_cards.store_cards(session, stores, t, lambda ids: {"health": {"ok": 1, "low": 0, "out": 2, "total": 3}})
    stat = dashboard_cards.overview(session, cards, stores, t, [{"key": "recs", "n": 4}], 5, lambda p: True)
    assert stat["out_total"] == 2 and stat["out_by_store"][0]["pct"] == 100
    assert stat["jobs_total"] == 9 and [j["key"] for j in stat["jobs"]] == ["recs", "issues"]
    assert stat["tx"] == 0 and stat["hourly"] == [] and stat["avg_basket"] == 0      # nothing sold today: no invented figures
