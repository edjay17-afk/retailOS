"""Business services end to end: replenishment, data quality, issues, inspections, receiving, counts, audit."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app import audit
from app import settings_service as cfgsvc
from app.models import (CycleCount, DqEvent, ExpiryBatch, Inspection, Issue, Location, OrderBatch, Product, PurchaseOrder, Receiving,
                        Recommendation, Supplier, SyncState, User)
from app.security import hash_password
from app.services import counts, dq, expiry, freshness, inspections, issues, purchasing, receiving, replenishment
from app.timeutil import now_utc, today_local
from tests.conftest import tx


def mkuser(db, name="mgr", role="store_manager", locs=None):
    u = User(username=name, full_name=name.title(), role=role, password_hash=hash_password("Passw0rd-long"), location_ids=locs)
    db.add(u); db.flush()
    return u


def iso(d: date, hour=3):  # 03:00 UTC = 11:00 Manila the same day
    return f"{d.isoformat()}T{hour:02d}:00:00Z"


def seeded(sync, fake, session, days=10, qty=4):
    """Store Alpha sells `qty` of P1 on each of the last `days` days; everything synced and fresh."""
    sync.sync_stores(); sync.sync_employees(); sync.sync_products(); sync.sync_inventory()
    t = today_local()
    fake.data["transactions"] = [tx(f"T{i}", "S_A", iso(t - timedelta(days=i)), [("P1", qty, qty * 20.0)]) for i in range(1, days + 1)]
    sync.sync_transactions(t - timedelta(days=days), t)
    sync.sync_purchase_orders(); sync.sync_timesheets()
    session.expire_all()


# =============================================================================== replenishment
def test_engine_produces_the_hand_calculated_transfer(sync, fake, session):
    seeded(sync, fake, session)
    run = replenishment.run(session); session.commit()
    assert run.status == "ok"
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    p1 = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    rec = session.scalar(select(Recommendation).where(Recommendation.location_id == loc.id, Recommendation.product_id == p1.id))
    # ADS 4; lead 1 + review 1 = 2 days -> 8; safety 2 days -> 8; target 16; on hand 10; need 6, rounded up to a pack of 12
    assert rec.source_kind == "transfer" and rec.ads == 4.0 and rec.target_stock == 16.0 and rec.position == 10.0 and rec.final_qty == 12.0
    assert rec.status == "pending" and rec.confidence > 0.8
    assert rec.explanation["inputs"]["trading_days"] == 7


def test_engine_run_twice_makes_no_duplicates(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session); session.commit()
    n = session.scalar(select(func.count()).select_from(Recommendation))
    replenishment.run(session); session.commit()
    assert session.scalar(select(func.count()).select_from(Recommendation)) == n and n > 0


def test_engine_blocks_on_stale_data(sync, fake, session):
    seeded(sync, fake, session)
    st = session.get(SyncState, "inventory")
    st.last_success_at = now_utc() - timedelta(hours=3); session.commit()
    run = replenishment.run(session); session.commit()
    assert run.status == "blocked" and "Inventory" in run.note
    assert session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_negative_stock_needs_review_and_lowers_confidence(sync, fake, session):
    seeded(sync, fake, session)
    t = today_local()
    fake.data["transactions"] += [tx(f"C{i}", "S_A", iso(t - timedelta(days=i)), [("P2", 3, 30.0)]) for i in range(1, 11)]
    sync.sync_transactions(t - timedelta(days=10), t)
    replenishment.run(session); session.commit()
    p2 = session.scalar(select(Product).where(Product.storehub_id == "P2"))
    rec = session.scalar(select(Recommendation).where(Recommendation.product_id == p2.id, Recommendation.source_kind == "transfer"))
    assert rec.status == "needs_review" and "negative_stock" in rec.needs_review_reasons and rec.confidence <= 0.7
    assert rec.on_hand == -3 and rec.position == 0  # negative stock is treated as zero in the calculation


def test_stale_open_po_is_not_incoming(sync, fake, session):
    seeded(sync, fake, session)
    old = (now_utc() - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_exp = (now_utc() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = lambda q: [{"productId": "P1", "orderedQuantity": q, "supplierPrice": 10, "subTotal": 10 * q}]
    fake.data["purchaseOrders"] = [
        {"purchaseOrderId": 1, "status": "open", "supplierId": "SUP1", "targetStoreId": "S_WH", "createdTime": old, "modifiedTime": old, "expectedArrivalDate": old,
         "subTotal": 1, "tax": 0, "discount": 0, "total": 1, "orderedItems": line(500)},
        {"purchaseOrderId": 2, "status": "partiallyReceived", "supplierId": "SUP1", "targetStoreId": "S_WH", "createdTime": old, "modifiedTime": old, "expectedArrivalDate": fresh_exp,
         "subTotal": 1, "tax": 0, "discount": 0, "total": 1, "orderedItems": [{"productId": "P1", "orderedQuantity": 30, "receivedQuantity": 10, "supplierPrice": 10, "subTotal": 300}]}]
    sync.sync_purchase_orders(); session.expire_all()
    inc, stale = purchasing.incoming_map(session)
    wh = session.scalar(select(Location).where(Location.storehub_id == "S_WH"))
    p1 = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    assert inc[(wh.id, p1.id)] == 20.0  # only PO 2, ordered 30 minus received 10; the 20-day-old PO is ignored
    assert [p.sh_po_id for p in stale] == [1]
    dq.run_all(session); session.commit()
    assert session.scalar(select(DqEvent).where(DqEvent.rule == "stale_open_po", DqEvent.resolved_at.is_(None)))


def test_decisions_audit_limits_and_batches(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session)
    mgr = mkuser(session)
    boss = mkuser(session, "boss", "management")
    session.commit()
    rec = session.scalar(select(Recommendation).where(Recommendation.source_kind == "transfer", Recommendation.status == "pending"))
    with pytest.raises(replenishment.DecisionError):
        replenishment.decide(session, rec, mgr, "reject", reason="   ")  # a reason is required (blank is not one)
    with pytest.raises(replenishment.DecisionError):
        replenishment.decide(session, rec, mgr, "modify", qty=3)  # reason required
    cfgsvc.set_(session, "repl.approval_limit_manager", 1.0)  # absurdly low limit
    with pytest.raises(replenishment.DecisionError):
        replenishment.decide(session, rec, mgr, "approve")
    session.rollback()
    rec = session.get(Recommendation, rec.id)
    replenishment.decide(session, rec, boss, "modify", qty=3, reason="  Cebu fiesta week, order double  ")
    assert rec.status == "modified" and rec.approved_qty == 3 and rec.reason_code == "Cebu fiesta week, order double"  # any wording, trimmed
    with pytest.raises(replenishment.DecisionError):
        replenishment.decide(session, rec, boss, "approve")  # already decided
    batches = replenishment.create_batches(session, boss)
    assert len(batches) == 1 and batches[0].source_kind == "transfer" and batches[0].lines[0].approved_qty == 3
    replenishment.mark_entered(session, batches[0], boss, None)
    assert rec.status == "ordered"
    ok, n, bad = audit.verify_chain(session)
    assert ok and n >= 3


def test_supplier_batch_needs_a_po_number(sync, fake, session):
    seeded(sync, fake, session)
    run = replenishment.run(session)  # the engine no longer raises warehouse supplier suggestions, so build one directly
    boss = mkuser(session, "boss", "management")
    loc = session.scalar(select(Location).where(Location.type == "warehouse"))
    prod = session.scalar(select(Product).limit(1))
    sup = Supplier(name="Test Supplier"); session.add(sup); session.flush()
    prod.supplier_id = sup.id
    rec = Recommendation(run_id=run.id, location_id=loc.id, product_id=prod.id, supplier_id=prod.supplier_id,
                         business_date=today_local(), calc_version=replenishment.CALC_VERSION, source_kind="supplier",
                         ads=5, trading_days=10, on_hand=0, raw_qty=50, final_qty=50, status="pending")
    session.add(rec); session.commit()
    replenishment.decide(session, rec, boss, "approve")
    b = replenishment.create_batches(session, boss)[0]
    with pytest.raises(replenishment.DecisionError):
        replenishment.mark_entered(session, b, boss, None)


def test_metrics_after_decisions(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session)
    boss = mkuser(session, "boss", "management"); session.commit()
    recs = session.scalars(select(Recommendation).where(Recommendation.status == "pending")).all()
    replenishment.decide(session, recs[0], boss, "approve")
    replenishment.decide(session, recs[1], boss, "reject", reason="Other") if len(recs) > 1 else None
    m = replenishment.metrics(session)
    assert m["decided"] >= 1 and m["acceptance_rate"] is not None
    oo = m["override_outcomes"]
    assert oo["verdict"] is None and oo["modified"] == 0  # no measured outcomes yet: refuse to conclude


# =============================================================================== data quality
def test_dq_rules_and_auto_resolve(sync, fake, session):
    seeded(sync, fake, session)
    out = dq.run_all(session); session.commit()
    assert out["negative_stock"]["open_now"] == 1  # Alpha / Chips = -3
    assert out["no_cost"]["open_now"] == 0
    fake.data["inventory"]["S_A"][1]["quantityOnHand"] = 5
    sync.sync_inventory()
    out = dq.run_all(session); session.commit()
    assert out["negative_stock"]["open_now"] == 0 and out["negative_stock"]["resolved"] == 1


def test_stale_domain_raises_a_critical_issue(sync, fake, session):
    seeded(sync, fake, session)
    session.get(SyncState, "sales").last_success_at = now_utc() - timedelta(hours=2); session.commit()
    dq.run_all(session); session.commit()
    ev = session.scalar(select(DqEvent).where(DqEvent.rule == "stale_sync", DqEvent.resolved_at.is_(None)))
    assert ev.severity == "critical" and ev.issue_id
    assert session.get(Issue, ev.issue_id).severity == "P1"
    dq.run_all(session); session.commit()
    assert session.scalar(select(func.count()).select_from(Issue).where(Issue.type == "data_quality")) == 1  # no duplicate issue


# =============================================================================== issues
def test_issue_workflow_requires_verification(cfg, session):
    mgr = mkuser(session); staff = mkuser(session, "staff1", "staff")
    i = issues.create_issue(session, type="stock", severity="P2", title="Shelf broken", created_by=mgr.id, owner_id=staff.id)
    session.commit()
    with pytest.raises(issues.IssueError):
        issues.act(session, i, staff, "resolve", note="")  # fix must be described
    issues.act(session, i, staff, "acknowledge")
    issues.act(session, i, staff, "resolve", note="Replaced the shelf")
    with pytest.raises(issues.IssueError):
        issues.act(session, i, staff, "close")  # staff cannot verify
    issues.act(session, i, mgr, "close", note="Checked")
    assert i.status == "closed" and i.verified_by == mgr.id
    with pytest.raises(issues.IssueError):
        issues.act(session, i, mgr, "comment", note="x")  # closed


def test_issue_dedupe_and_sla(cfg, session):
    a = issues.create_issue(session, type="stock", severity="P1", title="A", dedupe_key="k1")
    b = issues.create_issue(session, type="stock", severity="P1", title="A again", dedupe_key="k1")
    assert a.id == b.id
    assert abs((a.due_at - a.created_at).total_seconds() - 24 * 3600) < 5  # P1 is due in 24 hours


def test_escalation_is_idempotent(cfg, session):
    mgr = mkuser(session, "m", "management")
    i = issues.create_issue(session, type="stock", severity="P1", title="Late", owner_id=mgr.id)
    i.created_at = now_utc() - timedelta(minutes=45); i.due_at = now_utc() + timedelta(hours=5); session.commit()
    assert issues.escalate_overdue(session) == {"level1": 1, "level2": 0}
    assert issues.escalate_overdue(session) == {"level1": 0, "level2": 0}
    i.due_at = now_utc() - timedelta(minutes=1)
    assert issues.escalate_overdue(session) == {"level1": 0, "level2": 1}
    assert i.escalation_level == 2


# =============================================================================== inspections
def test_tasks_generated_once_per_day(sync, session):
    sync.sync_stores(); session.expire_all()
    n1 = inspections.generate_tasks(session, today_local()); session.commit()
    n2 = inspections.generate_tasks(session, today_local()); session.commit()
    assert n1 == 2 + 1 and n2 == 0  # 2 stores x 1 Store Daily Inspection + 1 Warehouse Inspection


def _task(session, slot="store_daily"):
    return session.scalar(select(Inspection).join(Inspection.template).where(Inspection.template.has(code=slot)))


def test_failed_item_needs_note_and_photo_then_opens_issue(sync, session):
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    insp = _task(session, "warehouse_daily")
    insp.due_at = now_utc() + timedelta(hours=1)  # keep this completion test independent of the local time
    user = mkuser(session, "mgr2")
    items = insp.template.items
    ok = {i.id: ({"value": "50" if "Humidity" in i.label else "20", "passed": None} if i.kind == "number" else {"passed": True}) for i in items}
    answers = {k: dict(v) for k, v in ok.items()}
    answers[items[1].id] = {"value": "40", "passed": None, "note": ""}  # storage temperature 40C is above the allowed 30C
    with pytest.raises(inspections.InspectionError, match="note"):
        inspections.complete(session, insp, user, answers, {})
    answers[items[1].id]["note"] = "Door left open"
    with pytest.raises(inspections.InspectionError, match="photo"):
        inspections.complete(session, insp, user, answers, {})
    inspections.complete(session, insp, user, answers, {items[1].id: "2026/10/x.jpg"}); session.commit()
    assert insp.status == "completed" and insp.fail_count == 1
    iss = session.scalar(select(Issue).where(Issue.source == "inspection"))
    assert iss.severity == "P2" and "Storage temperature" in iss.title and iss.owner_id is not None
    with pytest.raises(inspections.InspectionError):
        inspections.complete(session, insp, user, answers, {items[1].id: "x.jpg"})  # already completed


def test_late_created_tasks_are_not_alerts(sync, session):
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    for i in session.scalars(select(Inspection)):
        i.due_at = now_utc() - timedelta(hours=2)  # created now, due two hours ago: the system simply started late
    session.commit()
    assert inspections.flag_missed(session) == 0


def test_missed_checklist_raises_issue_once(sync, session):
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    for i in session.scalars(select(Inspection)):
        i.created_at = now_utc() - timedelta(hours=10)
        i.due_at = now_utc() - timedelta(hours=2)
    session.commit()
    n = inspections.flag_missed(session); session.commit()
    assert n == 3
    assert inspections.flag_missed(session) == 0
    assert session.scalar(select(func.count()).select_from(Issue).where(Issue.title.like("Missed checklist%"))) == 3


def test_staff_do_not_need_to_be_clocked_in(sync, fake, session):
    sync.sync_stores(); sync.sync_employees(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    insp = _task(session)
    staff = mkuser(session, "cashier", "staff"); staff.storehub_employee_id = "E1"; session.commit()
    inspections.start(session, insp, staff)  # no POS clock-in: still allowed
    assert insp.status == "in_progress" and insp.clocked_in_ok is False  # recorded for reference only


# =============================================================================== receiving, expiry, counts
def _po(sync, fake, session, qty=10):
    seeded(sync, fake, session)
    ts = (now_utc() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake.data["purchaseOrders"] = [{"purchaseOrderId": 7, "status": "open", "supplierId": "SUP1", "targetStoreId": "S_A", "createdTime": ts, "modifiedTime": ts,
                                    "expectedArrivalDate": (now_utc() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "subTotal": 100, "tax": 12, "discount": 0, "total": 112,
                                    "orderedItems": [{"productId": "P1", "orderedQuantity": qty, "supplierPrice": 10, "subTotal": 10 * qty}]}]
    sync.sync_purchase_orders(); session.expire_all()
    return session.scalar(select(PurchaseOrder).where(PurchaseOrder.sh_po_id == 7))


def test_receiving_mismatch_opens_issue_and_records_expiry(sync, fake, session):
    po = _po(sync, fake, session)
    u = mkuser(session, "wh", "warehouse")
    rec = receiving.start(session, u, po)
    assert receiving.start(session, u, po).id == rec.id  # one open receiving per PO
    with pytest.raises(receiving.ReceivingError):
        receiving.complete(session, rec, u)  # nothing counted yet
    line = rec.lines[0]
    receiving.save_counts(session, rec, u, [{"line_id": line.id, "counted": 8.0, "damaged": 1.0, "expiry": today_local() + timedelta(days=20), "batch": "B1", "note": "1 box crushed, 1 short"}])
    receiving.complete(session, rec, u); session.commit()
    assert rec.status == "mismatch"
    assert session.scalar(select(Issue).where(Issue.type == "receiving", Issue.title.like("Receiving difference%")))
    b = session.scalar(select(ExpiryBatch))
    assert b.qty == 7.0 and b.batch_no == "B1"  # counted 8 minus 1 damaged


def test_receiving_exact_match_has_no_issue(sync, fake, session):
    po = _po(sync, fake, session)
    u = mkuser(session, "wh", "warehouse")
    rec = receiving.start(session, u, po)
    receiving.save_counts(session, rec, u, [{"line_id": rec.lines[0].id, "counted": 10.0, "damaged": 0}])
    receiving.complete(session, rec, u)
    assert rec.status == "completed" and not session.scalar(select(Issue))


def test_counted_but_not_received_in_storehub_is_flagged(sync, fake, session):
    po = _po(sync, fake, session)
    u = mkuser(session, "wh", "warehouse")
    rec = receiving.start(session, u, po)
    receiving.save_counts(session, rec, u, [{"line_id": rec.lines[0].id, "counted": 10.0, "damaged": 0}])
    receiving.complete(session, rec, u)
    rec.completed_at = now_utc() - timedelta(hours=30); session.commit()
    out = receiving.verify_with_storehub(session)
    assert out["flagged"] == 1 and session.scalar(select(Issue).where(Issue.title.like("%still open in StoreHub%")))


def test_expiry_stages_and_issue_per_stage(sync, fake, session):
    seeded(sync, fake, session)
    u = mkuser(session)
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A")); p = session.scalar(select(Product))
    assert expiry.stage_for(-1, [30, 14, 7, 3]) == "expired" and expiry.stage_for(2, [30, 14, 7, 3]) == "3"
    assert expiry.stage_for(40, [30, 14, 7, 3]) is None and expiry.stage_for(10, [30, 14, 7, 3]) == "14"
    expiry.add_batch(session, u, loc.id, p.id, 5, today_local() + timedelta(days=2))
    assert expiry.check(session) == 1
    expiry.check(session)
    assert session.scalar(select(func.count()).select_from(Issue).where(Issue.type == "expiry")) == 1


def test_cycle_count_puts_negative_stock_first_and_approval_opens_issue(sync, fake, session):
    seeded(sync, fake, session)
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    u = mkuser(session)
    cc = counts.create(session, u, loc, 10); session.commit()
    assert cc.lines[0].reason == "negative_stock" and cc.lines[0].product.storehub_id == "P2"
    counts.enter(session, cc, u, [{"line_id": cc.lines[0].id, "counted": 4.0, "note": "found 4 on shelf"}])
    counts.submit(session, cc, u); counts.approve(session, cc, u)
    assert cc.lines[0].variance == 7.0
    assert session.scalar(select(Issue).where(Issue.type == "stock"))
    csv = counts.stock_take_csv(cc)
    assert "P2" in csv and "Counted Quantity" in csv


# =============================================================================== audit log
def test_audit_log_is_append_only_and_tamper_evident(cfg, session):
    audit.log(session, actor_type="system", actor_id="t", action="a", entity_type="x", entity_id=1, after={"v": 1})
    audit.log(session, actor_type="system", actor_id="t", action="b", entity_type="x", entity_id=1, after={"v": 2}); session.commit()
    for sql in ("update audit_event set action='z'", "delete from audit_event"):
        with pytest.raises(Exception, match="append-only"):
            session.execute(text(sql)); session.commit()
        session.rollback()
    assert audit.verify_chain(session)[0] is True
    # an administrator who removes the trigger and edits a row is still detected by the hash chain
    session.execute(text("drop trigger audit_event_no_update"))
    session.execute(text("update audit_event set after='{\"v\": 99}' where id=1")); session.commit()
    ok, n, bad = audit.verify_chain(session)
    assert ok is False and bad == 1


# =============================================================================== transfers, compliance, performance
def _transfer_batch(sync, fake, session):
    from app.services import transfers  # noqa: F401
    seeded(sync, fake, session)
    replenishment.run(session)
    boss = mkuser(session, "boss", "management"); session.commit()
    rec = session.scalar(select(Recommendation).where(Recommendation.source_kind == "transfer", Recommendation.status == "pending"))
    replenishment.decide(session, rec, boss, "approve")
    batch = replenishment.create_batches(session, boss)[0]
    return boss, rec, batch


def test_transfer_flow_dispatch_then_store_count_with_difference(sync, fake, session):
    from app.services import transfers
    boss, rec, batch = _transfer_batch(sync, fake, session)
    wh = mkuser(session, "wh", "warehouse"); store = mkuser(session, "stf", "staff")
    with pytest.raises(transfers.TransferError, match="StoreHub first"):
        transfers.dispatch(session, wh, batch)  # not keyed in StoreHub yet
    replenishment.mark_entered(session, batch, boss, None)
    with pytest.raises(transfers.TransferError, match="not dispatched"):
        transfers.receive(session, store, batch, {rec.id: rec.approved_qty})
    transfers.dispatch(session, wh, batch)
    with pytest.raises(transfers.TransferError, match="every line"):
        transfers.receive(session, store, batch, {})
    transfers.receive(session, store, batch, {rec.id: rec.approved_qty - 1}); session.commit()
    assert batch.status == "received" and rec.received_qty == rec.approved_qty - 1
    iss = session.scalar(select(Issue).where(Issue.title.like("Transfer #%")))
    assert iss and iss.severity == "P2" and "sent" in iss.description
    with pytest.raises(transfers.TransferError, match="already received"):
        transfers.receive(session, store, batch, {rec.id: 1})


def test_transfer_exact_count_has_no_issue(sync, fake, session):
    from app.services import transfers
    boss, rec, batch = _transfer_batch(sync, fake, session)
    replenishment.mark_entered(session, batch, boss, None)
    transfers.dispatch(session, mkuser(session, "wh", "warehouse"), batch)
    transfers.receive(session, mkuser(session, "stf", "staff"), batch, {rec.id: rec.approved_qty})
    assert not session.scalar(select(Issue).where(Issue.title.like("Transfer #%")))


def test_compliance_stages_and_idempotent_alerts(cfg, session):
    from app.services import compliance
    assert compliance.stage(45) is None and compliance.stage(30)[0] == 30 and compliance.stage(10)[0] == 14 and compliance.stage(3)[0] == 7 and compliance.stage(-2)[0] == 0
    boss = mkuser(session, "boss", "management")
    t = today_local()
    it = compliance.add(session, boss, kind="permit", name="Mayor's permit", due=t + timedelta(days=20), location_id=None, owner_id=boss.id, reference="MP-1", notes=None)
    assert compliance.check(session) == 1 and compliance.check(session) == 1
    assert session.scalar(select(func.count()).select_from(Issue).where(Issue.type == "permit")) == 1  # same stage, one issue
    far = compliance.add(session, boss, kind="equipment", name="Chiller 2 service", due=t + timedelta(days=90), location_id=None, owner_id=None, reference=None, notes=None)
    assert compliance.check(session) == 1  # the far item raises nothing yet
    with pytest.raises(ValueError):
        compliance.renew(session, boss, it, t, None)  # must be later
    compliance.renew(session, boss, it, t + timedelta(days=365), "renewed")
    compliance.check(session)
    sev = {i.title: i.severity for i in session.scalars(select(Issue))}
    assert all("Mayor" in k for k in sev) and len(sev) == 1


def test_performance_metrics_compute(sync, fake, session):
    from app.services import performance
    seeded(sync, fake, session)
    replenishment.run(session); session.commit()
    a = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    m = performance.compute(session, [a.id])
    assert m["inventory"]["negative_items"] == 1 and m["data"]["sync_runs"] > 0 and m["data"]["fresh_domains"] == 5
    boss = mkuser(session, "boss", "management")
    i = issues.create_issue(session, type="stock", severity="P3", title="x: a", location_id=a.id); issues.create_issue(session, type="stock", severity="P3", title="x: b", location_id=a.id)
    issues.act(session, i, boss, "acknowledge"); session.commit()
    m = performance.compute(session, [a.id])
    assert m["issues"]["open"] == 2 and m["issues"]["median_ack_h"] is not None and m["issues"]["repeat_rate"] == 0.5


def test_lark_alerts_are_forwarded_once(cfg, session):
    import httpx
    from app import lark
    from app.config import Config, set_config
    from app.services import notifications
    sent = []
    def handler(req):
        if req.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
        sent.append(req.content); return httpx.Response(200, json={"code": 0, "data": {}})
    c = Config(data_dir=cfg.data_dir, lark_app_id="a", lark_app_secret="b", lark_alert_chat_id="oc_1", scheduler_enabled=False); set_config(c)
    client = lark.LarkClient(c, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    notifications.notify(session, role="management", level="critical", title="Big cash variance"); notifications.notify(session, role="management", level="info", title="FYI")
    session.commit()
    assert lark.forward_alerts(client)["sent"] == 1 and lark.forward_alerts(client)["sent"] == 0
    assert b"Big cash variance" in sent[0] and len(sent) == 1
    assert lark.forward_alerts(lark.LarkClient(Config(data_dir=cfg.data_dir, lark_app_id="", lark_app_secret="", lark_alert_chat_id="", scheduler_enabled=False)), chat_id="")["enabled"] is False


def test_startup_recovery_releases_dead_locks_and_closes_interrupted_runs(session):
    from datetime import timedelta
    from app import jobs
    from app.models import JobLock, SyncRun
    from app.timeutil import now_utc
    session.add(JobLock(name="job:transactions", locked_until=now_utc() + timedelta(minutes=25), owner="dead-process"))
    session.add(SyncRun(job="transactions", status="running"))
    session.commit()
    assert jobs.recover_interrupted() == {"locks": 1, "runs": 1}
    session.expire_all()
    assert session.query(JobLock).count() == 0
    assert session.query(SyncRun).one().status == "failed"


def _fill_store_daily(insp):
    items = insp.template.items
    answers = {i.id: ({"value": "24", "passed": None} if i.kind == "number" else {"passed": True} if i.kind == "check" else {}) for i in items}
    video = next(i for i in items if i.kind == "video")
    return answers, {video.id: "2026/10/walk.mp4"}


def test_store_daily_inspection_content_and_due_time(sync, session):
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    insp = _task(session)
    assert insp.template.name == "Store Daily Inspection" and insp.template.due_time == "12:00"
    assert [i.label for i in insp.template.items] == ["Store Entrance", "Counter Area", "Restroom", "Floor & Table Cleanliness", "Aircon Temp", "All Machines (All)",
                                                      "Shelf Stocks (All including chillers and refs)", "Video Walkthrough"]
    assert _task(session, "store_opening") is None and _task(session, "warehouse_daily").template.name == "Warehouse Inspection"


def test_submitting_after_noon_is_late_and_before_noon_is_not(sync, session):
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    user = mkuser(session, "mgr3")
    a, b = [i for i in session.scalars(select(Inspection).join(Inspection.template).where(Inspection.template.has(code="store_daily")))][:2]
    a.due_at = now_utc() + timedelta(hours=2)   # still before 12 noon
    b.due_at = now_utc() - timedelta(minutes=5)  # noon has passed
    for insp in (a, b):
        answers, photos = _fill_store_daily(insp)
        inspections.complete(session, insp, user, answers, photos)
    session.commit()
    assert a.status == "completed" and b.status == "late"


def test_video_walkthrough_is_required(sync, session):
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    insp = _task(session)
    answers, _ = _fill_store_daily(insp)
    with pytest.raises(inspections.InspectionError, match="video"):
        inspections.complete(session, insp, mkuser(session, "mgr4"), answers, {})


def test_old_store_checklists_are_retired_by_the_v6_migration(session):
    from app import dbinit
    from app.models import InspectionTemplate, Location
    loc = Location(storehub_id="S_X", name="Store X", type="store", monitored=True)
    session.add(loc)
    old = InspectionTemplate(code="store_midday", name="Store midday checklist", location_type="store", slot="midday", due_time="14:00", active=True)
    wh = session.scalar(select(InspectionTemplate).where(InspectionTemplate.code == "warehouse_daily"))
    wh.name = "Warehouse daily checklist"  # how an existing database still has it
    session.add(old); session.flush()
    session.add(Inspection(template_id=old.id, location_id=loc.id, business_date=today_local(), due_at=now_utc(), status="pending"))
    session.commit()
    dbinit._migrate_templates_v6(session); session.commit()
    session.expire_all()
    assert session.get(InspectionTemplate, old.id).active is False
    assert session.get(InspectionTemplate, wh.id).name == "Warehouse Inspection"
    assert session.query(Inspection).count() == 0  # the untouched task of a retired checklist is gone


def test_approve_with_a_typed_quantity_needs_no_reason(sync, fake, session):
    seeded(sync, fake, session)
    replenishment.run(session); session.commit()
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    boss = mkuser(session, "boss2", "management")
    replenishment.decide(session, rec, boss, "approve", qty=rec.final_qty + 6)
    assert rec.status == "modified" and rec.approved_qty == rec.final_qty + 6


# =============================================================================== V2.1 MUST 1: receiving truth (inventory events)
def _counted(sync, fake, session, counted=10.0, damaged=0.0, note=""):
    po = _po(sync, fake, session)
    u = mkuser(session, "wh", "warehouse")
    rec = receiving.start(session, u, po)
    receiving.save_counts(session, rec, u, [{"line_id": rec.lines[0].id, "counted": counted, "damaged": damaged, "note": note}])
    receiving.complete(session, rec, u); session.commit()
    return po, rec, u


def test_at_rcv_01_receiving_leaves_a_trace(sync, fake, session):
    from app.models import InventoryEvent
    po, rec, u = _counted(sync, fake, session)
    e = session.scalar(select(InventoryEvent).where(InventoryEvent.event_type == "supplier_receiving"))
    assert (e.actor_user_id, e.actor_role, e.quantity, e.direction) == (u.id, "warehouse", 10.0, "+")
    assert e.related_po_id == po.id and e.storehub_reference == "7" and e.occurred_at and e.audit_event_id
    assert e.reconciliation_status == "pending" and e.source_system == "retailos"


def test_variance_needs_a_reason_and_leaves_variance_and_damage_events(sync, fake, session):
    from app.models import InventoryEvent
    po = _po(sync, fake, session)
    u = mkuser(session, "wh", "warehouse")
    rec = receiving.start(session, u, po)
    receiving.save_counts(session, rec, u, [{"line_id": rec.lines[0].id, "counted": 8.0, "damaged": 1.0, "note": ""}])
    with pytest.raises(receiving.ReceivingError):
        receiving.complete(session, rec, u)  # a difference without a reason is refused
    receiving.save_counts(session, rec, u, [{"line_id": rec.lines[0].id, "counted": 8.0, "damaged": 1.0, "note": "box crushed"}])
    receiving.complete(session, rec, u); session.commit()
    types = {e.event_type: e for e in session.scalars(select(InventoryEvent))}
    assert set(types) == {"supplier_receiving", "receiving_variance", "damage"}
    assert (types["receiving_variance"].quantity, types["receiving_variance"].direction, types["receiving_variance"].reason_text) == (2.0, "-", "box crushed")
    assert (types["damage"].quantity, types["damage"].direction) == (1.0, "-")


def test_at_rcv_02_matching_storehub_receipt_reconciles(sync, fake, session):
    from app.models import InventoryEvent
    po, rec, u = _counted(sync, fake, session)
    po.status = "completed"; po.lines[0].received_qty = 10.0; session.commit()
    out = receiving.verify_with_storehub(session); session.commit()
    e = session.scalar(select(InventoryEvent).where(InventoryEvent.event_type == "supplier_receiving"))
    assert out["matched"] == 1 and e.reconciliation_status == "matched" and e.reconciled_at
    assert receiving.verify_with_storehub(session)["matched"] == 0  # re-running settles nothing twice
    from app.services import inventory_events
    assert inventory_events.kpis(session)["receiving_reconciled_rate"] == 1.0


def test_at_rcv_03_storehub_difference_is_a_mismatch_with_an_issue(sync, fake, session):
    from app.models import InventoryEvent
    po, rec, u = _counted(sync, fake, session)
    po.status = "completed"; po.lines[0].received_qty = 7.0; session.commit()
    receiving.verify_with_storehub(session); session.commit()
    e = session.scalar(select(InventoryEvent).where(InventoryEvent.event_type == "supplier_receiving"))
    assert e.reconciliation_status == "mismatch" and "StoreHub received 7" in e.reconciliation_note
    assert session.scalar(select(Issue).where(Issue.title.like("%StoreHub received quantity differs%")))


def test_at_idemp_01_events_are_recorded_once(sync, fake, session):
    from app.models import InventoryEvent
    from app.services import inventory_events as iev
    po, rec, u = _counted(sync, fake, session)
    n = session.scalar(select(func.count()).select_from(InventoryEvent))
    again = iev.record(session, uid=f"rcv:{rec.lines[0].id}", event_type="supplier_receiving", user=u, product_id=rec.lines[0].product_id, location_id=rec.location_id, quantity=99)
    session.commit()
    assert session.scalar(select(func.count()).select_from(InventoryEvent)) == n and again.quantity == 10.0  # the first record stands
