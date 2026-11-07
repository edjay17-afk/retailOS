"""7-Eleven Inventory Logs and Bad Merchandise slips: read exactly, refused when the numbers do not add up, saved once, shown to admin and management only."""
import pytest
from sqlalchemy import func, select

from app.models import SevenBadSlip, SevenLogLine
from app.services import seven_eleven as sv
from tests.test_web import add_user, client, csrf_of, login  # noqa: F401

SLIP = """:Store :
BAD MERCHANDISE
PHILIPPINE SEVEN CORPORATION
Print Date
Status
BM Slip Date :
:
BM Slip ID :
11/05/2026 11.24 AM
10/01/2026
COMPLETED:Document ID
6216-Poblacion Pardo 2
3110621716271897467
F1Y5PR5D
Itemcode Quantity Total
Cost
Unit
RetailNo. Total
RetailProduct Name Unit
Cost
SK Spicy Beef Crunchy Pan (Store Ordering) -2 27.00 40.00 -54.00 -80.00360100431
SK 1pc Fried Chicken -3 54.00 92.00 -162.00 -276.00398000062
TOTAL: -216.00 -356.00
Page 1 of 1Signature over Printed Name
"""

LOG = """Print Date
:
Date From :
INVENTORY TRANSACTION LOG
:
Date To
:
PHILIPPINE SEVEN CORPORATION
Store 6216-Poblacion Pardo 2 11/05/2026 10:56 AM
August 01, 2026
August 01, 2026
Delivered By RetailDocument IDTransaction Date Date Completed Cost
WAREHOUSE TRANSFER
08/01/2026 08/02/2026 07:31 AM CEB3189870 Convenience Distribution Inc Cebu [CD2] 15,693.0011,213.86
08/01/2026 08/02/2026 07:30 AM CEB3189491 Chilled 2 [CH2] 12,470.008,079.50
Total: 19,293.36 28,163.00
BAD MERCHANDISE
08/01/2026 08/02/2026 07:39 AM EYJ5SAT3 *BM (574.00)(360.42)
Total: (360.42) (574.00)
PRICE CHANGE
08/01/2026 08/02/2026 07:16 AM EYQYMQXQ *PC 1,319.000.00
Total: 0.00 1,319.00
GRAND TOTAL: 18,932.94 28,908.00
"""


def test_a_slip_is_read_exactly():
    d = sv.parse_slip(SLIP)
    assert (d.slip_id, str(d.slip_date), d.store_code, d.total_cost, d.total_retail) == ("F1Y5PR5D", "2026-10-01", "6216", -216.0, -356.0)
    assert [(i["line_no"], i["item_code"], i["qty"], i["total_cost"]) for i in d.items] == [(1, "36010043", -2.0, -54.0), (2, "39800006", -3.0, -162.0)]


def test_a_log_with_every_section_including_price_change_adds_up():
    d = sv.parse_log(LOG)
    assert [r["section"] for r in d.lines] == ["WAREHOUSE TRANSFER", "WAREHOUSE TRANSFER", "BAD MERCHANDISE", "PRICE CHANGE"]
    assert d.lines[0]["retail"] == 15693.0 and d.lines[0]["cost"] == 11213.86 and d.lines[2]["cost"] == -360.42 and d.lines[3]["retail"] == 1319.0


def test_numbers_that_do_not_add_up_are_refused():
    with pytest.raises(sv.SevenError):
        sv.parse_slip(SLIP.replace("TOTAL: -216.00 -356.00", "TOTAL: -216.00 -350.00"))
    with pytest.raises(sv.SevenError):
        sv.parse_log(LOG.replace("Total: 19,293.36 28,163.00", "Total: 19,293.36 28,000.00"))
    with pytest.raises(sv.SevenError):
        sv.parse_slip(SLIP.replace("-54.00 -80.00", "-55.00 -80.00"))


def test_upload_saves_once_and_only_admin_and_management_may_see_it(client, session, monkeypatch):
    texts = {b"%PDF-slip": SLIP, b"%PDF-log": LOG}
    monkeypatch.setattr(sv, "pdf_text", lambda raw: texts[raw])
    add_user(session, "staff_s", "staff"); login(client, "staff_s")
    assert client.get("/seven-eleven").status_code == 403
    add_user(session, "boss_s", "management"); login(client, "boss_s"); token = csrf_of(client, "/")
    def up(*names):
        return client.post("/seven-eleven/upload", data={"csrf": token}, files=[("files", (n + ".pdf", n.encode(), "application/pdf")) for n in names], follow_redirects=False)
    r = up("%PDF-slip", "%PDF-log")
    assert r.status_code in (302, 303) and "ok=" in r.headers["location"]
    again = up("%PDF-slip", "%PDF-log")
    assert "already+saved" in again.headers["location"].replace("%20", "+") and "2+already" in again.headers["location"].replace("%20", "+")
    assert session.scalar(select(func.count()).select_from(SevenBadSlip)) == 1 and session.scalar(select(func.count()).select_from(SevenLogLine)) == 4
    page = client.get("/seven-eleven").text
    assert "2026-08" in page and "1,319.00" not in page.split("Bad merchandise")[0]
    assert "F1Y5PR5D" in client.get("/seven-eleven?tab=slips&frm=2026-10-01&to=2026-10-31").text
    slip = session.scalar(select(SevenBadSlip))
    assert "SK 1pc Fried Chicken" in client.get(f"/seven-eleven/slip/{slip.id}").text
    assert "Chilled 2" in client.get("/seven-eleven?tab=log&frm=2026-08-01&to=2026-08-31").text
    assert "Convenience Distribution" not in client.get("/seven-eleven?tab=log&frm=2026-08-01&to=2026-08-31&section=BAD+MERCHANDISE").text
    assert client.get("/seven-eleven?tab=log&frm=2026-08-01&to=2026-08-31&export=csv").status_code == 200
    bad = client.post("/seven-eleven/upload", data={"csrf": token}, files=[("files", ("x.pdf", b"not a pdf", "application/pdf"))], follow_redirects=False)
    assert "err=" in bad.headers["location"]


SALES = """Print Date :
PHILIPPINE SEVEN CORPORATION
:
:Store
OR SALES MONITOR
Date From
Date To :
6216-Poblacion Pardo 2
11/04/2026
11/04/2026
11/05/2026 8.17 AM
Itemcode AmountQuantityBarcode Product Name UPSD
Category : 10-Grocery
10820075 00748485100418 Century Tuna Hot and Spicy EOC 155g 6 294.00 6.00
10850052 04806517043457 Goya Everyday Instant Powdered Chocolate Drink 26g 2 36.00 2.00
Total: 8 330.00
Category : EX-Expense Items
EX000589 04806518315619 Alas Siete Deals Sticker 2 0.00 2.00
Total: 2 0.00
GRAND TOTAL: 10 330.00
"""


def test_the_or_sales_report_is_read_exactly_including_expense_items():
    d = sv.parse_sales(SALES)
    assert (d.store_code, str(d.day), d.grand) == ("6216", "2026-11-04", (10.0, 330.0))
    assert [(r["category"], r["item_code"], r["qty"], r["amount"]) for r in d.rows] == [("10-Grocery", "10820075", 6.0, 294.0), ("10-Grocery", "10850052", 2.0, 36.0), ("EX-Expense Items", "EX000589", 2.0, 0.0)]


def test_a_sales_report_that_does_not_add_up_or_spans_days_is_refused():
    with pytest.raises(sv.SevenError):
        sv.parse_sales(SALES.replace("Total: 8 330.00", "Total: 8 300.00"))
    with pytest.raises(sv.SevenError):
        sv.parse_sales(SALES.replace("11/04/2026\n11/04/2026", "11/01/2026\n11/04/2026"))


def test_sales_are_saved_once_and_shown_per_product_with_units_per_day(client, session, monkeypatch):
    from app.models import SevenSaleLine
    day2 = SALES.replace("11/04/2026\n11/04/2026", "11/03/2026\n11/03/2026").replace("10820075 00748485100418 Century Tuna Hot and Spicy EOC 155g 6 294.00 6.00", "10820075 00748485100418 Century Tuna Hot and Spicy EOC 155g 4 196.00 4.00").replace("Total: 8 330.00", "Total: 6 232.00").replace("GRAND TOTAL: 10 330.00", "GRAND TOTAL: 8 232.00")
    texts = {b"%PDF-d1": SALES, b"%PDF-d2": day2}
    monkeypatch.setattr(sv, "pdf_text", lambda raw: texts[raw])
    add_user(session, "sales_boss", "admin"); login(client, "sales_boss"); token = csrf_of(client, "/")
    for _ in range(2):
        client.post("/seven-eleven/upload", data={"csrf": token}, files=[("files", (n + ".pdf", n.encode(), "application/pdf")) for n in ("%PDF-d1", "%PDF-d2")], follow_redirects=False)
    assert session.scalar(select(func.count()).select_from(SevenSaleLine)) == 6          # the second upload added nothing
    page = client.get("/seven-eleven?tab=sales&frm=2026-11-03&to=2026-11-04").text
    assert "Century Tuna" in page and "2 trading days" in page.replace("&nbsp;", " ") or "2 trading" in page
    assert ">5<" in page.replace(" ", "") or "5.00" in page                      # (6 + 4) units over 2 trading days = 5 a day
    assert "Century" not in client.get("/seven-eleven?tab=sales&frm=2026-11-03&to=2026-11-04&category=EX-Expense+Items").text
    csv = client.get("/seven-eleven?tab=sales&frm=2026-11-03&to=2026-11-04&export=csv")
    assert csv.status_code == 200 and "Units per sales day" in csv.text and "10820075" in csv.text


def _sales(session, code, name, per_day_by_date, category="10-Grocery"):
    from app.models import SevenSaleLine
    for d, q in per_day_by_date.items():
        session.add(SevenSaleLine(store_code="6216", store_name="Poblacion Pardo 2", sale_date=d, category=category, item_code=code, barcode="0" * 13, name=name, qty=q, amount=q * 10))


def test_order_suggestions_follow_the_rules_with_exact_numbers(client, session):
    from datetime import date, timedelta
    from app.models import SevenBadItem, SevenBadSlip
    from app.services import seven_replenishment as srep
    end = date(2026, 11, 4)
    days = [end - timedelta(days=n) for n in range(28)]
    _sales(session, "10000001", "Steady Item", {d: 6 for d in days})                                      # 6 a day
    _sales(session, "10000002", "Slow Item", {days[3]: 1, days[10]: 1})                                  # 2 units in 28 days = 0.07 a day
    _sales(session, "10000003", "Rising Item", {d: (2 if d < end - timedelta(days=6) else 10) for d in days})
    _sales(session, "10000004", "Wasty Item", {d: 2 for d in days})
    _sales(session, "EX000589", "Promo Sticker", {d: 3 for d in days}, category="EX-Expense Items")
    _sales(session, "30000001", "Bottle Deposit", {d: 3 for d in days}, category="30-Deposits")
    for d in days[1:]:                                                                                   # every day has some sale, so there are 28 trading days
        pass
    slip = SevenBadSlip(slip_id="W1", document_id="1", store_code="6216", store_name="x", slip_date=end - timedelta(days=5), status="COMPLETED", total_cost=0, total_retail=0)
    session.add(slip); session.flush()
    session.add(SevenBadItem(slip_id=slip.id, line_no=1, item_code="10000004", name="Wasty Item", qty=-12, unit_cost=1, unit_retail=1, total_cost=-12, total_retail=-12))
    session.commit()
    data = srep.suggest(session, cover_days=3, lookback_days=28)
    by = {r["code"]: r for r in data["rows"]}
    assert data["meta"]["trading_days"] == 28 and data["meta"]["as_of"] == end
    assert by["10000001"]["order"] == 18 and by["10000001"]["per_day"] == 6.0                              # 6 x 3 days
    assert by["10000002"]["order"] == 0 and "Slow mover" in by["10000002"]["flags"]                       # 0.07 x 3 = 0.21 < 0.5
    assert abs(by["10000003"]["per_day"] - (21 * 2 + 7 * 10) / 28) < 1e-9 and "Selling faster lately" in by["10000003"]["flags"]
    assert by["10000004"]["waste"] == 12 and "High waste" in by["10000004"]["flags"] and by["10000004"]["review"]   # 12 wasted of 56 sold
    assert "EX000589" not in by and "30000001" not in by                                                 # expense items and deposits are not ordered
    assert data["meta"]["to_order"] == 3 and data["meta"]["units"] == 18 + by["10000003"]["order"] + by["10000004"]["order"]


def test_the_order_page_csv_and_settings(client, session):
    from datetime import date, timedelta
    end = date(2026, 11, 4)
    _sales(session, "10000001", "Steady Item", {end - timedelta(days=n): 6 for n in range(28)})
    session.commit()
    add_user(session, "order_staff", "staff"); login(client, "order_staff")
    assert client.get("/seven-eleven?tab=order").status_code == 403
    add_user(session, "order_boss", "management"); login(client, "order_boss"); token = csrf_of(client, "/")
    page = client.get("/seven-eleven?tab=order").text
    assert "Steady Item" in page and "Sales only" in page and "<b style=\"font-size:16px\">18</b>" in page
    assert client.post("/seven-eleven/settings", data={"csrf": token, "cover_days": "5", "lookback_days": "28"}, follow_redirects=False).status_code in (302, 303)
    assert "<b style=\"font-size:16px\">30</b>" in client.get("/seven-eleven?tab=order").text                # 6 x 5 days
    bad = client.post("/seven-eleven/settings", data={"csrf": token, "cover_days": "0", "lookback_days": "28"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]
    csv = client.get("/seven-eleven?tab=order&export=csv")
    assert csv.status_code == 200 and "Order qty" in csv.text and "10000001" in csv.text and ",30," in csv.text


def test_the_replenishment_sheet_is_not_called_sheet_in_the_breadcrumb(client, session):
    add_user(session, "crumb_admin", "admin"); login(client, "crumb_admin")
    crumbs = client.get("/replenishment/sheet").text.split('<nav class="crumbs"')[1].split("</nav>")[0]
    assert "Auto replenishment" in crumbs and "Sheet" not in crumbs


def test_the_seven_eleven_button_sits_with_the_account_buttons_for_admin_and_management_only(client, session):
    from app.models import Location
    session.add_all([Location(storehub_id="l-a", name="Loy A", type="store", source="loyverse", source_account="A", external_id="a", monitored=True),
                     Location(storehub_id="l-b", name="Loy B", type="store", source="loyverse", source_account="B", external_id="b", monitored=True)])
    session.commit()
    add_user(session, "btn_staff", "staff"); login(client, "btn_staff")
    assert 'class="seven-btn' not in client.get("/").text
    add_user(session, "btn_boss", "admin"); login(client, "btn_boss")
    home = client.get("/").text
    assert 'class="seven-btn ' in home and 'href="/seven-eleven"' in home and "is-active" not in home.split('class="seven-btn')[1].split(">")[0]
    seven = client.get("/seven-eleven").text
    assert 'class="seven-btn is-active"' in seven
    assert 'name="back" value="/"' in seven                  # choosing an account from the 7-Eleven page goes home, not back to 7-Eleven


def test_centavo_rounding_in_a_printed_total_is_allowed_but_a_real_difference_is_not():
    kitchen = """Store 6216-Poblacion Pardo 2 11/05/2026 10:56 AM
June 01, 2026
June 01, 2026
KITCHEN TRANSFER
06/01/2026 06/02/2026 07:29 AM K1 Kitchen Cebu 863.00529.93
06/01/2026 06/02/2026 07:28 AM K2 Kitchen Cebu 635.00385.63
Total: 915.54 1,498.00
GRAND TOTAL: 915.54 1,498.00
"""
    d = sv.parse_log(kitchen)                                   # the lines add up to 915.56; the report prints 915.54: two centavos of rounding
    assert len(d.lines) == 2
    with pytest.raises(sv.SevenError):
        sv.parse_log(kitchen.replace("915.54", "915.00"))        # a difference this size is a real error
