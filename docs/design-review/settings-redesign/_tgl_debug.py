"""Debug: boot app, open /settings, dump computed geometry of the first toggle and screenshot it zoomed."""
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import uvicorn
from playwright.sync_api import sync_playwright

from app import db as dbm
from app.config import Config, set_config
from app.dbinit import init_db
from app.models import User
from app.security import hash_password

tmp = Path(tempfile.mkdtemp())
set_config(Config(loyverse_reporting_enabled=False, loyverse_tokens_file="", data_dir=tmp,
                  storehub_username="t", storehub_token="t", scheduler_enabled=False, secret_key="k" * 32))
dbm.init_engine()
init_db()
s = dbm.SessionLocal()
s.add(User(username="admin", full_name="Admin", role="admin", password_hash=hash_password("Passw0rd-long-1")))
s.commit()

from app.web.main import create_app
app = create_app()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8124, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(50):
    if server.started:
        break
    time.sleep(0.1)

out = Path(__file__).parent
with sync_playwright() as pw:
    try:
        b = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        b = pw.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8124/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "Passw0rd-long-1")
    page.click("button[type=submit],button:not([type])")
    page.wait_for_url(re.compile(r"127\.0\.0\.1:8124/(?!login)"), timeout=15000)
    page.goto("http://127.0.0.1:8124/settings")
    page.wait_for_timeout(700)
    info = page.evaluate(
        """() => {
        const out = {};
        const lab = document.querySelector('.tgl');
        const inp = lab.querySelector('input');
        const sp = lab.querySelector('span');
        const rect = (el) => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; };
        out.label = rect(lab);
        out.input = rect(inp);
        out.span = rect(sp);
        const cs = getComputedStyle(sp);
        const ca = getComputedStyle(sp, '::after');
        out.spanStyle = {position:cs.position, inset:cs.inset, top:cs.top, left:cs.left, background:cs.background.slice(0,80)};
        out.afterStyle = {top:ca.top, left:ca.left, width:ca.width, height:ca.height, position:ca.position};
        out.labStyle = (() => { const c = getComputedStyle(lab); return {display:c.display, width:c.width, height:c.height, position:c.position}; })();
        return out;
    }"""
    )
    print(info)
    tgl = page.locator(".tgl").first
    tgl.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    row = page.locator(".setrow", has=tgl).first
    row.screenshot(path=str(out / "05-tgl-row.png"))
    box = tgl.bounding_box()
    page.screenshot(path=str(out / "06-tgl-zoom.png"),
                    clip={"x": box["x"] - 20, "y": box["y"] - 40, "width": 260, "height": 120})
    b.close()
server.should_exit = True
print("done")
