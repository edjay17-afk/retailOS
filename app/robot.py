"""The StoreHub back-office robot.

It drives the installed Google Chrome (headless) like a person would: signs in to the back office with the login in .env
(STOREHUB_BO_EMAIL / STOREHUB_BO_PASSWORD), opens Stock Transfer, ticks "Include item details", and exports the CSV into the inbox folder.
`services/transfer_inbox.py` then imports that file. Signing in fresh on every run means there is no saved session that can expire.

StoreHub's export covers its default date range (the last 30 days), which is enough: importing the same transfers again changes nothing.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
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


# ------------------------------------------------------------------------------------------------ register shifts
def fetch_shifts(start, end, skip: set[str], max_details: int = 40, attempts: int = 2) -> tuple[list[dict], dict[str, str]]:
    """StoreHub's Shift Reports (read only). Returns (every shift between the dates as its list row, {shift id: text of the shift's own page} for up to
    `max_details` shifts whose id is not in `skip`, newest first)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return _shifts_once(start, end, skip, max_details)
        except (RobotNotConfigured, RobotLoginFailed):
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("shift report read failed (attempt %s/%s): %s: %s", i + 1, attempts, type(e).__name__, str(e).strip().splitlines()[0][:200] if str(e).strip() else "")
    raise RobotError(f"could not read StoreHub's Shift Reports: {type(last).__name__}")


def _shifts_once(start, end, skip: set[str], max_details: int) -> tuple[list[dict], dict[str, str]]:
    from playwright.sync_api import sync_playwright
    cfg = get_config()
    base = cfg.storehub_bo_url.rstrip("/")
    with sync_playwright() as pw:
        browser, ctx = open_browser(pw, headless=cfg.robot_headless)
        try:
            page = ctx.new_page()
            sign_in(page)
            page.goto(base + "/reports/sales/shifts", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=60000)
            url = f"/reports/sales/ajaxShifts?fromdate={start:%m/%d/%Y}&todate={end:%m/%d/%Y}&store=allStores"
            data = page.evaluate("async (u) => { const r = await fetch(u, {credentials: 'same-origin'}); if (!r.ok) throw new Error('HTTP ' + r.status); return await r.json(); }", url)
            rows = data.get("aaData") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise RobotError("StoreHub's Shift Reports did not return a list")
            texts: dict[str, str] = {}
            todo = [r for r in rows if r.get("DT_RowId") and r["DT_RowId"] not in skip][:max_details]
            for r in todo:
                try:
                    page.goto(f"{base}/reports/sales/shifts/id={r['DT_RowId']}", wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("text=Payments Received", timeout=30000)
                    texts[r["DT_RowId"]] = page.inner_text("body")
                except Exception as e:  # noqa: BLE001
                    log.warning("shift %s page read failed: %s: %s", r["DT_RowId"], type(e).__name__, str(e).strip().splitlines()[0][:160] if str(e).strip() else "")
            return rows, texts
        finally:
            browser.close()


# ------------------------------------------------------------------------------------------------ official Z-reading reports (GET only)
_GET_JSON_JS = """async (u) => {
  const r = await fetch(u, {method: "GET", credentials: "same-origin"});
  if (!r.ok) throw new Error("HTTP " + r.status);
  return await r.json();
}"""

_Z_DETAIL_JS = r"""() => {
  const sections = [];
  let current = null;
  for (const el of document.querySelectorAll('h1, h2, h3, h4, table')) {
    if (el.tagName === 'TABLE') {
      if (!current) continue;
      const rows = [...el.querySelectorAll('tr')].map(tr =>
        [...tr.querySelectorAll('th,td')].map(cell => cell.innerText.trim())
      ).filter(row => row.some(Boolean));
      if (rows.length) current.tables.push({rows});
      continue;
    }
    const title = el.innerText.trim();
    if (!title || /^Z-Reading Details$/i.test(title)) continue;
    current = {title, tables: []};
    sections.push(current);
  }
  return sections;
}"""


def fetch_zreadings(start, end, skip: set[str], max_details: int = 40, attempts: int = 2) -> tuple[list[dict], dict[str, dict]]:
    """Read StoreHub's official Z-reading list and detail pages. After the existing sign-in, every report request is a GET; no report controls are clicked or changed.

    Returns (all list rows, {report id: {sections, source_text}} for up to `max_details` reports not in `skip`, newest first).
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return _zreadings_once(start, end, skip, max_details)
        except (RobotNotConfigured, RobotLoginFailed):
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("Z-reading read failed (attempt %s/%s): %s: %s", i + 1, attempts, type(e).__name__, str(e).strip().splitlines()[0][:200] if str(e).strip() else "")
    raise RobotError(f"could not read StoreHub's Z-Reading Reports: {type(last).__name__}")


def _read_zreadings(page, base: str, start, end, skip: set[str], max_details: int) -> tuple[list[dict], dict[str, dict]]:
    """GET-only page reader, separated from browser setup so tests can prove no mutating page controls are used."""
    page.goto(base + "/reports/bir/zreadings", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=60000)
    url = f"/reports/bir/ajaxZreadings?fromdate={start:%m/%d/%Y}&todate={end:%m/%d/%Y}&store=allStores"
    data = page.evaluate(_GET_JSON_JS, url)
    rows = data.get("aaData") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RobotError("StoreHub's Z-Reading Reports did not return a list")
    details: dict[str, dict] = {}
    todo = [r for r in rows if r.get("DT_RowId") and r["DT_RowId"] not in skip][:max_details]
    for r in todo:
        sid = r["DT_RowId"]
        try:
            page.goto(f"{base}/reports/bir/zreading/id={sid}", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("table", timeout=30000)
            sections = page.evaluate(_Z_DETAIL_JS)
            if not sections:
                raise RobotError("StoreHub's Z-reading detail did not contain its official sections")
            details[sid] = {"sections": sections, "source_text": page.inner_text("body")}
        except Exception as e:  # noqa: BLE001
            log.warning("Z-reading %s detail read failed: %s: %s", sid, type(e).__name__, str(e).strip().splitlines()[0][:160] if str(e).strip() else "")
    return rows, details


def _zreadings_once(start, end, skip: set[str], max_details: int) -> tuple[list[dict], dict[str, dict]]:
    from playwright.sync_api import sync_playwright
    cfg = get_config()
    base = cfg.storehub_bo_url.rstrip("/")
    with sync_playwright() as pw:
        browser, ctx = open_browser(pw, headless=cfg.robot_headless)
        try:
            page = ctx.new_page()
            sign_in(page)
            return _read_zreadings(page, base, start, end, skip, max_details)
        finally:
            browser.close()
