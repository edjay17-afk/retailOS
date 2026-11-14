"""Render mockup.html to desktop + mobile PNGs."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path(__file__).parent
url = (out / "mockup.html").as_uri()
with sync_playwright() as pw:
    try:
        b = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        b = pw.chromium.launch(headless=True)
    d = b.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
    d.goto(url)
    d.wait_for_timeout(500)
    d.screenshot(path=str(out / "02-redesign-desktop.png"), full_page=True)
    m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    m.goto(url)
    m.wait_for_timeout(500)
    m.screenshot(path=str(out / "03-redesign-mobile.png"), full_page=True)
    b.close()
print("done")
