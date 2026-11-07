"""The transaction slide-over on the Transactions list loads a compact receipt."""
from sqlalchemy import select

from app.models import SalesTx
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


def test_the_panel_shows_a_compact_receipt(client, sync, fake, session):
    seeded(sync, fake, session, days=3, qty=2)
    add_user(session, "panel_admin", "admin"); login(client, "panel_admin")
    tx = session.scalar(select(SalesTx).where(SalesTx.cancelled.is_(False)))
    r = client.get(f"/sales/tx/{tx.id}/panel")
    assert r.status_code == 200 and 'class="txp"' in r.text and "txp-total" in r.text
    assert client.get("/sales/transactions").status_code == 200
    assert client.get("/sales/tx/999999/panel").status_code == 404
