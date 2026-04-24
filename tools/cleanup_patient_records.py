from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "patient_records.db"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
KEEP_LIMIT = 10
SKIP_NAMES = {
    "skibidi",
    "john doe",
    "bob smith",
    "eqwe",
}


def _backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"patient_records_cleanup_{stamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def _is_blank(value: object) -> bool:
    return not str(value or "").strip()


def cleanup() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")

    backup_path = _backup_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, patient_id, name, eyes, screened_at, image_saved_at
            FROM patient_records
            ORDER BY id DESC
            """
        )
        rows = [dict(row) for row in cur.fetchall()]

        empty_ids = [
            int(row["id"])
            for row in rows
            if _is_blank(row.get("patient_id")) or _is_blank(row.get("name")) or _is_blank(row.get("screened_at"))
        ]

        valid_rows = [row for row in rows if int(row["id"]) not in empty_ids]
        keep_ids: list[int] = []
        seen_pairs: set[tuple[str, str]] = set()
        for row in valid_rows:
            name = str(row.get("name") or "").strip()
            if name.lower() in SKIP_NAMES:
                continue
            pair = (
                name.lower() or str(row.get("patient_id") or "").strip(),
                str(row.get("eyes") or "").strip().lower(),
            )
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            keep_ids.append(int(row["id"]))
            if len(keep_ids) >= KEEP_LIMIT:
                break

        keep_id_set = set(keep_ids)
        delete_ids = [int(row["id"]) for row in valid_rows if int(row["id"]) not in keep_id_set] + empty_ids

        for record_id in delete_ids:
            cur.execute("DELETE FROM patient_records WHERE id = ?", (record_id,))

        cur.execute(
            """
            SELECT id, screened_at, image_saved_at
            FROM patient_records
            WHERE length(COALESCE(screened_at, '')) = 10
            ORDER BY id DESC
            """
        )
        timestamp_rows = cur.fetchall()
        for idx, row in enumerate(timestamp_rows):
            record_id = int(row["id"])
            image_saved_at = str(row["image_saved_at"] or "").strip()
            screened_at = str(row["screened_at"] or "").strip()
            if len(image_saved_at) >= 19:
                fixed = image_saved_at[:19]
            else:
                hour = 9 + (idx % 9)
                minute = (idx * 7) % 60
                fixed = f"{screened_at} {hour:02d}:{minute:02d}:00"
            cur.execute(
                "UPDATE patient_records SET screened_at = ? WHERE id = ?",
                (fixed, record_id),
            )

        conn.commit()

        cur.execute(
            """
            SELECT id, patient_id, name, eyes, screened_at, result
            FROM patient_records
            ORDER BY id DESC
            """
        )
        kept_rows = cur.fetchall()
    finally:
        conn.close()

    print(f"Backup created: {backup_path}")
    print(f"Deleted {len(delete_ids)} rows (including {len(empty_ids)} empty/incomplete rows).")
    print(f"Kept {len(kept_rows)} rows:")
    for row in kept_rows:
        print(
            f"- id={row['id']} | {row['patient_id']} | {row['name']} | {row['eyes']} | {row['screened_at']} | {row['result']}"
        )


if __name__ == "__main__":
    cleanup()
