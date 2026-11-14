"""Visual check: boot the app on a throwaway DB seeded with realistic sales, screenshot current /sales/employees."""
import random
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import uvicorn
from playwright.sync_api import sync_playwright

from app import db as dbm
from app.config import Config, set_config
from app.dbinit import init_db
from app.models import Employee, Location, SalesLine, SalesTx, SyncState, User
from app.security import hash_password

tmp = Path(tempfile.mkdtemp())
set_config(Config(loyverse_reporting_enabled=False, loyverse_tokens_file="", data_dir=tmp,
                  storehub_username="t", storehub_token="t", scheduler_enabled=False, secret_key="k" * 32))
dbm.init_engine()
init_db()
s = dbm.SessionLocal()
s.add(User(username="admin", full_name="Admin", role="admin", password_hash=hash_password("Passw0rd-long-1")))

stores = [
    Location(storehub_id="L1", name="The Daily Centro - Bantayan", type="store", monitored=True, city="Bantayan"),
    Location(storehub_id="L2", name="The Daily Centro - Getafe", type="store", monitored=True, city="Getafe"),
    Location(storehub_id="L3", name="ZENWOK Balamban", type="store", monitored=True, city="Balamban"),
]
s.add_all(stores)
people = [
    ("E1", "Maria", "Santos"), ("E2", "Jose", "Reyes"), ("E3", "Ana", "Cruz"), ("E4", "Mark", "Villanueva"),
    ("E5", "Jenny", "Lim"), ("E6", "Paolo", "Dizon"), ("E7", "Kim", "Torres"), ("E8", "Rita", "Gomez"),
]
s.add_all(Employee(storehub_id=sh, first_name=f, last_name=l) for sh, f, l in people)
s.flush()

random.seed(11)
# (employee index, skill multiplier, home store index) — one star, solid mids, two low
skill = [2.1, 1.45, 1.2, 1.05, 0.95, 0.8, 0.62, 0.45]
home = [0, 0, 1, 1, 2, 0, 2, 1]
today = datetime.now().date()
ref = 0
for day in range(13, -1, -1):
    d = today - timedelta(days=day)
    dow = d.weekday()
    weekend = 1.35 if dow >= 5 else (0.85 if dow == 0 else 1.0)
    for ei, (sh, _, _) in enumerate(people):
        if random.random() < 0.12:  # day off
            continue
        n_tx = max(1, round(random.gauss(26, 7) * skill[ei] * weekend / 2))
        for _ in range(n_tx):
            ref += 1
            loc = stores[home[ei] if random.random() < 0.85 else random.randrange(3)]
            basket = max(35.0, random.gauss(210, 70))
            cancelled = random.random() < 0.02
            t = datetime(d.year, d.month, d.day, random.randint(7, 21), random.randint(0, 59))
            tx = SalesTx(refid=f"R{ref:05d}", invoice_no=str(10000 + ref), location_id=loc.id,
                         employee_sh_id=sh, terminal=random.choice([1, 2]), tx_type="Sale",
                         tx_time=t, business_date=d, total=basket if not cancelled else 0.0,
                         subtotal=basket, tax=basket * 0.12, cancelled=cancelled,
                         cancelled_time=t if cancelled else None, raw_hash=f"h{ref}")
            s.add(tx)
            s.flush()
            for li, q in enumerate([random.randint(1, 4) for _ in range(random.randint(1, 3))], 1):
                s.add(SalesLine(tx_id=tx.id, line_no=li, qty=q, unit_price=round(basket / q, 2),
                                subtotal=basket, total=basket))
s.add(SyncState(domain="sales", last_success_at=datetime.now() - timedelta(minutes=4)))
s.commit()

from app.web.main import create_app
app = create_app()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8126, log_level="error"))
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
    page.goto("http://127.0.0.1:8126/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "Passw0rd-long-1")
    page.click("button[type=submit],button:not([type])")
    page.wait_for_url(re.compile(r"127\.0\.0\.1:8126/(?!login)"), timeout=15000)
    page.goto("http://127.0.0.1:8126/sales/employees")
    page.wait_for_timeout(800)
    page.screenshot(path=str(out / "00-current-desktop.png"), full_page=True)
    m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    m.goto("http://127.0.0.1:8126/login")
    m.fill('input[name="username"]', "admin")
    m.fill('input[name="password"]', "Passw0rd-long-1")
    m.click("button[type=submit],button:not([type])")
    m.wait_for_url(re.compile(r"127\.0\.0\.1:8126/(?!login)"), timeout=15000)
    m.goto("http://127.0.0.1:8126/sales/employees")
    m.wait_for_timeout(800)
    m.screenshot(path=str(out / "01-current-mobile.png"), full_page=True)
    b.close()
server.should_exit = True
print("done")
