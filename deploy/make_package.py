"""Run on the Windows PC (with the system stopped or idle): python deploy/make_package.py
Writes retailos-cloud.tar.gz: the code, a consistent copy of the database, the uploads and a Linux-ready .env."""
import re
import sqlite3
import tarfile
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
out = root / "retailos-cloud.tar.gz"
tmp = Path(tempfile.mkdtemp())
db_copy = tmp / "retailos.db"
src = sqlite3.connect(root / "data" / "retailos.db")
dst = sqlite3.connect(db_copy)
src.backup(dst)
dst.close(); src.close()

env_lines = []
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    if re.match(r"(DATA_DIR|HOST|PORT|BEHIND_PROXY)=", line):
        continue
    env_lines.append(line)
env_lines += ["DATA_DIR=/placeholder", "HOST=127.0.0.1", "PORT=8000", "BEHIND_PROXY=true"]
env_file = tmp / ".env"
env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "tests", "docs"}
with tarfile.open(out, "w:gz") as tar:
    for p in sorted((root / "app").rglob("*")):
        if p.is_file() and not (SKIP_DIRS & set(p.parts)):
            tar.add(p, arcname=p.relative_to(root).as_posix())
    for name in ("requirements.txt",):
        tar.add(root / name, arcname=name)
    for p in sorted((root / "deploy").glob("*")):
        tar.add(p, arcname=f"deploy/{p.name}")
    tar.add(db_copy, arcname="data/retailos.db")
    tar.add(env_file, arcname=".env")
    up = root / "data" / "uploads"
    if up.exists():
        tar.add(up, arcname="data/uploads")
print("Wrote", out, round(out.stat().st_size / 1e6, 1), "MB")
