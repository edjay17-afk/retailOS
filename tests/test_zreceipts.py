"""Z-reading receipts: exact official fields, 80mm single/two-up A4 bulk printing and access control."""
from datetime import datetime

from sqlalchemy import select

from app.models import Location, ZReading
from app.services import zreceipts
from app.timeutil import today_local
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


SOURCE = """Z-Reading Details
General
Store Name:

The Daily Centro - Getafe

Operated By:

ZENWOK CORPORATION

Accred:

043-0100-36922-202505-2303

MIN:

26080618132972818

PTU:

FP082026-084-0627205-00008

S/N:

DP0225CS10659

Report Date:

10/26/2026

Report Time:

05:51:47 AM

Start Date & Time:

10/26/26 12:00:00 AM

End Date & Time:

10/26/26 05:51:47 AM

Reset Counter No.:

0

Z Count No.:

4
"""


def table(*rows):
    return {"rows": [list(row) for row in rows]}


SECTIONS = [
    {"title": "General", "tables": [table(
        ("Beg. SI #:", "00400000702"), ("End. SI #:", "00400000711"), ("Beg. VOID #:", "N/A"), ("End. VOID #:", "N/A"),
        ("Beg. REFUND #:", "N/A"), ("End. REFUND #:", "N/A"), ("Beg. TRANS #:", "00400000702"), ("End. TRANS #:", "00400000711"),
        ("TRANS. Count:", "10"), ("SALES Count:", "10"), ("VOID Count:", "0"), ("REFUND Count:", "0"),
    )]},
    {"title": "Breakdown of Sales", "tables": [table(
        ("Present Accumulated Sales:", "80,143.00"), ("Previous Accumulated Sales:", "79,374.00"), ("Sales for the Day:", "769.00"),
        ("Present Accumulated Net Sales:", "80,143.00"), ("Previous Accumulated Net Sales:", "79,374.00"),
        ("VATABLE SALES:", "686.58"), ("VAT AMOUNT:", "82.42"), ("VAT EXEMPT SALES:", "0.00"), ("ZERO RATED SALES:", "0.00"),
        ("Gross Amount:", "769.00"), ("Less Discount:", "1.00"), ("Less Refund:", "0.00"), ("Less Void:", "0.00"),
        ("Less VAT Adjustment:", "0.00"), ("Net Amount:", "768.99"),
    )]},
    {"title": "Discount Summary", "tables": [table(
        ("SC Disc.:", "0.00"), ("PWD Disc.:", "0.00"), ("NAAC Disc.:", "0.00"), ("Medal of Valor Holder Disc.:", "0.00"),
        ("Solo Parent Disc.:", "0.00"), ("Other Disc.:", "0.00"), ("Total Discount:", "9.99"),
    )]},
    {"title": "Sales Adjustment", "tables": [table(("VOID:", "0.00"), ("Refund:", "0.00"), ("Total Sales Adjustments:", "8.88"))]},
    {"title": "VAT Adjustment", "tables": [table(
        ("SC TRANS.:", "0.00"), ("PWD TRANS.:", "0.00"), ("Solo Parent TRANS.:", "0.00"), ("REG. Disc. TRANS.:", "0.00"),
        ("ZERO-RATED TRANS.:", "0.00"), ("VAT on Refund:", "0.00"), ("VAT on VOID:", "0.00"),
        ("Other VAT Adjustments:", "0.00"), ("Total VAT Adjustments:", "7.77"),
    )]},
    {"title": "Collections Summary", "tables": [table(
        ("Payment Method", "Transactions", "Amount (PHP)"), ("Cash", "10", "769.00"), ("Total Collection", "11", "770.00"),
    )]},
    {"title": "Transaction Summary", "tables": [table(
        ("Cash in Drawer:", "3,016.00"), ("GCASH:", "82.42"), ("Opening Fund:", "450.00"), ("Pay In:", "0.00"),
        ("Less Withdrawal:", "0.00"), ("Payment Received:", "2,566.00"), ("Expected Drawer:", "3,016.00"), ("Short/Over:", "0.00"),
    )]},
]


def make_reading(session, suffix="receipt1"):
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    row = ZReading(sh_id=suffix, location_id=loc.id, close_at=datetime.combine(today_local(), datetime.min.time()).replace(hour=5, minute=51),
                   z_count=4, register="5", tx_count=10, gross_sales=769.0, detail_ok=True, detail_json=SECTIONS, source_text=SOURCE)
    session.add(row)
    session.commit()
    return row


def test_bulk_print_excludes_interims_and_keeps_selected_period(client, sync, fake, session):
    from datetime import timedelta
    seeded(sync, fake, session)
    early = make_reading(session, 'early-slip')
    early.source_text += '\nBeg. SI #:\t00400000702'
    final = make_reading(session, 'final-slip')
    final.close_at = early.close_at.replace(hour=23, minute=59)
    final.source_text = early.source_text.replace('05:51:47 AM', '11:59:00 PM')
    session.commit()
    add_user(session, 'print_admin', 'admin'); login(client, 'print_admin')

    response = client.get(f'/sales/zreadings/receipts?loc={early.location_id}&days=1')
    assert response.status_code == 200
    assert '11:59:00 PM' in response.text
    assert '05:51:47 AM' not in response.text
    assert response.text.count('<article class="receipt">') == 1

    # If the replacement is outside the selected period, do not pull it in
    # or fall back to printing its interim predecessor.
    final.close_at += timedelta(days=1)
    session.commit()
    response = client.get(f'/sales/zreadings/receipts?loc={early.location_id}&days=1')
    assert response.status_code == 200
    assert '11:59:00 PM' not in response.text and '05:51:47 AM' not in response.text


def test_mapper_uses_real_store_registration_and_official_totals(sync, fake, session):
    seeded(sync, fake, session)
    data = zreceipts.receipt_data(make_reading(session))
    assert data["store"] == "The Daily Centro - Getafe"
    assert data["min"] == "26080618132972818" and data["ptu"] == "FP082026-084-0627205-00008" and data["sn"] == "DP0225CS10659"
    assert data["min"] != "26072418034265750"  # not the mini app's Tabunoc placeholder
    assert data["reportDate"] == "10/26/2026" and data["reportTime"] == "05:51:47 AM"
    assert data["startDateTime"] == "10/26/26 12:00:00 AM" and data["endDateTime"] == "10/26/26 05:51:47 AM"
    assert dict(data["counters"])["Z Count No.:"] == "4"
    assert dict(data["breakdown"])["Net Amount"] == "768.99"  # the official total, not a recalculation
    assert dict(data["discount"])["Total Discount"] == "9.99"
    assert dict(data["salesAdjustment"])["Total Sales Adjustments"] == "8.88"
    assert dict(data["vatAdjustment"])["Total VAT Adjustments"] == "7.77"
    assert data["collections"] == [{"method": "Cash", "count": "10", "amount": "769.00"}]
    assert data["collectionTotal"] == {"count": "11", "amount": "770.00"}
    assert ("GCASH:", "82.42") in data["transaction"]


def test_single_and_bulk_receipt_routes_and_access(client, sync, fake, session):
    seeded(sync, fake, session)
    row = make_reading(session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    beta = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    add_user(session, "boss", "admin")
    login(client, "boss")

    single = client.get(f"/sales/zreadings/{row.sh_id}/receipt")
    assert single.status_code == 200 and "@page { size: 80mm 255mm" in single.text
    assert "26080618132972818" in single.text and "768.99" in single.text and "GCASH:" in single.text
    assert "Print / Save PDF" in single.text
    listing = client.get("/sales/zreadings")
    assert listing.status_code == 200 and "Print slips" in listing.text and f"/sales/zreadings/{row.sh_id}/receipt" in listing.text
    detail = client.get(f"/sales/zreadings/{row.sh_id}")
    assert detail.status_code == 200 and "80mm receipt" in detail.text

    make_reading(session, "receipt2")
    make_reading(session, "receipt3")
    bulk = client.get(f"/sales/zreadings/receipts?loc={alpha.id}&days=7")
    assert bulk.status_code == 200 and "Z-READING REPORT" in bulk.text and "26080618132972818" in bulk.text
    assert "@page { size: A4 portrait; margin: 10mm; }" in bulk.text
    assert bulk.text.count('class="receipt"') == 3 and bulk.text.count('class="receipt-sheet"') == 2  # two per A4 sheet, one odd receipt on the last
    other = client.get(f"/sales/zreadings/receipts?loc={beta.id}&days=7")
    assert other.status_code == 200 and "Z-READING REPORT" not in other.text

    pending = ZReading(sh_id="pending-receipt", location_id=alpha.id, close_at=row.close_at, z_count=99, detail_ok=False)
    session.add(pending)
    session.commit()
    assert client.get("/sales/zreadings/pending-receipt/receipt").status_code == 409

    add_user(session, "staff", "staff")
    login(client, "staff")
    assert client.get(f"/sales/zreadings/{row.sh_id}/receipt").status_code == 403
    assert client.get("/sales/zreadings/receipts").status_code == 403

    add_user(session, "scoped", "admin", locs=[beta.id])
    login(client, "scoped")
    assert client.get(f"/sales/zreadings/{row.sh_id}/receipt").status_code == 403
    assert client.get(f"/sales/zreadings/receipts?loc={alpha.id}").status_code == 403


def test_receipt_routes_need_a_sign_in(client):
    response = client.get("/sales/zreadings/receipts")
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
    response = client.get("/sales/zreadings/some-id/receipt")
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
