"""Sales by product: Enter in the search box searches; the CSV downloads only run when their own buttons are clicked."""
from app.security import hash_password
from tests.test_ai import ready
from tests.test_web import PW, client, login  # noqa: F401


def test_downloads_are_not_the_forms_default_button(client, sync, fake, session):
    boss = ready(sync, fake, session)
    boss.password_hash = hash_password(PW); session.commit()
    login(client, "boss")
    html = client.get("/sales/products").text
    assert 'data-export="csv"' in html and 'data-export="detailed"' in html
    assert 'type="submit" form="product-sales-filters"' not in html       # a submit button before the search box would be what Enter presses
    assert 'name="q"' in html
    csv = client.get("/sales/products?export=csv")                      # the download itself still works
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]


def test_other_filter_pages_have_no_download_before_the_search():
    from pathlib import Path
    for name in ("stock_transfers.html", "loyverse_replenishment.html", "sales_products.html"):
        text = Path("app/web/templates", name).read_text(encoding="utf8")
        assert 'type="submit" form=' not in text, name


def test_hiding_the_filter_labels_does_not_hide_the_date_picker_presets():
    from pathlib import Path
    css = Path("app/web/static/sales_align.css").read_text(encoding="utf8")
    assert ".sales-filters label{" not in css and ".sales-filters select{" not in css      # these would also reach the picker's presets and month/year boxes
    assert ".sales-filters>div>label{" in css and ".sales-filters>div>select{" in css
