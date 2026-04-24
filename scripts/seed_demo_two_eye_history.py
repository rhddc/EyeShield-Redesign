from __future__ import annotations

import sqlite3
from pathlib import Path


def seed() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    db_path = (repo_root / "data" / "patient_records.db").resolve()

    if not db_path.exists():
        raise FileNotFoundError(f"Missing DB at {db_path}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='patient_records'")
    if not cur.fetchone():
        raise RuntimeError("patient_records table missing")

    patient_id = "DEMO-2EYE-001"
    patient_name = "Demo Two-Eye Patient"
    base = "stored_images"

    visits = [
        {
            "group": "DEMO-2EYE-001-V1",
            "screened_at": "2026-04-20 09:15:00",
            "screening_type": "initial",
            "od": {
                "eyes": "Right",
                "src": f"{base}/EYS-2026-00007/20260322_093627_right_eye_source.jpg",
                "hm": f"{base}/EYS-2026-00007/20260322_093627_right_eye_heatmap.png",
                "sev": "No DR",
                "conf": "Confidence: 92.4% | Uncertainty: 7.6%",
            },
            "os": {
                "eyes": "Left",
                "src": f"{base}/EYS-2026-00007/20260322_093627_left_eye_source.jpg",
                "hm": f"{base}/EYS-2026-00007/20260322_093627_left_eye_heatmap.png",
                "sev": "Mild DR",
                "conf": "Confidence: 86.0% | Uncertainty: 14.0%",
            },
            "notes": "Baseline screening (demo data).",
        },
        {
            "group": "DEMO-2EYE-001-V2",
            "screened_at": "2026-04-22 14:40:00",
            "screening_type": "follow_up",
            "od": {
                "eyes": "Right",
                "src": f"{base}/ES-260329-W5PWP/20260329_235131_right_eye_source.jpg",
                "hm": f"{base}/ES-260329-W5PWP/20260329_235131_right_eye_heatmap.png",
                "sev": "Moderate DR",
                "conf": "Confidence: 78.2% | Uncertainty: 21.8%",
            },
            "os": {
                "eyes": "Left",
                "src": f"{base}/ES-260329-W5PWP/20260329_230135_left_eye_source.jpg",
                "hm": f"{base}/ES-260329-W5PWP/20260329_230135_left_eye_heatmap.png",
                "sev": "Moderate DR",
                "conf": "Confidence: 80.1% | Uncertainty: 19.9%",
            },
            "notes": "Follow-up screening (demo data).",
        },
        {
            "group": "DEMO-2EYE-001-V3",
            "screened_at": "2026-04-24 11:05:00",
            "screening_type": "follow_up",
            "od": {
                "eyes": "Right",
                "src": f"{base}/ES-260324-YUMRT/20260324_232914_right_eye_source.jpg",
                "hm": f"{base}/ES-260324-YUMRT/20260324_232914_right_eye_heatmap.png",
                "sev": "Severe DR",
                "conf": "Confidence: 69.5% | Uncertainty: 30.5%",
            },
            "os": {
                "eyes": "Left",
                "src": f"{base}/ES-260324-YUMRT/20260324_232942_left_eye_source.jpg",
                "hm": f"{base}/ES-260324-YUMRT/20260324_232942_left_eye_heatmap.png",
                "sev": "Proliferative DR",
                "conf": "Confidence: 64.0% | Uncertainty: 36.0%",
            },
            "notes": "Latest follow-up screening (demo data).",
        },
    ]

    # Idempotency: clear prior seeded rows for this demo patient.
    cur.execute("DELETE FROM patient_records WHERE patient_id = ?", (patient_id,))
    conn.commit()

    insert_sql = """
    INSERT INTO patient_records (
      patient_id, name, eyes, screened_at,
      source_image_path, heatmap_image_path,
      ai_classification, doctor_classification, final_diagnosis_icdr,
      confidence,
      screening_group_id, screening_type, previous_screening_id,
      notes, doctor_findings
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    inserted_ids: list[int] = []
    prev_primary_id: int | None = None
    for v in visits:
        for side_key, side_label in (("od", "Right (OD)"), ("os", "Left (OS)")):
            e = v[side_key]
            cur.execute(
                insert_sql,
                (
                    patient_id,
                    patient_name,
                    e["eyes"],
                    v["screened_at"],
                    e["src"],
                    e["hm"],
                    e["sev"],
                    e["sev"],
                    e["sev"],
                    e["conf"],
                    v["group"],
                    v["screening_type"],
                    prev_primary_id,
                    v["notes"],
                    f"{side_label}: {e['sev']} (demo)",
                ),
            )
            inserted_ids.append(int(cur.lastrowid))
        prev_primary_id = max(inserted_ids[-2:])

    conn.commit()

    cur.execute(
        """
        SELECT screened_at, eyes, screening_group_id, ai_classification, source_image_path, heatmap_image_path
        FROM patient_records
        WHERE patient_id = ?
        ORDER BY screened_at ASC, id ASC
        """,
        (patient_id,),
    )
    rows = cur.fetchall()
    conn.close()

    print(f"Seeded patient_id={patient_id} rows={len(rows)}")
    for r in rows:
        print(r)


if __name__ == "__main__":
    seed()

