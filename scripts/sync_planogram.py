"""Copies the built 3D store (Store Walk) into Retail OS so it opens inside the system as "Planogram".
Run again whenever the 3D store is rebuilt:   python scripts/sync_planogram.py [path-to-store-walk-dist]
The built files use absolute web paths (/products/...), so they are moved under /planogram-app/ while copying."""
import re
import shutil
import sys
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\User\3d store\store-walk\dist")
DST = Path(__file__).resolve().parent.parent / "planogram"
PFX = "/planogram-app"
if not (SRC / "index.html").exists():
    sys.exit(f"No built store found in {SRC} (run 'npm run build' there first)")
if DST.exists():
    shutil.rmtree(DST)
shutil.copytree(SRC, DST)

names = r"(?:warehouse-products|products-catalog|product-images|products|entrance-sign\.png|sign-dailycentro\.png|models|assets)"
rewrite = lambda s: re.sub(r"([\"'`(])/(" + names + r")", lambda m: f"{m.group(1)}{PFX}/{m.group(2)}", s)
for f in list(DST.glob("index.html")) + list((DST / "assets").glob("*.js")) + list((DST / "assets").glob("*.css")) + list(DST.rglob("manifest.json")):
    f.write_text(rewrite(f.read_text(encoding="utf-8")), encoding="utf-8")
print("Planogram synced:", sum(1 for _ in DST.rglob("*") if _.is_file()), "files")
