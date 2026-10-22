"""Daily sales links must keep both the business date and store scope."""
import csv
import io
import re
from datetime import timedelta
from html import unescape

from app.models import Location, Product, SalesDay, SalesDayProduct
from app.timeutil import today_local
from tests.test_web import add_user, client, login  # noqa: F401


def test_daily_sales_opens_products_for_only_that_day_and_store(client, session, monkeypatch):
    # Force pagination so summary totals must include products beyond page one.
    monkeypatch.setattr("app.web.routes_stock.PAGE", 1)
    add_user(session, "sales-boss", "management")
    stores = [Location(storehub_id=f"drill-{i}", name=f"Store {i}", monitored=True) for i in range(2)]
    product = Product(storehub_id="drill-product", name="Daily product")
    second_product = Product(storehub_id="second-product", name="Second daily product")
    later_product = Product(storehub_id="later-product", name="Later product")
    session.add_all([*stores, product, second_product, later_product])
    session.flush()
    chosen = today_local() - timedelta(days=2)
    session.add(SalesDay(location_id=stores[0].id, business_date=chosen, net_sales=50, tx_count=1))
    for store, day, item, units, net in [
        (stores[0], chosen, product, 3, 30),
        (stores[0], chosen, second_product, 2, 20),
        (stores[0], chosen - timedelta(days=1), product, 10, 100),
        (stores[0], chosen + timedelta(days=1), product, 20, 200),
        (stores[0], chosen + timedelta(days=1), later_product, 40, 400),
        (stores[1], chosen, product, 30, 300),
    ]:
        session.add(SalesDayProduct(location_id=store.id, business_date=day,
                                   product_id=item.id, units=units, net_sales=net))
    session.commit()
    login(client, "sales-boss")
    overview = client.get(f"/sales?loc={stores[0].id}&days=7")
    assert overview.status_code == 200
    url = unescape(re.search(r'<tr data-href="([^"]+)"', overview.text).group(1))
    assert url == f"/sales/products?loc={stores[0].id}&days=custom&frm={chosen}&to={chosen}"
    assert f'href="{url.replace("&", "&amp;")}"' in overview.text
    report = client.get(url)
    assert report.status_code == 200
    assert "Daily product" in report.text and "Later product" not in report.text
    assert f'name="frm" value="{chosen}"' in report.text
    assert f'name="to" value="{chosen}"' in report.text
    def summary_values(html):
        return {unescape(re.sub(r"<[^>]+>", "", label)): unescape(re.sub(r"<[^>]+>", "", value)) for label, value in
                re.findall(r'<div class="label">(.*?)</div><div class="value">(.*?)</div>', html, re.S)}

    summary = summary_values(report.text)
    assert summary["Products sold"] == "2"
    assert summary["Units sold"] == "5"
    assert summary["Sales after returns and discounts"] == "50.00"
    assert next(iter(summary_values(overview.text).values())) == "50.00"
    second_page = client.get(url + "&page=2")
    assert second_page.status_code == 200
    assert "Second daily product" in second_page.text
    assert "Later product" not in second_page.text
    assert summary_values(second_page.text) == summary
    exported = client.get(url + "&export=csv")
    rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert len(rows) == 2
    assert rows[0]["Units sold"] == "3"
    assert rows[0]["Net sales"] == "30.00"
    assert sum(float(row["Net sales"]) for row in rows) == 50
    assert sum(float(row["Units sold"]) for row in rows) == 5
