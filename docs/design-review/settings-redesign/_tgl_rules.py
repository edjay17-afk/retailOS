"""Find every CSS rule that matches the .tgl label and sets display/height/margin."""
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
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8125, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(50):
    if server.started:
        break
    time.sleep(0.1)

with sync_playwright() as pw:
    try:
        b = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        b = pw.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8125/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "Passw0rd-long-1")
    page.click("button[type=submit],button:not([type])")
    page.wait_for_url(re.compile(r"127\.0\.0\.1:8125/(?!login)"), timeout=15000)
    page.goto("http://127.0.0.1:8125/settings")
    page.wait_for_timeout(700)
    hits = page.evaluate(
        """() => {
        const lab = document.querySelector('.tgl');
        const sp = lab.querySelector('span');
        const out = [];
        for (const sheet of document.styleSheets) {
            let rules;
            try { rules = sheet.cssRules; } catch (e) { continue; }
            for (const rule of rules) {
                if (!rule.selectorText || !rule.style) continue;
                const d = rule.style;
                const interesting = d.display || d.height || d.minHeight || d.margin || d.marginTop || d.alignItems || d.position || d.padding || d.lineHeight || d.top || d.inset;
                if (!interesting) continue;
                let el = null;
                try {
                    if (lab.matches(rule.selectorText)) el = 'label';
                    else if (sp.matches(rule.selectorText)) el = 'span';
                } catch (e) { continue; }
                if (el) out.push({el, sel: rule.selectorText, css: rule.style.cssText.slice(0, 200), href: sheet.href ? sheet.href.split('/').pop() : 'inline'});
            }
        }
        return out;
    }"""
    )
    for h in hits:
        print(h)
    b.close()
server.should_exit = True
print("done")
