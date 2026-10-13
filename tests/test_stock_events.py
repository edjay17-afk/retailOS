"""Stock event capture: audit trail, the key-into-StoreHub issue, verification against the sync, and targeted counts."""
import pytest
from sqlalchemy import select

from app.models import AuditEvent, InventoryChange, Issue, Location, Product, StockEvent
from app.services import counts, stock_events
from app.timeutil import now_utc
from tests.test_services import mkuser, seeded


def loc_prod(session, sh_loc="S_A", sh_prod="P1"):
    loc = session.scalar(select(Location).where(Location.storehub_id == sh_loc))
    prod = session.scalar(select(Product).where(Product.storehub_id == sh_prod))
    return loc, prod


# =============================================================================== create
def test_create_writes_audit_and_instruction_issue(sync, fake, session):
    seeded(sync, fake, session)
    u = mkuser(session)
    loc, p1 = loc_prod(session)
    ev = stock_events.create(session, u, kind="write_off", location_id=loc.id, product_id=p1.id, qty=2, reason="damaged", note="crushed box")
    session.commit()
    assert ev.id and ev.verify_issue_id and ev.verified_at is None
    iss = session.get(Issue, ev.verify_issue_id)
    assert iss.severity == "P3" and iss.dedupe_key == f"stockevent:{ev.id}" and iss.status == "open"
    assert "StoreHub" in iss.description  # the issue is the to-do: key it into the back office
    a = session.scalar(select(AuditEvent).where(AuditEvent.action == "stock_event.create", AuditEvent.entity_id == str(ev.id)))
    assert a and a.after["kind"] == "write_off" and a.after["qty"] == 2 and a.after["reason"] == "damaged" and a.after["note"] == "crushed box"


def test_create_rejects_bad_qty_and_reason(sync, fake, session):
    seeded(sync, fake, session)
    u = mkuser(session)
    loc, p1 = loc_prod(session)
    with pytest.raises(stock_events.StockEventError):
        stock_events.create(session, u, kind="write_off", location_id=loc.id, product_id=p1.id, qty=0, reason="damaged")
    with pytest.raises(stock_events.StockEventError):
        stock_events.create(session, u, kind="write_off", location_id=loc.id, product_id=p1.id, qty=-1, reason="damaged")
    with pytest.raises(stock_events.StockEventError):
        stock_events.create(session, u, kind="write_off", location_id=loc.id, product_id=p1.id, qty=1, reason="delivery without PO")
    with pytest.raises(stock_events.StockEventError):
        stock_events.create(session, u, kind="unplanned_receipt", location_id=loc.id, product_id=p1.id, qty=1, reason="damaged")
    session.rollback()
    assert session.scalar(select(StockEvent)) is None  # validation happens before anything is written


# =============================================================================== verification against the sync
def test_verify_with_sync_marks_only_matching_events(sync, fake, session):
    seeded(sync, fake, session)
    u = mkuser(session)
    loc, p1 = loc_prod(session)
    ev = stock_events.create(session, u, kind="write_off", location_id=loc.id, product_id=p1.id, qty=3, reason="damaged")
    too_big = stock_events.create(session, u, kind="write_off", location_id=loc.id, product_id=p1.id, qty=5, reason="expired")
    wrong_dir = stock_events.create(session, u, kind="unplanned_receipt", location_id=loc.id, product_id=p1.id, qty=1, reason="delivery without PO")
    session.flush()
    session.add(InventoryChange(location_id=loc.id, product_id=p1.id, old_qty=10, new_qty=8, delta=-2, observed_at=now_utc()))
    out = stock_events.verify_with_sync(session)
    session.commit()
    assert out["verified"] == 1
    for e in (ev, too_big, wrong_dir):
        session.refresh(e)
    assert ev.verified_at is not None  # negative delta, abs 2 >= 3 * 0.5
    assert too_big.verified_at is None  # abs 2 < 5 * 0.5: a smaller move does not verify a bigger write-off
    assert wrong_dir.verified_at is None  # a stock decrease never verifies a receipt
    iss = session.get(Issue, ev.verify_issue_id)
    assert iss.status == "closed"
    assert session.get(Issue, too_big.verify_issue_id).status == "open"


def test_verify_with_sync_ignores_older_stock_moves(sync, fake, session):
    from datetime import timedelta
    seeded(sync, fake, session)
    u = mkuser(session)
    loc, p1 = loc_prod(session)
    ev = stock_events.create(session, u, kind="write_off", location_id=loc.id, product_id=p1.id, qty=2, reason="damaged")
    session.flush()
    # a move observed before the event was recorded cannot be its StoreHub entry
    session.add(InventoryChange(location_id=loc.id, product_id=p1.id, old_qty=12, new_qty=10, delta=-2,
                                observed_at=ev.created_at - timedelta(seconds=1)))
    out = stock_events.verify_with_sync(session)
    assert out["verified"] == 0
    session.refresh(ev)
    assert ev.verified_at is None


# =============================================================================== targeted counts
def test_create_targeted_single_product_and_merge(sync, fake, session):
    seeded(sync, fake, session)
    u = mkuser(session)
    loc, p1 = loc_prod(session)
    p2 = session.scalar(select(Product).where(Product.storehub_id == "P2"))
    cc = counts.create_targeted(session, u, loc, [p2.id])
    session.commit()
    assert cc.location_id == loc.id and cc.status == "open" and len(cc.lines) == 1
    line = cc.lines[0]
    assert line.product_id == p2.id and line.reason == "negative_stock" and line.system_qty == -3  # the fake has -3 of P2 at S_A
    a = session.scalar(select(AuditEvent).where(AuditEvent.action == "count.create", AuditEvent.entity_id == str(cc.id)))
    assert a and a.after["lines"] == 1
    # asking for more products merges into the same open count; a product already in it is not duplicated
    cc2 = counts.create_targeted(session, u, loc, [p1.id, p2.id])
    session.commit()
    assert cc2.id == cc.id
    assert sorted(l.product_id for l in cc2.lines) == sorted([p1.id, p2.id])
    p1_line = next(l for l in cc2.lines if l.product_id == p1.id)
    assert p1_line.reason == "targeted" and p1_line.system_qty == 10  # positive stock is just a targeted count
