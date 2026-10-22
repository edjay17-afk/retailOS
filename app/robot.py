"""The StoreHub back-office robot.

It drives the installed Google Chrome (headless) like a person would: signs in to the back office with the login in .env
(STOREHUB_BO_EMAIL / STOREHUB_BO_PASSWORD), opens Stock Transfer, ticks "Include item details", and exports the CSV into the inbox folder.
`services/transfer_inbox.py` then imports that file. Signing in fresh on every run means there is no saved session that can expire.

StoreHub's export covers its default date range (the last 30 days), which is enough: importing the same transfers again changes nothing.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from .config import get_config

log = logging.getLogger("retailos.robot")


class RobotNotConfigured(Exception):
    """STOREHUB_BO_EMAIL / STOREHUB_BO_PASSWORD are not set."""


class RobotLoginFailed(Exception):
    """StoreHub did not accept the sign-in (wrong login, a verification code, or a changed sign-in page)."""


class RobotError(Exception):
    """The page did not look the way the robot expects (StoreHub changed it) or the export did not arrive."""


def configured() -> bool:
    cfg = get_config()
    return bool(cfg.robot_enabled and cfg.storehub_bo_email and cfg.storehub_bo_password)


def open_browser(pw, headless: bool = True):
    """The installed Google Chrome when there is one (Windows); otherwise the Chromium that Playwright downloads (Linux servers). Returns (browser, context)."""
    try:
        browser = pw.chromium.launch(channel="chrome", headless=headless)
    except Exception:  # noqa: BLE001  (no Google Chrome on this machine)
        browser = pw.chromium.launch(headless=headless, args=["--no-sandbox"])
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900})
    return browser, ctx


def sign_in(page) -> None:
    cfg = get_config()
    if not (cfg.storehub_bo_email and cfg.storehub_bo_password):
        raise RobotNotConfigured("Set STOREHUB_BO_EMAIL and STOREHUB_BO_PASSWORD in .env")
    page.goto(cfg.storehub_bo_url.rstrip("/") + "/login", wait_until="commit", timeout=60000)
    page.wait_for_selector("input[type=password]", timeout=60000)
    page.locator("input[type=email], input[name=email], input[placeholder*=mail i]").first.fill(cfg.storehub_bo_email)
    page.locator("input[type=password]").first.fill(cfg.storehub_bo_password)
    page.get_by_role("button", name=re.compile("continue with email", re.I)).click()
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=45000)
    except Exception as e:  # noqa: BLE001
        text = (page.inner_text("body") or "")[:600].replace("\n", " ")
        if re.search(r"verif|code|otp|two-step|2-step", text, re.I):
            raise RobotLoginFailed("StoreHub asked for a verification code, which a robot cannot enter") from e
        raise RobotLoginFailed("StoreHub did not accept the sign-in. Check the email and password in .env") from e


def export_stock_transfers(dest_dir: Path | None = None, attempts: int = 3) -> Path:
    """Sign in, export the Stock Transfer list with item details, and return the saved CSV. A slow StoreHub page is retried; a refused sign-in is not."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _export_once(dest_dir)
        except (RobotNotConfigured, RobotLoginFailed):
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("stock transfer export attempt %d/%d failed: %s", attempt, attempts, e)
    raise RobotError(f"The Stock Transfer export failed {attempts} times: {last}") from last


def _export_once(dest_dir: Path | None = None) -> Path:
    from playwright.sync_api import sync_playwright
    cfg = get_config()
    dest_dir = dest_dir or cfg.transfer_inbox
    dest_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser, ctx = open_browser(pw, headless=cfg.robot_headless)
        try:
            page = ctx.new_page()
            sign_in(page)
            page.goto(cfg.storehub_bo_url.rstrip("/") + "/stocks/stockTransfers", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=60000)
            try:
                page.get_by_text("Export to CSV").first.click(timeout=20000)
                box = page.locator("#includeItems")  # a styled checkbox: the real input is hidden, so the visible label is clicked
                if not box.is_checked():
                    page.get_by_text("Include item details").first.click(timeout=15000)
                if not box.is_checked():
                    raise RobotError("Could not tick 'Include item details'")
                with page.expect_download(timeout=120000) as download:
                    page.locator(".modal a.btn, [role=dialog] a.btn", has_text=re.compile(r"^\s*Export\s*$")).first.click(timeout=15000)
            except Exception as e:  # noqa: BLE001
                raise RobotError(f"The Stock Transfer export did not work: {type(e).__name__}. StoreHub may have changed the page.") from e
            target = dest_dir / f"StockTransfers-{datetime.now():%Y%m%d-%H%M%S}.csv"
            download.value.save_as(str(target))
            return target
        finally:
            browser.close()


# ------------------------------------------------------------------------------------------------ daily sales report (Gross Profit)
def fetch_daily_sales(store_names: list[str], start, end, attempts: int = 2) -> dict[str, list[dict]]:
    """Read-only. StoreHub's own "Sales over time" report, grouped by day, for each store: the rows StoreHub itself shows (Total Sales, Tax, Gross Profit...). Its API does
    not send item costs, so the Gross Profit of past days can only come from this report. Signs in once, then asks the report page for each store in turn.
    Returns {store name: [row dicts as StoreHub sends them]}; a store whose page did not answer is left out."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _daily_sales_once(store_names, start, end)
        except (RobotNotConfigured, RobotLoginFailed):
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("daily sales report attempt %d/%d failed: %s", attempt, attempts, e)
    raise RobotError(f"The daily sales report did not load {attempts} times: {last}") from last


def _daily_sales_once(store_names: list[str], start, end) -> dict[str, list[dict]]:
    from playwright.sync_api import sync_playwright
    cfg = get_config()
    out: dict[str, list[dict]] = {}
    url = cfg.storehub_bo_url.rstrip("/") + "/reports/v2/sales/over-time"
    is_table = lambda r: "api/v2/report/sales" in r.url and "fetchTableData" in r.url  # noqa: E731
    with sync_playwright() as pw:
        browser, ctx = open_browser(pw, headless=cfg.robot_headless)
        try:
            ctx.close()
            for name in store_names:  # a fresh sign-in per store: the report page keeps the store filter of the last visit
                fresh = browser.new_context(viewport={"width": 1366, "height": 900})
                try:
                    page = fresh.new_page()
                    sign_in(page)
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_load_state("networkidle", timeout=60000)
                    page.get_by_role("button", name=re.compile("Filters")).first.click(timeout=20000)
                    page.locator(".ant-select-selection-overflow").first.click(force=True, timeout=15000)
                    page.get_by_text(name, exact=True).first.click(timeout=15000)
                    with page.expect_response(is_table, timeout=60000):
                        page.get_by_role("button", name=re.compile(r"^Apply$")).first.click(timeout=15000)
                    page.wait_for_timeout(800)
                    page.get_by_text(re.compile(r"\d+ \w{3} \d{4}\s*[–-]\s*\d+ \w{3} \d{4}")).first.click(timeout=15000)
                    boxes = page.locator("input[value*='/20']")
                    boxes.nth(0).fill(f"{start:%m/%d/%Y}")
                    boxes.nth(1).fill(f"{end:%m/%d/%Y}")
                    with page.expect_response(is_table, timeout=60000) as resp:
                        page.get_by_role("button", name=re.compile(r"^Apply$")).last.click(timeout=15000)
                    rows = resp.value.json().get("data")
                    if isinstance(rows, list):
                        out[name] = rows
                except (RobotNotConfigured, RobotLoginFailed):
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("daily sales report for %s failed: %s: %s", name, type(e).__name__, str(e).strip().splitlines()[0][:200] if str(e).strip() else "")
                finally:
                    fresh.close()
            return out
        finally:
            browser.close()


# ------------------------------------------------------------------------------------------------ exact supplier prices of purchase orders
_PICK_RANGE_JS = r"""([from, to]) => {
  const jq = window.jQuery;
  const el = [...document.querySelectorAll('*')].find(e => jq(e).data('daterangepicker'));
  const dp = jq(el).data('daterangepicker');
  dp.setStartDate(moment(from, 'YYYY-MM-DD'));
  dp.setEndDate(moment(to, 'YYYY-MM-DD'));
  dp.clickApply();
}"""

_PO_ROWS_JS = r"""() => {
  const tables = [...document.querySelectorAll('table')];
  const t = tables.find(x => /Supplier Price/i.test(x.innerText));
  if (!t) return null;
  const heads = [...t.querySelectorAll('thead th, tr th')].map(h => h.innerText.trim());
  const pi = heads.findIndex(h => /Supplier Price/i.test(h));
  const ni = heads.findIndex(h => /Product Name/i.test(h));
  const rows = [];
  for (const tr of t.querySelectorAll('tbody tr')) {
    const cells = [...tr.querySelectorAll('td')];
    if (cells.length < 4 || !/^\d+$/.test(cells[0].innerText.trim())) continue;
    const inp = tr.querySelector('input[name^="price"]');
    const price = inp ? inp.value : (pi >= 0 && cells[pi] ? cells[pi].innerText : '');
    rows.push([cells[ni >= 0 ? ni : 1].innerText.trim(), String(price).replace(/,/g, '').trim()]);
  }
  return rows;
}"""


def fetch_po_prices(wanted: set[int], earliest, today, attempts: int = 2) -> dict[int, list[tuple[str, str]]]:
    """Read only. The StoreHub API sends a purchase order's supplier price rounded to 2 decimals, but StoreHub keeps (and prints) up to 6. This reads each PO's own page in the back
    office, exactly as a person would open it, and returns {PO number: [(product name, supplier price text as StoreHub shows it), ...]} in the order of the lines.
    It never presses Save or anything that changes a purchase order."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _po_prices_once(wanted, earliest, today)
        except (RobotNotConfigured, RobotLoginFailed):
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("po price read attempt %d/%d failed: %s", attempt, attempts, e)
    raise RobotError(f"The purchase order prices could not be read {attempts} times: {last}") from last


def _po_prices_once(wanted: set[int], earliest, today) -> dict[int, list[tuple[str, str]]]:
    from datetime import timedelta
    from playwright.sync_api import sync_playwright
    cfg = get_config()
    base = cfg.storehub_bo_url.rstrip("/")
    found: dict[int, str] = {}
    out: dict[int, list[tuple[str, str]]] = {}
    with sync_playwright() as pw:
        browser, ctx = open_browser(pw, headless=cfg.robot_headless)
        try:
            page = ctx.new_page()
            sign_in(page)
            # 1) the list, one 30-day window at a time (its default window is the last 30 days), to learn each PO's page address
            end = today
            while end >= earliest and not wanted <= set(found):
                start = max(earliest, end - timedelta(days=29))
                try:
                    page.goto(base + "/stocks/purchaseOrders", wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_load_state("networkidle", timeout=60000)
                    page.evaluate(_PICK_RANGE_JS, [start.isoformat(), end.isoformat()])  # the page's own date-range picker, set through its API
                    page.wait_for_timeout(500)
                    page.get_by_text("Apply Filter").first.click(timeout=10000)
                    page.wait_for_load_state("networkidle", timeout=60000)
                    idx = page.evaluate("() => [...document.querySelectorAll('select')].findIndex(e => [...e.options].some(o => o.value === '100'))")
                    if idx >= 0:
                        page.locator("select").nth(idx).select_option(value="100")
                        page.wait_for_timeout(2000)
                    links = page.eval_on_selector_all("a", r"els => els.map(e => [e.innerText.trim(), e.getAttribute('href')]).filter(x => /^PO\d+$/.test(x[0]) && x[1])")
                    for label, href in links:
                        found[int(label[2:])] = href
                except Exception as e:  # noqa: BLE001
                    log.warning("po list %s..%s failed: %s: %s", start, end, type(e).__name__, str(e).strip().splitlines()[0][:160] if str(e).strip() else "")
                end = start - timedelta(days=1)
            # 2) each wanted PO's own page
            for no in sorted(wanted & set(found)):
                try:
                    page.goto(base + found[no], wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("table tbody tr", timeout=30000)
                    page.wait_for_timeout(600)
                    rows = page.evaluate(_PO_ROWS_JS)
                    if rows:
                        out[no] = [(n, p) for n, p in rows]
                except Exception as e:  # noqa: BLE001
                    log.warning("po %s price read failed: %s: %s", no, type(e).__name__, str(e).strip().splitlines()[0][:160] if str(e).strip() else "")
            return out
        finally:
            browser.close()
