#!/usr/bin/env bash
# Nightly: a consistent copy of the database plus the uploaded photos, kept for 14 days in ~/backups.
set -euo pipefail
APP="$HOME/retailos"; OUT="$HOME/backups"; STAMP=$(date +%F)
mkdir -p "$OUT"
sqlite3 "$APP/data/retailos.db" ".backup '$OUT/retailos-$STAMP.db'"
tar -czf "$OUT/uploads-$STAMP.tar.gz" -C "$APP/data" uploads 2>/dev/null || true
find "$OUT" -type f -mtime +14 -delete
