"""New-product registration (process: register -> Lark Base "New Product" table -> Excel for the ShadowBot RPA -> RPA keys it into StoreHub).

The local row is the record of what was registered and whether the Lark mirror worked; the Excel file is the RPA input.
StoreHub itself is never written from here — the robot does that part.
"""
from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit, settings_service
from ..config import get_config
from ..lark import LarkClient, LarkError
from ..models import NewProduct, Product, User
from ..timeutil import fmt_local, now_utc

# the Lark Base "New Product" table (link: .../base/RY44bAx8Kat41lsIt4OjLbx1pTh?table=tblEtY60ilU7XKQ1)
LARK_BASE_TOKEN = "RY44bAx8Kat41lsIt4OjLbx1pTh"
LARK_TABLE = "tblEtY60ilU7XKQ1"
# our column -> matched against the Lark table's writable columns (people rename columns; field_map fuzzy-matches)
LARK_COLUMNS = ("Barcode", "Product Name", "Category", "Supplier", "Supplier Cost", "Selling Price", "Quantity", "Expiration Date", "Status", "Created By",
                "Entry Type", "Brand", "Flavor", "Unit", "Units Per Pack", "Pack Price")
# added to the Lark table when missing ("Product Name" is not here on purpose: the table's own "Product Name" is a
# computed column, and field_map already routes our value to the writable "Product" column instead)
LARK_ADD_IF_MISSING = {"Category": 1, "Supplier": 1, "Supplier Cost": 2, "Selling Price": 2, "Status": 1, "Created By": 1,
                       "Entry Type": 1, "Brand": 1, "Flavor": 1, "Unit": 1, "Units Per Pack": 2, "Pack Price": 2}

# the first 11 columns keep their positions (the RPA reads them); the entry-process columns are appended after them
EXCEL_HEADERS = ["Product Name", "SKU", "Barcode", "Category", "Supplier", "Cost", "Price", "Initial Quantity", "Status", "Created At", "Created By",
                 "Entry Type", "Brand", "Flavor", "Unit", "Units Per Pack", "Pack Price", "Packs"]
UNITS = ("piece", "pack", "case")
MAX_QTY = 5000  # more base units than this in one entry is almost always a unit mistake (cases keyed as pieces)
RPA_DIR = Path("rpa") / "new-products"


class NewProductError(Exception):
    pass


def rpa_dir() -> Path:
    d = get_config().data_dir / RPA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def lark_client() -> LarkClient:
    """Same app credentials as the mirror, pointed at the New Product base."""
    return LarkClient(get_config().model_copy(update={"lark_base_token": LARK_BASE_TOKEN}))


def send_to_lark(np_: NewProduct, client: LarkClient) -> str:
    """Write one row to the Lark New Product table; returns the Lark record id.
    Columns the table does not have yet (Category, Supplier, costs, Status…) are created once, so the table
    carries the full registration; computed columns (its formula "Product Name") are never written."""
    norm = lambda n: re.sub(r"\s+", " ", n.strip().lower())
    have = {norm(n) for n in client.writable_fields(LARK_TABLE)}
    for name, ftype in LARK_ADD_IF_MISSING.items():
        if norm(name) not in have:
            client.add_field(LARK_TABLE, name, ftype)
            have.add(norm(name))
    values = {"Barcode": np_.barcode, "Product Name": np_.name, "Category": np_.category, "Supplier": np_.supplier_name,
              "Supplier Cost": np_.cost, "Selling Price": np_.price, "Quantity": np_.qty,
              "Expiration Date": np_.expiry_date.isoformat() if np_.expiry_date else None, "Status": "Pending RPA",
              "Created By": np_.creator.full_name or np_.creator.username if np_.creator else "",
              "Entry Type": "Existing" if np_.entry_type == "existing" else "New", "Brand": np_.brand, "Flavor": np_.flavor, "Unit": np_.unit,
              "Units Per Pack": np_.units_per_pack, "Pack Price": np_.pack_price}
    fmap = client.field_map(LARK_TABLE, LARK_COLUMNS)
    fields = {fmap[k]: v for k, v in values.items() if k in fmap and v is not None}
    if not fields:
        raise LarkError("No matching writable columns in the Lark New Product table")
    return client.batch_create(LARK_TABLE, [fields])[0]


def write_excel(np_: NewProduct) -> str:
    """One Excel file per registration, in the folder the ShadowBot RPA watches. Returns the path relative to data_dir."""
    from openpyxl import Workbook
    rel = RPA_DIR / f"NP-{np_.id:05d}_{now_utc().strftime('%Y%m%d-%H%M%S')}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "New Products"
    ws.append(EXCEL_HEADERS)
    ws.append([np_.name, "", np_.barcode, np_.category, np_.supplier_name, np_.cost, np_.price, np_.qty, "Pending RPA",
               fmt_local(np_.created_at), (np_.creator.full_name or np_.creator.username) if np_.creator else "",
               "Existing" if np_.entry_type == "existing" else "New", np_.brand, np_.flavor, np_.unit, np_.units_per_pack, np_.pack_price, np_.packs])
    for col, width in zip("ABCDEFGHIJKLMNOPQR", (34, 12, 16, 16, 24, 10, 10, 14, 12, 18, 16, 12, 16, 16, 8, 14, 12, 10)):
        ws.column_dimensions[col].width = width
    wb.save(rpa_dir() / rel.name)
    return rel.as_posix()


def barcode_variants(barcode: str) -> set[str]:
    """A scanner reads EAN-13 (0012345678905) where the product list may hold the UPC-A (012345678905) or the bare digits:
    the same code with leading zeros added or removed is the same product."""
    b = (barcode or "").strip()
    if not b.isdigit():
        return {b} if b else set()
    core = b.lstrip("0") or "0"
    return {b, core} | {core.zfill(n) for n in (8, 12, 13, 14)}


def lookup(db: Session, barcode: str) -> Product | None:
    """Step 1 of the flow: is the scanned barcode already a StoreHub product (in the saved product list)?"""
    v = barcode_variants(barcode)
    if not v:
        return None
    p = db.scalar(select(Product).where(Product.barcode.in_(v)).order_by(Product.in_api.desc(), Product.id))
    if p is not None:
        return p
    # StoreHub lets one product carry several barcodes, stored comma-separated ("100070161773,6977318633319")
    for cand in db.scalars(select(Product).where(Product.barcode.like("%,%"), or_(*[Product.barcode.like(f"%{x}%") for x in v])).order_by(Product.in_api.desc(), Product.id)):
        if v & {t.strip() for t in (cand.barcode or "").split(",")}:
            return cand
    return None


_last_refresh = 0.0
REFRESH_EVERY = 120  # seconds: scans in a row never hammer StoreHub


def refresh_products() -> bool:
    """Fetch the whole product list from StoreHub now (the saved copy is otherwise refreshed on a schedule).
    True if StoreHub answered. At most once every two minutes."""
    global _last_refresh
    now = time.monotonic()
    if _last_refresh and now - _last_refresh < REFRESH_EVERY:
        return False
    _last_refresh = now
    try:
        from ..storehub.sync import SyncService
        out = SyncService().sync_products()
        return bool(out) and "error" not in out
    except Exception:  # StoreHub down or not configured: the saved list is all we have
        return False


def products_fresh(db: Session) -> bool:
    """True when the product list was fetched from StoreHub in the last couple of minutes: asking again would only repeat the answer."""
    from ..models import SyncState
    st = db.get(SyncState, "products")
    return bool(st and st.last_success_at and (now_utc() - st.last_success_at).total_seconds() < REFRESH_EVERY)


def find_product(db: Session, barcode: str, refresh: bool = True) -> tuple[Product | None, bool]:
    """Saved StoreHub list first; if the barcode is not there, ask StoreHub for the current list (a product may have been created there
    today) and look again. Returns (product, refreshed_from_storehub)."""
    p = lookup(db, barcode)
    if p is not None or not refresh or not (barcode or "").strip() or products_fresh(db):
        return p, False
    if refresh_products():
        db.rollback()  # end the read snapshot so the freshly saved list is visible
        return lookup(db, barcode), True
    return None, False


_WEB_CACHE: dict[str, dict | None] = {}


def web_lookup(db: Session, barcode: str, timeout: float = 4.0) -> dict | None:
    """Ask Open Food Facts (free, public) what the scanned barcode is, so staff do not type the name. Only the barcode leaves the system.
    Returns {name, brand, category, quantity, source} or None (not found, offline, switched off). Staff always confirm what it fills in."""
    barcode = (barcode or "").strip()
    if not barcode.isdigit() or not (8 <= len(barcode) <= 14) or not settings_service.get(db, "product.web_lookup", True):
        return None
    if barcode in _WEB_CACHE:
        return _WEB_CACHE[barcode]
    found = None
    try:
        r = httpx.get(f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json", timeout=timeout, headers={"User-Agent": "RetailOS/1.0"},
                      params={"fields": "product_name,brands,categories,quantity"})
        p = r.json().get("product") if r.status_code == 200 else None
        if p:
            name = (p.get("product_name") or "").strip()
            brand = (p.get("brands") or "").split(",")[0].strip()
            if name or brand:
                found = {"name": name, "brand": brand, "category": (p.get("categories") or "").split(",")[0].strip(),
                         "quantity": (p.get("quantity") or "").strip(), "source": "Open Food Facts"}
    except Exception:  # offline, slow or a changed answer: the form simply stays empty
        return None  # not cached, so a later scan tries again
    _WEB_CACHE[barcode] = found
    return found


def unit_cost(pack_price: float | None, units_per_pack: float | None) -> float | None:
    """Cost = supplier price / units per pack."""
    if pack_price is None or not units_per_pack:
        return None
    return round(pack_price / units_per_pack, 4)


def selling_price(cost: float | None, margin_pct: float | None) -> float | None:
    """Price = cost x (1 + margin)."""
    if cost is None or margin_pct is None:
        return None
    return round(cost * (1 + margin_pct / 100), 2)


def _check_numbers(cost, price, qty, packs, units_per_pack, pack_price) -> None:
    for label, v in (("Supplier price", pack_price), ("Supplier cost", cost), ("Selling price", price), ("Quantity", qty), ("Packs", packs),
                     ("Units per pack", units_per_pack)):
        if v is not None and v < 0:
            raise NewProductError(f"{label} cannot be negative")
    if price is not None and cost is not None and price < cost:
        raise NewProductError(f"The selling price ({price:g}) is below the cost ({cost:g}) — raise the price")
    if qty is not None and qty > MAX_QTY:
        raise NewProductError(f"{qty:g} units looks too many — check the unit and the units per pack")
    if qty is not None and abs(qty - round(qty)) > 1e-9:
        raise NewProductError("The quantity must be a whole number of base units (pieces)")


def _unit_and_qty(unit, units_per_pack, packs, qty):
    """Unit set + pack size, and the quantity in base units: packs x units per pack (2 cases x 24 = 48)."""
    unit = (unit or "").strip().lower() or "piece"
    if unit not in UNITS:
        raise NewProductError("Choose the unit: piece, pack or case")
    upp = 1.0 if unit == "piece" else units_per_pack
    if upp is None or upp <= 0:
        raise NewProductError("Enter how many pieces are in one " + unit)
    if packs is not None:
        qty = packs * upp
    elif qty is not None and unit == "piece":
        packs = qty
    return unit, upp, packs, qty


def _finish(db: Session, user: User, np_: NewProduct, client: LarkClient | None, action: str) -> NewProduct:
    """Save -> Excel for the RPA -> Lark mirror. A Lark failure never loses the entry."""
    db.add(np_)
    db.flush()
    np_.excel_path = write_excel(np_)
    np_.creator = user
    client = client if client is not None else lark_client()
    if not client.configured:
        np_.status, np_.lark_error = "recorded", "Lark is not configured"
    else:
        try:
            np_.lark_record_id = send_to_lark(np_, client)
            np_.status, np_.lark_error = "lark_sent", None
        except Exception as e:  # Lark down / table changed: the entry and the Excel still stand
            np_.status, np_.lark_error = "lark_failed", str(e)[:300]
    audit.log(db, actor_type="human", actor_id=user.id, action=action, entity_type="new_product", entity_id=np_.id,
              after={"entry_type": np_.entry_type, "barcode": np_.barcode, "name": np_.name, "brand": np_.brand, "flavor": np_.flavor,
                     "supplier": np_.supplier_name, "unit": np_.unit, "units_per_pack": np_.units_per_pack, "pack_price": np_.pack_price,
                     "cost": np_.cost, "price": np_.price, "qty": np_.qty, "lark": np_.status})
    return np_


def _parse_expiry(expiry: str | None) -> date | None:
    if not (expiry or "").strip():
        return None
    try:
        return date.fromisoformat(expiry.strip())
    except ValueError:
        raise NewProductError("Enter the expiry date as year-month-day")


def create(db: Session, user: User, *, barcode: str, name: str, brand: str = "", flavor: str = "", category: str = "", supplier_name: str = "",
           unit: str = "piece", units_per_pack: float | None = None, pack_price: float | None = None, packs: float | None = None,
           cost: float | None = None, price: float | None = None, qty: float | None = None, expiry: str | None = None,
           note: str | None = None, client: LarkClient | None = None) -> NewProduct:
    """New product (barcode not known yet). Flow: register name/brand/flavor/barcode -> unit and pack size -> cost and price
    -> quantity in base units -> automatic checks -> save (Excel for the RPA + Lark mirror)."""
    barcode, name = (barcode or "").strip(), (name or "").strip()
    brand, flavor = (brand or "").strip(), (flavor or "").strip()
    if not barcode:
        raise NewProductError("Scan or enter the barcode")
    if not name:
        raise NewProductError("Enter the product name")
    if not brand:
        raise NewProductError("Enter the brand")
    if not flavor:
        raise NewProductError("Enter the flavor (type None if the product has no flavor) — every flavor is its own record")
    if db.scalar(select(Product.id).where(Product.barcode.in_(barcode_variants(barcode)))):
        raise NewProductError("This barcode already exists in StoreHub products — scan it again to enter it as an existing product")
    if db.scalar(select(NewProduct.id).where(NewProduct.barcode == barcode, NewProduct.entry_type == "new")):
        raise NewProductError("This barcode was already registered — find it in the list below")
    twin = db.scalar(select(NewProduct).where(NewProduct.entry_type == "new", func.lower(NewProduct.name) == name.lower(),
                                              func.lower(func.coalesce(NewProduct.flavor, "")) == flavor.lower()))
    if twin is not None:
        raise NewProductError(f"{name} / {flavor} was already registered with barcode {twin.barcode} — one barcode per flavor")
    unit, upp, packs, qty = _unit_and_qty(unit, units_per_pack, packs, qty)
    if pack_price is not None:  # cost follows from the supplier price; a directly typed cost is only for callers that have no pack price
        cost = unit_cost(pack_price, upp)
    if cost is None:
        raise NewProductError("Enter the supplier price")
    if price is None:
        raise NewProductError("Enter the selling price (or a margin to calculate it)")
    if qty is None:
        raise NewProductError("Enter the quantity (0 if the goods have not arrived yet)")
    _check_numbers(cost, price, qty, packs, upp, pack_price)
    np_ = NewProduct(entry_type="new", barcode=barcode, name=name[:300], brand=brand[:120], flavor=flavor[:120], category=(category or "").strip()[:120],
                     supplier_name=(supplier_name or "").strip()[:160], unit=unit, units_per_pack=upp, pack_price=pack_price, packs=packs,
                     cost=cost, price=price, qty=qty, expiry_date=_parse_expiry(expiry), note=(note or "").strip()[:300] or None, created_by=user.id)
    return _finish(db, user, np_, client, "new_product.create")


def create_existing(db: Session, user: User, product: Product, *, flavor: str = "", supplier_name: str = "", unit: str = "piece",
                    units_per_pack: float | None = None, pack_price: float | None = None, packs: float | None = None,
                    price: float | None = None, expiry: str | None = None, note: str | None = None, confirmed: bool = False,
                    client: LarkClient | None = None) -> NewProduct:
    """Existing product (barcode already in the system). Flow: record loaded -> staff confirm flavor and unit match the goods ->
    supplier cost checked (a change recomputes cost and price) -> quantity in base units -> checks -> save."""
    if not confirmed:
        raise NewProductError("Confirm that the flavor and unit match the goods in your hands")
    if packs is None:
        raise NewProductError("Enter the quantity")
    unit, upp, packs, qty = _unit_and_qty(unit, units_per_pack, packs, None)
    if packs <= 0:
        raise NewProductError("Enter a quantity above zero")
    cost = unit_cost(pack_price, upp) if pack_price is not None else product.cost
    changed = pack_price is not None and product.cost is not None and abs((cost or 0) - product.cost) > 0.005
    if changed and price is None:
        raise NewProductError(f"The supplier cost changed ({product.cost:g} to {cost:g}) — enter the new selling price")
    final_price = price if price is not None else product.unit_price
    _check_numbers(cost, final_price, qty, packs, upp, pack_price)
    np_ = NewProduct(entry_type="existing", product_id=product.id, barcode=product.barcode or "", name=product.name[:300], flavor=(flavor or "").strip()[:120] or None,
                     category=(product.category or "")[:120], supplier_name=(supplier_name or (product.supplier.name if product.supplier else "")).strip()[:160],
                     unit=unit, units_per_pack=upp, pack_price=pack_price, packs=packs, cost=cost, price=final_price, qty=qty,
                     expiry_date=_parse_expiry(expiry), note=(note or "").strip()[:300] or None, created_by=user.id)
    return _finish(db, user, np_, client, "new_product.existing")


def retry_lark(db: Session, user: User, np_: NewProduct, client: LarkClient | None = None) -> NewProduct:
    if np_.status == "lark_sent":
        raise NewProductError("Already mirrored to Lark")
    client = client if client is not None else lark_client()
    if not client.configured:
        raise NewProductError("Lark is not configured")
    np_.lark_record_id = send_to_lark(np_, client)
    np_.status, np_.lark_error = "lark_sent", None
    audit.log(db, actor_type="human", actor_id=user.id, action="new_product.retry_lark", entity_type="new_product", entity_id=np_.id,
              after={"lark": np_.status})
    return np_
