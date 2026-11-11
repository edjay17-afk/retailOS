"""Find state-machine evidence without importing the app or reading its database.

Run: python scripts/inventory_state_machines.py [--output path.json]
This is an evidence index, not an inferred transition graph. See
docs/state-machines.md for the reviewed interpretation and limitations.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MARKER = re.compile(
    r"status|state|pending|busy|loading|locked|active|cancelled|verified|"
    r"completed|submitted|approved|reconciled|deleted|expired|failed|"
    r"in_flight|inflight|controller|sequence|\bseq\b|dirty|uncertain|"
    r"last_success|consecutive_failures|content_hash|synced_at|"
    r"must_change_password|locked_until|read_at|sent_at|detail_ok",
    re.IGNORECASE,
)


def inventory(root: Path = ROOT) -> dict:
    records = []
    files = []
    for path in sorted((root / "app").rglob("*")):
        if path.suffix not in (".py", ".js", ".html") or "vendor" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        files.append(relative)
        source = path.read_text(encoding="utf-8-sig")
        owners = {}
        if path.suffix == ".py":
            tree = ast.parse(source, filename=relative)
            # Innermost function/class supplies context for each evidence line.
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    for line in range(node.lineno, node.end_lineno + 1):
                        owners[line] = node.name
        for number, line in enumerate(source.splitlines(), 1):
            if MARKER.search(line):
                records.append({"path": relative, "line": number,
                                "owner": owners.get(number), "source": line.strip()})
    return {"schema_version": 1, "scope": "app Python, JavaScript and templates; excludes vendor",
            "limitations": "Heuristic evidence, not proof of completeness or legal transitions; includes false positives.",
            "files_scanned": files, "evidence": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(inventory(), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
