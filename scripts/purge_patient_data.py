"""
Purge all patient-domain data (EMR tables) while keeping user accounts.

This resets:
- emr_patients
- emr_queue_entries
- emr_visit_details
- emr_screenings
- emr_screening_eyes

Usage:
  python scripts/purge_patient_data.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def main() -> int:
    db = Path(__file__).resolve().parents[1] / "data" / "users.db"
    if not db.exists():
        raise SystemExit(f"Missing database: {db}")

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    existing = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    tables = [
        "emr_screening_eyes",
        "emr_screenings",
        "emr_visit_details",
        "emr_queue_entries",
        "emr_patients",
    ]

    deleted: dict[str, int | None] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for t in tables:
            if t not in existing:
                deleted[t] = None
                continue
            cur.execute(f"DELETE FROM {t}")
            deleted[t] = int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()

    print("purge_ok")
    for t in tables:
        print(f"{t}: {deleted[t]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

