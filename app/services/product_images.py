"""The product image robot: finds a picture for products that have none and keeps a copy in RetailOS.

It looks the product's barcode up in the free public product databases (Open Food Facts and its sister databases for beauty,
general products and pet food), downloads the front picture, shrinks it and stores it under data/uploads/products/.
`Product.image_url` then points at that local copy. Nothing is ever written to StoreHub: this only touches our own database and folder.

Only real GTIN barcodes (EAN-8, UPC-A, EAN-13, GTIN-14 with a correct check digit) are looked up; shop-internal codes are skipped
without any network call. A product that was looked up is not looked up again for RETRY_DAYS.
"""
from __future__ import annotations

import io
import logging
import time
from datetime import timedelta

import httpx
from PIL import Image
from sqlalchemy import or_, select

from ..config import get_config
from ..models import Product
from ..timeutil import now_utc

log = logging.getLogger("retailos.product_images")

HOSTS = ("world.openfoodfacts.org", "world.openbeautyfacts.org", "world.openproductsfacts.org", "world.openpetfoodfacts.org")
USER_AGENT = "RetailOS-ProductImages/1.0"   # generic on purpose: the public databases ask bots to identify themselves, nothing personal
RETRY_DAYS = 30
MAX_BYTES = 6 * 1024 * 1024
SIZE = 600   # longest side of the stored picture, in pixels
PAUSE = 1.0  # seconds between two requests


class LookupUnavailable(Exception):
    """The network or a database did not answer: try again on the next run (the product is not marked as looked up)."""


def valid_gtin(code: str) -> bool:
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1])))
    return (10 - total % 10) % 10 == digits[-1]


def gtins(barcode: str | None) -> list[str]:
    """The real barcodes in a product's barcode field (several can be stored, separated by commas)."""
    out = []
    for part in (barcode or "").split(","):
        code = part.strip()
        if valid_gtin(code) and code not in out:
            out.append(code)
    return out


def _find(client: httpx.Client, code: str) -> tuple[str, str] | None:
    """(picture url, host) of the first database that knows this barcode and has a front picture for it."""
    for host in HOSTS:
        try:
            r = client.get(f"https://{host}/api/v2/product/{code}.json", params={"fields": "image_front_url,image_url"})
        except httpx.HTTPError as e:
            raise LookupUnavailable(f"{host}: {type(e).__name__}") from e
        if r.status_code == 404:
            continue
        if r.status_code != 200:
            raise LookupUnavailable(f"{host}: HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            continue
        prod = data.get("product") or {}
        url = prod.get("image_front_url") or prod.get("image_url")
        if data.get("status") == 1 and isinstance(url, str) and url.startswith("https://"):
            return url, host
    return None


def _fetch_picture(client: httpx.Client, url: str) -> bytes | None:
    """The picture as a shrunken JPEG, or None when the download is not a usable image."""
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                return None
            raw = bytearray()
            for chunk in r.iter_bytes():
                raw += chunk
                if len(raw) > MAX_BYTES:
                    return None
    except httpx.HTTPError as e:
        raise LookupUnavailable(f"download: {type(e).__name__}") from e
    return shrink(bytes(raw))


def shrink(raw: bytes, min_side: int = 100, max_side: int = SIZE) -> bytes | None:
    """Any picture (JPEG, PNG, WebP, GIF...) as a JPEG of at most `max_side` pixels, or None when it is not a usable image."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:  # noqa: BLE001  (not an image, or a damaged one)
        return None
    if min(img.size) < min_side:
        return None
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.getchannel("A"))
        img = flat
    else:
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def save_picture(p: Product, jpeg: bytes, source: str) -> None:
    """Keep this picture as the product's image (the caller commits)."""
    folder = get_config().upload_dir / "products"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{p.id}.jpg").write_bytes(jpeg)
    p.image_url = f"/uploads/products/{p.id}.jpg?v={int(now_utc().timestamp())}"   # the ?v= makes browsers load a replaced picture
    p.image_source = source
    p.image_checked_at = now_utc()


def remove_picture(p: Product) -> None:
    (get_config().upload_dir / "products" / f"{p.id}.jpg").unlink(missing_ok=True)
    p.image_url, p.image_source, p.image_checked_at = None, None, now_utc()   # the robot waits RETRY_DAYS before looking again


def _candidates(db, limit: int):
    cutoff = now_utc() - timedelta(days=RETRY_DAYS)
    stmt = (select(Product)
            .where(Product.in_api.is_(True), Product.source == "storehub",
                   or_(Product.image_url.is_(None), Product.image_url == ""),
                   or_(Product.image_checked_at.is_(None), Product.image_checked_at < cutoff))
            .order_by(Product.image_checked_at.is_not(None), Product.image_checked_at, Product.id))
    return db.scalars(stmt.limit(limit))


def run(db, client: httpx.Client | None = None, limit: int | None = None, pause: float = PAUSE) -> dict:
    """One pass over the products still without a picture. Returns what happened, for the log."""
    cfg = get_config()
    limit = limit or cfg.product_images_per_run
    folder = cfg.upload_dir / "products"
    folder.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    out = {"checked": 0, "found": 0, "skipped_no_gtin": 0, "unavailable": ""}
    try:
        for p in list(_candidates(db, limit)):
            codes = gtins(p.barcode)
            if not codes:
                p.image_checked_at = now_utc()   # shop-internal code: nothing to look up, and nothing to retry soon
                out["skipped_no_gtin"] += 1
                continue
            try:
                found = None
                for code in codes:
                    hit = _find(client, code)
                    time.sleep(pause)
                    jpeg = _fetch_picture(client, hit[0]) if hit else None
                    if jpeg:
                        found = (jpeg, hit[1])
                        break
            except LookupUnavailable as e:
                out["unavailable"] = str(e)
                break   # the network is down or a database is busy: stop now, the next run continues from this product
            p.image_checked_at = now_utc()
            out["checked"] += 1
            if found:
                save_picture(p, found[0], found[1])
                out["found"] += 1
            db.commit()
        db.commit()
    finally:
        if own:
            client.close()
    return out
