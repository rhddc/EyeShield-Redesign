"""
Print EMR uniqueness indexes (uq_emr_*).

Usage:
  python scripts/check_emr_indexes.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from app.auth import UserManager
    db = repo / "data" / "users.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    # Ensure schema (and indexes) exist.
    UserManager._ensure_emr_schema(conn)
    conn.commit()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uq_emr_%' ORDER BY name")
    rows = [r[0] for r in cur.fetchall()]
    print(f"uq_indexes={len(rows)}")
    for name in rows:
        print(name)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

