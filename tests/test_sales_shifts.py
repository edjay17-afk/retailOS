"""Sales by shift: StoreHub's Shift Reports are read (never written), parsed exactly, saved, and shown."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Location, SalesShift
from app.services import sales_shifts
from app.timeutil import today_local
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401

PAGE = """Shift Details
 Dashboard  Shift Reports  Shift Details
General
Store Name:

The Daily Centro - Alpha

Cashier:

Torniado Jennifer

Register #:

5

Opening Fund
	Amount (PHP)
Opening Fund	450.00
Payments Received
	Qty	Amount (PHP)
Cash	77	9,300.00
Credit Card	0	0.00
GCASH	5	2,966.00
Total Payments:	82	12,266.00
Void
	Qty	Amount (PHP)
Cash	0	0.00
Total Void:	3	50.00
Refund
	Qty	Amount (PHP)
Total Refund:	0.00
Withdrawals
	Qty	Amount (PHP)
Withdrawals	2	1,071.00
Discount Summary
	Qty	Amount (PHP)
Discount	1	25.50
Tax Summary
Tax Code	Tax Rate	Amount (PHP)
VAT	12%	1,314.09
Total:	1,314.09
"""


def row(sid, opened, closed, store, n="82", total="12266.00"):
    return {"0": opened, "1": closed, "2": store, "3": "5", "4": n, "5": total, "6": "0.00", "7": "", "DT_RowId": sid}


def test_shifts_are_saved_when_storehub_prints_the_new_time_format(sync, fake, session):
    """In late October 2026 StoreHub started sending 'Oct 28, 2026, 2:03 PM' (plus an ISO time) instead of '10/28/2026 14:03'; the shifts must still be saved."""
    seeded(sync, fake, session, days=2, qty=1)
    alpha = session.scalar(select(Location).where(Location.name.like("%Alpha%")))
    new_style = {"0": "Oct 28, 2026, 2:03 PM", "1": "Oct 28, 2026, 10:10 PM", "2": alpha.name, "3": "5", "4": "132", "5": "13588.00", "6": "0.00", "7": "", "DT_RowId": "n1",
                 "openTimeIso": "2026-10-28T14:03:04+08:00", "closeTimeIso": "2026-10-28T22:10:41+08:00"}
    text_only = dict(new_style, DT_RowId="n2", **{"0": "Oct 29, 2026, 5:57 AM", "1": "Oct 29, 2026, 10:04 AM"}); text_only.pop("openTimeIso"); text_only.pop("closeTimeIso")
    still_open = dict(new_style, DT_RowId="n3", **{"1": "", "closeTimeIso": ""})
    out = sales_shifts.apply(session, [new_style, text_only, still_open], {})
    assert out["new"] == 3
    got = {s.sh_id: s for s in session.scalars(select(SalesShift))}
    assert got["n1"].opened_at.isoformat() == "2026-10-28T14:03:04" and got["n1"].closed_at.isoformat() == "2026-10-28T22:10:41"
    assert got["n2"].opened_at.isoformat() == "2026-10-29T05:57:00" and got["n2"].closed_at.isoformat() == "2026-10-29T10:04:00"
    assert got["n3"].closed_at is None


def test_the_shift_page_is_read_exactly():
    d = sales_shifts.parse_detail(PAGE)
    assert d["cashier"] == "Torniado Jennifer" and d["opening_fund"] == 450.0
    assert d["payments"] == {"Cash": [77, 9300.0], "Credit Card": [0, 0.0], "GCASH": [5, 2966.0]}
    assert d["withdrawals_qty"] == 2 and d["withdrawals"] == 1071.0 and d["discount_total"] == 25.5 and d["tax_total"] == 1314.09
    assert d["void_total"] == 50.0 and d["refund_total"] == 0.0
    empty = sales_shifts.parse_detail("nothing useful")
    assert empty["cashier"] == "" and empty["payments"] == {} and empty["opening_fund"] is None


def test_shifts_are_saved_updated_and_closed_ones_are_not_read_twice(sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A")).name
    calls = []

    def fetch(start, end, skip, per_run):
        calls.append(set(skip))
        rows = [row("a1", "10/24/2026 06:01", "10/24/2026 14:12", alpha), row("a2", "10/24/2026 14:20", "", alpha, "3", "300.00"),
                row("zz", "10/24/2026 07:00", "10/24/2026 08:00", "Some Other Shop")]
        return rows, {s["DT_RowId"]: PAGE for s in rows if s["DT_RowId"] not in skip}

    out = sales_shifts.run(session, fetch=fetch)
    assert out["ok"] and out["listed"] == 3 and out["new"] == 2   # the shop that is not one of ours is ignored
    a1 = session.scalar(select(SalesShift).where(SalesShift.sh_id == "a1"))
    assert a1.total_sales == 12266.0 and a1.tx_count == 82 and a1.cashier == "Torniado Jennifer" and a1.detail_ok
    a2 = session.scalar(select(SalesShift).where(SalesShift.sh_id == "a2"))
    assert a2.closed_at is None                            # still open
    assert sales_shifts.pending(session) == 1              # the open shift is read again next time


def test_the_page_and_csv(client, sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    today = today_local()
    stamp = f"{today:%m/%d/%Y}"
    sales_shifts.apply(session, [row("a1", f"{stamp} 06:01", f"{stamp} 14:12", alpha.name)], {"a1": PAGE})
    session.commit()
    add_user(session, "boss", "admin")
    login(client, "boss")
    r = client.get(f"/sales/shifts?loc={alpha.id}&days=7")
    assert r.status_code == 200 and "Torniado Jennifer" in r.text and "12,266.00" in r.text and "GCASH" in r.text
    csv = client.get(f"/sales/shifts?loc={alpha.id}&days=7&export=csv")
    assert csv.status_code == 200 and "Torniado Jennifer" in csv.text and "12266.00" in csv.text and "sales-by-shift" in csv.headers["content-disposition"]
    assert "Sales by shift" in client.get("/sales").text      # the menu has it


def test_the_page_needs_a_sign_in(client):
    r = client.get("/sales/shifts")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_cashier_link_filters_exact_overnight_shift(client, sync, fake, session):
    import re
    from html import unescape
    from datetime import datetime
    from app.models import SalesTx
    from tests.test_sales_tx_pages import _seed_sales
    _seed_sales(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    today = today_local()
    yesterday = today - timedelta(days=1)
    sales_shifts.apply(session, [row("link", f"{yesterday:%m/%d/%Y} 22:00", f"{today:%m/%d/%Y} 06:00", alpha.name)],
                       {"link": PAGE.replace("Torniado Jennifer", "Reyes Ana")})
    shift = session.scalar(select(SalesShift).where(SalesShift.sh_id == "link"))
    # Philippine shift 22:00-06:00 becomes UTC 14:00-22:00 on the previous day.
    base = datetime.combine(yesterday, datetime.min.time())
    for invoice, hour, minute, employee, location in [
        ("BEFORE", 13, 59, "E1", alpha.id), ("AT-OPEN", 14, 0, "E1", alpha.id),
        ("AFTER-MIDNIGHT", 18, 30, "E1", alpha.id), ("AT-CLOSE", 22, 0, "E1", alpha.id),
        ("OTHER-EMP", 17, 0, "E2", alpha.id),
    ]:
        session.add(SalesTx(refid=invoice, tx_type="Sale", raw_hash="test", invoice_no=invoice, location_id=location,
                            employee_sh_id=employee, tx_time=base + timedelta(hours=hour, minutes=minute),
                            business_date=today, total=10, subtotal=10))
    session.commit()
    add_user(session, "shiftboss", "admin")
    login(client, "shiftboss")
    response = client.get("/sales/shifts?days=7")
    link = re.search(r'<a href="([^"]+)">Reyes Ana</a>', response.text)
    assert link
    url = unescape(link.group(1))
    assert url == f"/sales/transactions?shift_id={shift.id}"
    for suffix in ("", "&export=csv", "&emp=E2&loc=0&days=90"):
        sales = client.get(url + suffix)
        assert sales.status_code == 200
        assert "AT-OPEN" in sales.text and "AFTER-MIDNIGHT" in sales.text
        for excluded in ("BEFORE", "AT-CLOSE", "OTHER-EMP"):
            assert excluded not in sales.text
    assert "Asia/Manila" in client.get(url).text
    assert f'name="shift_id" value="{shift.id}"' in client.get(url).text
    assert client.get("/sales/transactions?shift_id=999999").status_code == 404
    shift.closed_at = None
    session.commit()
    open_sales = client.get(url)
    assert open_sales.status_code == 200 and "AT-CLOSE" in open_sales.text
    beta = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    add_user(session, "otherstore", "store_manager", [beta.id])
    login(client, "otherstore")
    assert client.get(url).status_code == 403



def test_cashier_matching_never_guesses_duplicate_or_unknown_names(session):
    from app.models import Employee
    session.add_all([
        Employee(storehub_id="match1", first_name="Ana", last_name="Reyes"),
        Employee(storehub_id="match2", first_name="Ana", last_name="Reyes"),
        Employee(storehub_id="match3", first_name="Bong", last_name="Go"),
        Employee(storehub_id="loyverse:1", source="loyverse", first_name="Bong", last_name="Go"),
    ])
    session.flush()
    rows = [SalesShift(id=1, cashier="Ana Reyes"), SalesShift(id=2, cashier="  GO,  Bong "),
            SalesShift(id=3, cashier="Unknown"), SalesShift(id=4, cashier="")]
    assert sales_shifts.cashier_employee_ids(session, rows) == {2: "match3"}


def test_unreadable_times_fail_loudly_and_a_gap_is_caught_up_quickly(sync, fake, session):
    """If StoreHub changes its time format again, the run must say so (not report success), and a store whose sales are ahead of its shifts is 'behind'."""
    seeded(sync, fake, session, days=3, qty=1)
    alpha = session.scalar(select(Location).where(Location.name.like("%Alpha%")))
    broken = {"0": "sometime on Tuesday", "1": "", "2": alpha.name, "3": "5", "4": "10", "5": "100.00", "6": "0.00", "7": "", "DT_RowId": "bad1"}
    out = sales_shifts.run(session, fetch=lambda start, end, skip, n: ([broken], {}))
    assert out["ok"] is False and out["unreadable"] == 1 and "could not be read" in out["error"]
    assert session.get(sales_shifts.Setting, "sales_shifts.last_ok") is None                 # a failed run is never marked as done
    t = today_local()
    session.add(SalesShift(sh_id="old", location_id=alpha.id, opened_at=__import__("datetime").datetime.combine(t - timedelta(days=9), __import__("datetime").time(8, 0)), closed_at=None))
    session.commit()
    assert alpha.name in sales_shifts.behind(session)


def test_status_filter_splits_open_and_closed_shifts(client, sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    today = today_local()
    stamp = f"{today:%m/%d/%Y}"
    sales_shifts.apply(
        session,
        [
            row("open1", f"{stamp} 06:00", "", alpha.name, n="40", total="5000.00"),
            row("closed1", f"{stamp} 06:01", f"{stamp} 14:12", alpha.name, n="82", total="12266.00"),
        ],
        {
            "open1": PAGE.replace("Torniado Jennifer", "Reyes Ana").replace("12,266.00", "5,000.00"),
            "closed1": PAGE,
        },
    )
    session.commit()
    add_user(session, "boss", "admin")
    login(client, "boss")

    open_only = client.get(f"/sales/shifts?loc={alpha.id}&days=7&status=open")
    assert open_only.status_code == 200
    assert "Reyes Ana" in open_only.text and "Torniado Jennifer" not in open_only.text
    assert "Open (1)" in open_only.text and "Closed (1)" in open_only.text

    closed_only = client.get(f"/sales/shifts?loc={alpha.id}&days=7&status=closed")
    assert closed_only.status_code == 200
    assert "Torniado Jennifer" in closed_only.text and "Reyes Ana" not in closed_only.text

    open_csv = client.get(f"/sales/shifts?loc={alpha.id}&days=7&status=open&export=csv")
    assert open_csv.status_code == 200
    assert "Reyes Ana" in open_csv.text and "Torniado Jennifer" not in open_csv.text

