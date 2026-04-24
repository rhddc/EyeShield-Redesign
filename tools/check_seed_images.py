from __future__ import annotations

import os
import sqlite3


def main() -> None:
    conn = sqlite3.connect(os.path.join("data", "patient_records.db"))
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source_image_path, heatmap_image_path
        FROM patient_records
        WHERE COALESCE(TRIM(source_image_path), '') <> ''
        LIMIT 10
        """
    )
    rows = cur.fetchall()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM patient_records
        WHERE patient_id LIKE 'EYS-%'
          AND (COALESCE(TRIM(source_image_path), '') = '' OR COALESCE(TRIM(heatmap_image_path), '') = '')
        """
    )
    missing_seeded = int((cur.fetchone() or [0])[0])
    conn.close()

    print("rows_with_images", len(rows))
    missing = 0
    for src, heat in rows:
        for p in (src, heat):
            if not p:
                missing += 1
                continue
            abs_path = os.path.join("app", str(p).replace("/", os.sep))
            if not os.path.exists(abs_path):
                missing += 1
    print("missing_files_in_sample", missing)
    print("seeded_rows_missing_source_or_heatmap", missing_seeded)
    if rows:
        print("sample", rows[0])


if __name__ == "__main__":
    main()

