"""Consistent SQLite backup with timestamp; keeps the newest 30 copies."""
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

src = os.environ.get("APP_DB", "agents.db")
out_dir = Path(os.environ.get("APP_BACKUP_DIR", "backups"))
out_dir.mkdir(exist_ok=True)
dest = out_dir / f"agents-{datetime.now():%Y%m%d-%H%M%S}.db"
with sqlite3.connect(src) as s, sqlite3.connect(dest) as d:
    s.backup(d)
for old in sorted(out_dir.glob("agents-*.db"))[:-30]:
    old.unlink()
print(f"backup written: {dest}", file=sys.stderr)
