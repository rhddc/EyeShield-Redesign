"""
Seed a clean demo patient with progression flow (stable + worsening).

Creates a single patient_id with 3 visits in `patient_records`:
- initial (Moderate DR)
- follow_up (Stable: Moderate DR)
- follow_up (Worsening: Severe DR)

Run:
  python scripts/seed_progression_patient.py
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _pick_db_path() -> Path:
    # App code uses DB_FILE from app/auth.py which points at a project DB.
    # Prefer repo root `patient_records.db` if present, else `data/patient_records.db`, else `app/patient_records.db`.
    candidates = [
        REPO_ROOT / "patient_records.db",
        REPO_ROOT / "data" / "patient_records.db",
        REPO_ROOT / "app" / "patient_records.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Default to repo root so the file is easy to find.
    return candidates[0]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # Create base table if it doesn't exist yet (some DB files may be empty placeholders).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT,
            name TEXT,
            birthdate TEXT,
            age TEXT,
            sex TEXT,
            contact TEXT,
            eyes TEXT,
            diabetes_type TEXT,
            duration TEXT,
            hba1c TEXT,
            prev_treatment TEXT,
            notes TEXT,
            result TEXT,
            confidence TEXT,
            archived_at TEXT,
            archived_by TEXT,
            archive_reason TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(patient_records)")
    existing = {r[1] for r in cur.fetchall()}

    # Minimal set needed for follow-up flow and UI cards.
    required = {
        "screened_at": "TEXT",
        "doctor_classification": "TEXT",
        "ai_classification": "TEXT",
        "final_diagnosis_icdr": "TEXT",
        "decision_mode": "TEXT",
        "decision_by_username": "TEXT",
        "decision_at": "TEXT",
        "doctor_findings": "TEXT",
        "visual_acuity_left": "TEXT",
        "visual_acuity_right": "TEXT",
        "blood_pressure_systolic": "TEXT",
        "blood_pressure_diastolic": "TEXT",
        "fasting_blood_sugar": "TEXT",
        "random_blood_sugar": "TEXT",
        "diabetes_diagnosis_date": "TEXT",
        "symptom_blurred_vision": "TEXT",
        "symptom_floaters": "TEXT",
        "symptom_flashes": "TEXT",
        "symptom_vision_loss": "TEXT",
        "height": "TEXT",
        "weight": "TEXT",
        "bmi": "TEXT",
        "treatment_regimen": "TEXT",
        "prev_dr_stage": "TEXT",
        "follow_up": "TEXT",
        "followup_date": "TEXT",
        "followup_label": "TEXT",
        "screening_type": "TEXT",
        "previous_screening_id": "INTEGER",
        "screening_group_id": "TEXT",
        "source_image_path": "TEXT",
        "heatmap_image_path": "TEXT",
    }

    for name, col_type in required.items():
        if name in existing:
            continue
        cur.execute(f"ALTER TABLE patient_records ADD COLUMN {name} {col_type}")
    conn.commit()


def main() -> None:
    db_path = _pick_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_columns(conn)
        cur = conn.cursor()

        patient_id = "ES-DEMO-PROG-0001"
        name = "Demo Progression Patient"

        # Clear any existing seeded rows for idempotence.
        cur.execute("DELETE FROM patient_records WHERE patient_id = ?", (patient_id,))

        now = datetime.now().replace(microsecond=0)
        t0 = now - timedelta(days=180)
        t1 = now - timedelta(days=90)
        t2 = now - timedelta(days=7)

        group_id = f"{patient_id}-{now.strftime('%Y%m%d%H%M%S')}"

        def insert_visit(
            *,
            screened_at: datetime,
            screening_type: str,
            previous_id: int | None,
            dr_grade: str,
            follow_up: str,
            followup_date: str,
            followup_label: str,
            vitals_variant: str,
        ) -> int:
            # Vitals/clinical history are intentionally different per visit so you can verify
            # the UI shows "details for that specific date".
            if vitals_variant == "baseline":
                bp_sys, bp_dia = "132", "84"
                fbs, rbs = "145", "210"
                hba1c = "8.4%"
                duration = "6"
                regimen = "Oral meds"
            elif vitals_variant == "stable":
                bp_sys, bp_dia = "128", "82"
                fbs, rbs = "138", "198"
                hba1c = "8.1%"
                duration = "6.5"
                regimen = "Oral meds"
            else:  # worsening
                bp_sys, bp_dia = "146", "92"
                fbs, rbs = "172", "255"
                hba1c = "9.2%"
                duration = "7"
                regimen = "Oral + insulin"

            data = {
                "patient_id": patient_id,
                "name": name,
                "birthdate": "1973-05-14",
                "age": "52",
                "sex": "Female",
                "contact": "0712345678",
                "eyes": "Right Eye",
                "diabetes_type": "Type 2",
                "duration": str(duration),
                "hba1c": hba1c,
                "prev_treatment": "No",
                "notes": "Seeded demo record for progression testing.",
                "result": dr_grade,
                "confidence": "Confidence: 84% | Uncertainty: 12%",
                "screened_at": screened_at.strftime("%Y-%m-%d %H:%M:%S"),
                "ai_classification": dr_grade,
                "doctor_classification": dr_grade,
                "final_diagnosis_icdr": dr_grade,
                "decision_mode": "accepted",
                "decision_by_username": os.environ.get("EYESHIELD_CURRENT_USER", "demo.clinician"),
                "decision_at": screened_at.strftime("%Y-%m-%d %H:%M:%S"),
                "doctor_findings": f"{screening_type} visit. Seeded clinical note.",
                "visual_acuity_left": "6/9",
                "visual_acuity_right": "6/12",
                "blood_pressure_systolic": bp_sys,
                "blood_pressure_diastolic": bp_dia,
                "fasting_blood_sugar": fbs,
                "random_blood_sugar": rbs,
                "diabetes_diagnosis_date": "2018-02-10",
                "symptom_blurred_vision": "Yes" if vitals_variant != "baseline" else "No",
                "symptom_floaters": "No",
                "symptom_flashes": "No",
                "symptom_vision_loss": "No",
                "height": "165",
                "weight": "78" if vitals_variant == "worsening" else "76",
                "bmi": "28.7" if vitals_variant == "worsening" else "27.9",
                "treatment_regimen": regimen,
                "prev_dr_stage": "None" if screening_type == "initial" else "Moderate DR",
                "follow_up": follow_up,
                "followup_date": followup_date,
                "followup_label": followup_label,
                "screening_type": screening_type,
                "previous_screening_id": previous_id,
                "screening_group_id": group_id,
                "source_image_path": "",
                "heatmap_image_path": "",
            }

            cols = ", ".join(data.keys())
            qs = ", ".join(["?"] * len(data))
            cur.execute(f"INSERT INTO patient_records ({cols}) VALUES ({qs})", tuple(data.values()))
            return int(cur.lastrowid)

        initial_id = insert_visit(
            screened_at=t0,
            screening_type="initial",
            previous_id=None,
            dr_grade="Moderate DR",
            follow_up="",
            followup_date="",
            followup_label="",
            vitals_variant="baseline",
        )

        stable_id = insert_visit(
            screened_at=t1,
            screening_type="follow_up",
            previous_id=initial_id,
            dr_grade="Moderate DR",
            follow_up="Yes",
            followup_date=t1.strftime("%Y-%m-%d %H:%M:%S"),
            followup_label="Follow-up screening (stable)",
            vitals_variant="stable",
        )

        _ = insert_visit(
            screened_at=t2,
            screening_type="follow_up",
            previous_id=stable_id,
            dr_grade="Severe DR",
            follow_up="Yes",
            followup_date=t2.strftime("%Y-%m-%d %H:%M:%S"),
            followup_label="Follow-up screening (worsening)",
            vitals_variant="worsening",
        )

        conn.commit()
        print(f"Seeded patient_records in: {db_path}")
        print(f"Patient ID: {patient_id}")
        print("Visits: initial -> follow-up (stable) -> follow-up (worsening)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

