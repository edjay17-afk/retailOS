"""Replay gate (design principle: every recommendation is replayable).

Every stored suggestion must reproduce exactly from its own recorded inputs and run settings; a formula change
that regresses on replay fails here before it reaches a manager."""
from sqlalchemy import select

from app.models import Recommendation
from app.services import replenishment
from tests.test_services import seeded


def test_every_generated_suggestion_replays_exactly(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session)
    session.commit()
    out = replenishment.replay_all(session)
    assert out["total"] > 0
    assert out["failed"] == 0 and out["ok"] == out["total"] - out["skipped"], out["failures"]


def test_replay_detects_a_number_that_no_longer_reproduces(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session)
    session.commit()
    rec = session.scalar(select(Recommendation))
    assert replenishment.replay(session, rec)["ok"]
    rec.raw_qty += 5  # tamper: the stored number no longer matches the recorded inputs
    r = replenishment.replay(session, rec)
    assert not r["ok"] and any("raw" in m for m in r["mismatches"])


def test_sales_only_mode_ignores_stock_skips_recent_orders_and_still_replays(sync, fake, session):
    from datetime import timedelta
    from app import settings_service as cfg
    from app.models import InventoryCurrent
    from app.timeutil import today_local
    seeded(sync, fake, session)
    cfg.set_(session, "repl.mode", "sales_only")
    cfg.set_(session, "repl.sales_only_cover_days", 7)
    session.commit()
    replenishment.run(session)
    session.commit()
    recs = list(session.scalars(select(Recommendation).where(Recommendation.business_date == today_local())))
    assert recs and all(r.explanation["mode"] == "sales_only" and r.days_cover is None and r.explanation["inputs"]["cover_days"] == 7 for r in recs)
    assert all("negative_stock" not in (r.needs_review_reasons or []) for r in recs)  # a wrong stock count is not a reason to hold a sales-only suggestion
    for r in recs:  # target is sales for the cover period, whatever the stock says
        assert abs(r.target_stock - r.ads * 7) < 0.01 and abs(r.position - r.incoming) < 0.01
    assert replenishment.replay_all(session)["failed"] == 0
    # stock changes do not move the suggestion
    before = {(r.location_id, r.product_id): r.final_qty for r in recs}
    for inv in session.scalars(select(InventoryCurrent)):
        inv.on_hand = 9999.0
    session.commit()
    replenishment.run(session, force=True)
    session.commit()
    after = {(r.location_id, r.product_id): r.final_qty for r in session.scalars(select(Recommendation).where(Recommendation.business_date == today_local()))}
    assert after == before
    # a suggestion approved in the last 7 days is not suggested again
    done = recs[0]
    done.business_date = today_local() - timedelta(days=2)
    done.status, done.approved_qty = "approved", done.final_qty
    session.commit()
    replenishment.run(session, force=True)
    session.commit()
    again = {(r.location_id, r.product_id) for r in session.scalars(select(Recommendation).where(Recommendation.business_date == today_local()))}
    assert (done.location_id, done.product_id) not in again
