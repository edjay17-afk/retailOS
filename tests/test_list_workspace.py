from html import unescape
from urllib.parse import parse_qs, urlsplit
import re

from sqlalchemy import select

from app.models import Location, PurchaseOrder
from tests.test_services import seeded
from tests.test_web import add_user, client, login


def test_order_pagination_preserves_location(client, session, sync, fake):
    seeded(sync, fake, session)
    location = session.scalar(select(Location))
    session.add_all([
        PurchaseOrder(sh_po_id=90000 + i, location_id=location.id, status="open")
        for i in range(51)
    ])
    session.commit()
    add_user(session, "list-admin", "admin")
    login(client, "list-admin")
    response = client.get(f"/purchase-orders?view=all&loc={location.id}")
    assert response.status_code == 200
    next_link = re.search(r'href="([^"]+)">Next</a>', response.text).group(1)
    query = parse_qs(urlsplit(unescape(next_link)).query)
    assert query == {"view": ["all"], "loc": [str(location.id)], "page": ["2"]}
    assert client.get("/purchase-orders" + unescape(next_link)).status_code == 200


def test_inventory_pagination_encodes_filters_and_excludes_dashboard_styles(client, session, sync, fake):
    seeded(sync, fake, session)
    add_user(session, "list-admin", "admin")
    login(client, "list-admin")
    response = client.get("/inventory", params={"q": "Tea & coffee", "category": "Food & drink", "page": 2})
    assert response.status_code == 200
    previous = re.search(r'href="([^"]+)">Previous</a>', response.text).group(1)
    query = parse_qs(urlsplit(unescape(previous)).query)
    assert query["q"] == ["Tea & coffee"]
    assert query["category"] == ["Food & drink"]
    assert "list_workspace.css" in response.text
    assert "Clear filters" in response.text
    assert "list_workspace.css" not in client.get("/").text
    assert client.get("/static/list_workspace.css").status_code == 200
