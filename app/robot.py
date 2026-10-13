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
