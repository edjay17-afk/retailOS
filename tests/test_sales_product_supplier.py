import html
import csv
import io
from sqlalchemy import select
from app.models import Product, Supplier, SalesDayProduct
from app.security import hash_password
from tests.test_ai import ready
from tests.test_web import client, login, PW

def test_supplier_limits_sales_and_export(client,session,sync,fake):
    boss=ready(sync,fake,session);boss.password_hash=hash_password(PW)
    supplier=Supplier(name="Selected supplier")
    session.add(supplier);session.flush()
    product=session.scalar(select(Product).where(Product.storehub_id=="P1"))
    product.supplier_id=supplier.id;session.commit();login(client,"boss")
    url=f"/sales/products?days=60&supplier={supplier.id}"
    r=client.get(url)
    assert r.status_code==200 and 'name="supplier"' in r.text and 'class="drp"' in r.text
    assert 'data-export="csv"' in r.text
    rows=list(csv.DictReader(io.StringIO(client.get(url+'&export=csv').text.lstrip('\ufeff'))))
    assert rows and all(row['Product']==product.name for row in rows)
    assert not list(csv.DictReader(io.StringIO(client.get('/sales/products?days=60&supplier=999999&export=csv').text.lstrip('\ufeff'))))

    detailed=list(csv.DictReader(io.StringIO(client.get(url+'&export=detailed').text.lstrip('\ufeff'))))
    assert len(detailed)==2
    product_row, total = detailed
    assert product_row['Product']==product.name and product_row['Supplier']=='Selected supplier'
    assert total['Product']=='GRAND TOTAL'
    for record in detailed:
        assert record['Total units sold']==rows[0]['Units sold']
        assert record['Total net sales (PHP)']==rows[0]['Net sales']
        assert record['Average price (PHP)']==rows[0]['Average price']
        unit_columns=[key for key in record if key.endswith(' | Units')]
        sales_columns=[key for key in record if key.endswith(' | Net sales (PHP)')]
        assert len(unit_columns)==len(sales_columns)==60
        assert sum(float(record[key]) for key in unit_columns)==float(record['Total units sold'])
        assert round(sum(float(record[key]) for key in sales_columns),2)==float(record['Total net sales (PHP)'])
    other=Product(storehub_id="CSV-SECOND",name="Second product")
    session.add(other);session.flush()
    sale=session.scalar(select(SalesDayProduct).where(SalesDayProduct.product_id==product.id))
    session.add(SalesDayProduct(product_id=other.id,location_id=sale.location_id,business_date=sale.business_date,units=3,net_sales=42.50))
    session.commit()
    all_products=list(csv.DictReader(io.StringIO(client.get('/sales/products?days=60&export=detailed').text.lstrip('\ufeff'))))
    grand=all_products.pop()
    assert grand['Product']=='GRAND TOTAL' and len(all_products)>1
    assert round(sum(float(row['Total net sales (PHP)']) for row in all_products),2)==float(grand['Total net sales (PHP)'])
    assert sum(float(row['Total units sold']) for row in all_products)==float(grand['Total units sold'])
    day=unit_columns[-1].split(' | ')[0]
    custom=f"/sales/products?days=custom&frm={day}&to={day}&supplier={supplier.id}&q=zzzznothing&export=detailed"
    assert not list(csv.DictReader(io.StringIO(client.get(custom).text.lstrip('\ufeff'))))


def test_product_detail_preserves_list_filters(client,session,sync,fake):
    from urllib.parse import urlencode, urlsplit, parse_qs
    import re
    boss=ready(sync,fake,session);boss.password_hash=hash_password(PW)
    session.commit();login(client,"boss")
    product=session.scalar(select(Product).where(Product.storehub_id=="P1"))
    filters={"days":"60","q":product.name,"sort":"units","page":"1","supplier":"0","loc":"0","category":product.category or ""}
    listing=client.get("/sales/products?"+urlencode(filters))
    link=html.unescape(re.search(r'href="(/sales/product/'+str(product.id)+r'\?[^"]+)"',listing.text).group(1))
    query=parse_qs(urlsplit(link).query)
    assert parse_qs(query['list_query'][0],keep_blank_values=True)=={k:[v] for k,v in filters.items()}
    detail=client.get(link)
    links=[html.unescape(x) for x in re.findall(r'href="([^"]+)"',detail.text)]
    back=[x for x in links if x.startswith('/sales/products?')]
    assert len(back)>=3  # sidebar, breadcrumb, All products
    assert all(parse_qs(urlsplit(x).query,keep_blank_values=True)=={k:[v] for k,v in filters.items()} for x in back)
    assert 'name="list_query"' in detail.text
    assert client.get(back[0]).status_code==200
