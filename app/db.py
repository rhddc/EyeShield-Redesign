from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Optional

from app_paths import PATIENT_RECORDS_DB_PATH, USERS_DB_PATH


def get_users_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(USERS_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_records_conn() -> sqlite3.Connection:
    return sqlite3.connect(str(PATIENT_RECORDS_DB_PATH))


def ensure_patient_records_db_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure `data/patient_records.db` has the tables/columns expected by:
    - `app/reports.py` (refresh_report SELECT)
    - `app/dashboard.py` (KPI query)
    - `app/screening_form.py` (inserts/updates)

    This intentionally lives outside `auth.py` because `users.db` should not be the
    home of legacy screening records.
    """
    cur = conn.cursor()
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

    required_columns = {
        "archived_at": "TEXT",
        "archived_by": "TEXT",
        "archive_reason": "TEXT",
        "original_screener_username": "TEXT",
        "original_screener_name": "TEXT",
        "screened_at": "TEXT",
        "source_image_path": "TEXT",
        "heatmap_image_path": "TEXT",
        "image_sha256": "TEXT",
        "image_saved_at": "TEXT",
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
        "ai_classification": "TEXT",
        "doctor_classification": "TEXT",
        "decision_mode": "TEXT",
        "override_justification": "TEXT",
        "final_diagnosis_icdr": "TEXT",
        "doctor_findings": "TEXT",
        "decision_by_username": "TEXT",
        "decision_at": "TEXT",
        "follow_up": "TEXT",
        "followup_date": "TEXT",
        "followup_label": "TEXT",
        "screening_type": "TEXT",
        "previous_screening_id": "INTEGER",
        "screening_group_id": "TEXT",
    }
    cur.execute("PRAGMA table_info(patient_records)")
    existing = {row[1] for row in cur.fetchall()}
    for col, col_type in required_columns.items():
        if col in existing:
            continue
        cur.execute(f"ALTER TABLE patient_records ADD COLUMN {col} {col_type}")
    conn.commit()


def ensure_patient_records_db() -> tuple[bool, str]:
    try:
        conn = get_records_conn()
    except OSError as err:
        return False, f"Cannot open patient_records.db: {err}"
    try:
        ensure_patient_records_db_schema(conn)
        return True, ""
    except sqlite3.Error as err:
        return False, f"patient_records.db schema migration failed: {err}"
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def records_db_path() -> Path:
    return Path(PATIENT_RECORDS_DB_PATH)


def users_db_path() -> Path:
    return Path(USERS_DB_PATH)

