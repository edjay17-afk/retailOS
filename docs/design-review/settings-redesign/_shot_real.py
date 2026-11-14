"""One-off visual check: boot the app on a throwaway DB, sign in as admin, screenshot /settings."""
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
# one changed setting, to show the badge + audit line
from app import settings_service as cfg
cfg.set_(s, "fresh.sales_min", 25)
from app import audit
audit.log(s, actor_type="human", actor_id=1, action="setting.update", entity_type="setting", entity_id="fresh.sales_min",
          before={"value": 20}, after={"value": 25})
s.commit()

from app.web.main import create_app
app = create_app()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8123, log_level="error"))
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
    page.goto("http://127.0.0.1:8123/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "Passw0rd-long-1")
    page.click("button[type=submit],button:not([type])")
    page.wait_for_url(re.compile(r"127\.0\.0\.1:8123/(?!login)"), timeout=15000)
    page.goto("http://127.0.0.1:8123/settings")
    page.wait_for_timeout(700)
    page.screenshot(path=str(out / "03-implemented-page.png"), full_page=True)
    # danger group scroll-into-view shot
    page.locator("#setgroup-ai").scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    page.locator("#setgroup-ai").screenshot(path=str(out / "04-implemented-danger.png"))
    b.close()
server.should_exit = True
print("done")
