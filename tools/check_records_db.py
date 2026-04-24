from __future__ import annotations

import os
import sqlite3


def main() -> None:
    db = os.path.join("data", "patient_records.db")
    print("db", db)
    print("exists", os.path.exists(db))
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='patient_records'")
    has_table = bool(cur.fetchone())
    print("has_table", has_table)
    if has_table:
        cur.execute("SELECT COUNT(*) FROM patient_records")
        print("count", int(cur.fetchone()[0] or 0))
    conn.close()


if __name__ == "__main__":
    main()

