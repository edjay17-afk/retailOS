"""V2.1 MUST scope acceptance tests: MUST 2 (transfers), MUST 3 (cycle counts), MUST 4 (repair queue), audit integrity."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import audit
from app import settings_service as cfgsvc
from app.models import AuditEvent, InventoryChange, InventoryEvent, Issue, Location, Product, Recommendation
from app.services import counts, replenishment, transfers, v21_metrics
from app.timeutil import now_utc
from tests.test_services import _transfer_batch, mkuser, seeded
from tests.test_web import add_user, client, login  # noqa: F401


# ------------------------------------------------------------------------------- MUST 2
def test_at_trf_01_dispatch_records_actor_time_quantity(sync, fake, session):
    boss, rec, batch = _transfer_batch(sync, fake, session)
    wh = mkuser(session, "wh", "warehouse")
    replenishment.mark_entered(session, batch, boss, None)
    transfers.dispatch(session, wh, batch); session.commit()
    e = session.scalar(select(InventoryEvent).where(InventoryEvent.event_type == "transfer_dispatch"))
    assert (e.actor_user_id, e.quantity, e.direction, e.related_transfer_id) == (wh.id, rec.approved_qty, "-", batch.id)
    assert e.occurred_at == batch.dispatched_at and e.reconciliation_status == "pending"


def test_at_trf_02_transfer_cannot_close_without_store_confirmation(sync, fake, session):
    boss, rec, batch = _transfer_batch(sync, fake, session)
    wh = mkuser(session, "wh", "warehouse")
    replenishment.mark_entered(session, batch, boss, 9999)  # even with a StoreHub number on it
    transfers.dispatch(session, wh, batch); session.commit()
    replenishment.sync_batches_with_pos(session); session.commit()
    assert batch.status == "dispatched"  # a StoreHub PO status never closes a transfer; only the store count does


def test_at_trf_03_difference_opens_issue_and_events_settle(sync, fake, session):
    boss, rec, batch = _transfer_batch(sync, fake, session)
    wh = mkuser(session, "wh", "warehouse"); store = mkuser(session, "stf", "staff")
    replenishment.mark_entered(session, batch, boss, None)
    transfers.dispatch(session, wh, batch)
    transfers.receive(session, store, batch, {rec.id: rec.approved_qty - 1}, note="one box missing"); session.commit()
    by = {e.event_type: e for e in session.scalars(select(InventoryEvent))}
    assert session.scalar(select(Issue).where(Issue.title.like("Transfer #%")))
    assert by["store_receiving"].quantity == rec.approved_qty - 1 and by["store_receiving"].actor_user_id == store.id
    assert by["transfer_variance"].quantity == 1 and by["transfer_variance"].reason_text == "one box missing"
    assert by["transfer_dispatch"].reconciliation_status == "mismatch"


def test_transfer_exact_count_matches_dispatch(sync, fake, session):
    boss, rec, batch = _transfer_batch(sync, fake, session)
    wh = mkuser(session, "wh", "warehouse"); store = mkuser(session, "stf", "staff")
    replenishment.mark_entered(session, batch, boss, None)
    transfers.dispatch(session, wh, batch)
    transfers.receive(session, store, batch, {rec.id: rec.approved_qty}); session.commit()
    assert {e.reconciliation_status for e in session.scalars(select(InventoryEvent))} == {"matched"}
    assert v21_metrics.operating_metrics(session, 30)["transfer_confirmation_rate"] == 1.0


# ------------------------------------------------------------------------------- MUST 3
def test_at_cnt_02_count_adjustment_stays_pending_until_storehub_reflects_it(sync, fake, session):
    seeded(sync, fake, session)
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    u = mkuser(session)
    cc = counts.create(session, u, loc, 10); session.commit()
    line = cc.lines[0]
    counts.enter(session, cc, u, [{"line_id": line.id, "counted": 4.0}])
    with pytest.raises(counts.CountError, match="reason"):
        counts.submit(session, cc, u)  # a difference needs a reason
    counts.enter(session, cc, u, [{"line_id": line.id, "counted": 4.0, "note": "shelf recount"}])
    counts.submit(session, cc, u); counts.approve(session, cc, u); session.commit()
    e = session.scalar(select(InventoryEvent).where(InventoryEvent.event_type == "cycle_count"))
    assert e.reconciliation_status == "pending" and e.direction == "+" and e.quantity == 7.0  # counted 4 where StoreHub had -3
    assert counts.reconcile_events(session)["matched"] == 0 and e.reconciliation_status == "pending"
    session.add(InventoryChange(location_id=loc.id, product_id=line.product_id, old_qty=line.system_qty, new_qty=4.0, delta=7.0)); session.commit()
    assert counts.reconcile_events(session)["matched"] == 1 and e.reconciliation_status == "matched"


def test_count_never_reflected_is_flagged_for_review(sync, fake, session):
    seeded(sync, fake, session)
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    u = mkuser(session)
    cc = counts.create(session, u, loc, 10); session.commit()
    counts.enter(session, cc, u, [{"line_id": cc.lines[0].id, "counted": 4.0, "note": "recount"}])
    counts.submit(session, cc, u); counts.approve(session, cc, u)
    e = session.scalar(select(InventoryEvent).where(InventoryEvent.event_type == "cycle_count"))
    e.recorded_at = now_utc() - timedelta(days=5); session.commit()
    assert counts.reconcile_events(session)["needs_review"] == 1 and e.reconciliation_status == "needs_review"
    assert session.scalar(select(Issue).where(Issue.title == "Count adjustment is not in StoreHub yet"))


def test_repeated_count_differences_raise_a_root_cause_issue(sync, fake, session):
    seeded(sync, fake, session)
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    u = mkuser(session)
    cfgsvc.set_(session, "counts.repeat_variance_count", 2)
    p2 = session.scalar(select(Product).where(Product.storehub_id == "P2"))
    for i in range(2):
        cc = counts.create_targeted(session, u, loc, [p2.id]); session.flush()
        line = next(l for l in cc.lines if l.product_id == p2.id)
        counts.enter(session, cc, u, [{"line_id": line.id, "counted": float(i + 1), "note": "again"}])
        counts.submit(session, cc, u); counts.approve(session, cc, u); session.commit()
    assert session.scalar(select(Issue).where(Issue.title.like("Repeated count differences%")))


def test_at_cnt_01_negative_stock_finding_creates_a_count_task(sync, fake, session):
    seeded(sync, fake, session)
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    neg = session.scalar(select(Product).where(Product.storehub_id == "P2"))
    cc = counts.create_targeted(session, mkuser(session), loc, [neg.id]); session.commit()
    assert cc.lines[0].product_id == neg.id and cc.lines[0].reason == "negative_stock" and cc.status == "open"


# ------------------------------------------------------------------------------- MUST 4
def test_at_dq_01_blocked_suggestion_shows_cause_value_and_fix(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session)
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    rec.status, rec.needs_review_reasons = "needs_review", ["negative_stock", "no_supplier"]; session.commit()
    rows = v21_metrics.repair_queue(session)
    assert len(rows) == 1 and {c["code"] for c in rows[0]["causes"]} == {"negative_stock", "no_supplier"} and rows[0]["count_task"]
    c = rows[0]["causes"][0]
    assert c["label"] and c["why"] and c["fix_url"] and c["fix_label"]
    assert rows[0]["value"] >= 0 and rows[0]["severity"] in ("high", "medium", "low")
    k = v21_metrics.repair_kpis(session)
    assert k["open_value"] >= 0 and len(k["trend"]) == 14


def test_v21_pages_open_for_a_manager(sync, fake, session, client):
    seeded(sync, fake, session)
    replenishment.run(session); session.commit()
    add_user(session, "mgr21", "management"); login(client, "mgr21")
    for url in ("/replenishment/repairs", "/traceability", "/traceability?days=7&status=all"):
        assert client.get(url).status_code == 200, url


# ------------------------------------------------------------------------------- audit
def test_at_aud_01_audit_chain_valid_after_the_whole_flow(sync, fake, session):
    boss, rec, batch = _transfer_batch(sync, fake, session)
    wh = mkuser(session, "wh", "warehouse"); store = mkuser(session, "stf", "staff")
    replenishment.mark_entered(session, batch, boss, None)
    transfers.dispatch(session, wh, batch); transfers.receive(session, store, batch, {rec.id: rec.approved_qty}); session.commit()
    assert audit.verify_chain(session)[0] is True
    assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "inventory_event.record")) >= 2
