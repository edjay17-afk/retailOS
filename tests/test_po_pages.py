"""The desktop Purchase orders list (groups, due dates) and a purchase order's delivery steps."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Location, PoLine, Product, PurchaseOrder, Receiving, Supplier
from app.timeutil import now_utc
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


def _orders(sync, fake, session):
    seeded(sync, fake, session)
    loc = session.scalar(select(Location).where(Location.storehub_id == 'S_A'))
    sup = Supplier(name='Zenwok Trading')
    prod = session.scalar(select(Product).where(Product.source == 'storehub'))
    session.add(sup); session.flush()
    now = now_utc()
    made = {}
    for n, status, days, total in ((7001, 'open', 2, 500.0), (7002, 'open', 0, 300.0), (7003, 'open', -30, 900.0), (7004, 'completed', -5, 120.0)):
        po = PurchaseOrder(sh_po_id=n, location_id=loc.id, supplier_id=sup.id, status=status, created_sh=now - timedelta(days=9), expected_at=now + timedelta(days=days), total=total)
        session.add(po); session.flush()
        session.add(PoLine(po_id=po.id, product_id=prod.id, product_sh_id=prod.storehub_id, ordered_qty=10, received_qty=(7 if status == 'completed' else 0), received_present=status == 'completed', supplier_price=50))
        made[n] = po
    session.commit()
    return made


def test_the_list_groups_by_need_without_a_summary_banner(client, sync, fake, session):
    _orders(sync, fake, session)
    add_user(session, 'po_admin', 'admin'); login(client, 'po_admin')
    page = client.get('/purchase-orders').text
    assert 'po-big' not in page and 'po-facts' not in page and 'po-note' not in page    # the summary banner was removed by request
    assert page.index('class="po-group stale"') < page.index('class="po-group incoming"')   # needs attention first
    assert 'Due today' in page and 'Due in 2 days' in page and '30 days late' in page
    assert '<span>3</span></a>' in page and 'class="po-pill stale"' in page
    closed = client.get('/purchase-orders?view=closed').text
    assert 'class="po-group closed"' in closed and 'PO #7004' in closed and 'class="po-group incoming"' not in closed


def test_a_purchase_order_shows_where_the_delivery_stands(client, sync, fake, session):
    made = _orders(sync, fake, session)
    add_user(session, 'po_admin2', 'admin'); login(client, 'po_admin2')
    page = client.get(f'/purchase-orders/{made[7001].id}').text
    assert 'class="step done"' in page and 'Back to purchase orders' in page and 'Due in 2 days' in page
    assert 'No local count yet' in page and 'class="po-go"' in page
    session.add(Receiving(po_id=made[7001].id, location_id=made[7001].location_id, status='open')); session.commit()
    assert 'class="step now"' in client.get(f'/purchase-orders/{made[7001].id}').text
    done = client.get(f'/purchase-orders/{made[7004].id}').text
    assert 'class="step done"' in done and 'class="po-go"' not in done and 'class="po-short"' in done        # received 7 of 10: short
    stale = client.get(f'/purchase-orders/{made[7003].id}').text
    assert 'class="po-notice"' in stale and '30 days late' in stale
