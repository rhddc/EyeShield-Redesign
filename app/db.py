from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from .app_paths import PATIENT_RECORDS_DB_PATH, USERS_DB_PATH
except Exception:  # pragma: no cover
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
        "phone": "TEXT",
        "email": "TEXT",
        "address": "TEXT",
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
        # Demo/seed data must never be inserted implicitly during normal app use.
        # Enable explicitly when needed for demos: set EYESHIELD_ENABLE_LEGACY_SEED=1.
        if str(__import__("os").environ.get("EYESHIELD_ENABLE_LEGACY_SEED", "")).strip() == "1":
            _seed_mock_patient_records_if_empty(conn)
        return True, ""
    except sqlite3.Error as err:
        return False, f"patient_records.db schema migration failed: {err}"
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _seed_mock_patient_records_if_empty(conn: sqlite3.Connection) -> None:
    """Seed a few rows for demo/testing when the records DB is empty."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) FROM patient_records WHERE archived_at IS NULL")
    n = int(cur.fetchone()[0] or 0)
    if n > 0:
        return

    now = datetime.now()
    samples = [
        {
            "patient_id": "ES-0001",
            "name": "Juan Dela Cruz",
            "birthdate": "1978-02-14",
            "age": "48",
            "sex": "Male",
            "contact": "09171234567",
            "eyes": "Right Eye",
            "diabetes_type": "Type 2",
            "duration": "12",
            "hba1c": "8.4%",
            "prev_treatment": "No",
            "notes": "Seed record (mock).",
            "result": "Moderate DR",
            "confidence": "0.86",
            "screened_at": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "screening_type": "initial",
            "follow_up": "",
            "followup_label": "",
            "followup_date": "",
        },
        {
            "patient_id": "ES-0002",
            "name": "Maria Santos",
            "birthdate": "1969-09-03",
            "age": "56",
            "sex": "Female",
            "contact": "09981234567",
            "eyes": "Left Eye",
            "diabetes_type": "Type 2",
            "duration": "18",
            "hba1c": "9.1%",
            "prev_treatment": "Yes",
            "notes": "Seed record (mock).",
            "result": "Severe DR",
            "confidence": "0.79",
            "screened_at": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "screening_type": "follow_up",
            "follow_up": "Yes",
            "followup_label": "Follow-up screening",
            "followup_date": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        },
        {
            "patient_id": "ES-0003",
            "name": "Test Patient",
            "birthdate": "1990-01-01",
            "age": "36",
            "sex": "Other",
            "contact": "09000000000",
            "eyes": "",
            "diabetes_type": "",
            "duration": "",
            "hba1c": "",
            "prev_treatment": "No",
            "notes": "Queued placeholder (mock).",
            "result": "Queued",
            "confidence": "",
            "screened_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "screening_type": "initial",
            "follow_up": "",
            "followup_label": "",
            "followup_date": "",
        },
    ]

    for s in samples:
        cur.execute(
            """
            INSERT INTO patient_records (
                patient_id, name, birthdate, age, sex, contact, eyes,
                diabetes_type, duration, hba1c, prev_treatment, notes,
                result, confidence, screened_at,
                screening_type, follow_up, followup_label, followup_date,
                decision_mode
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?
            )
            """,
            (
                s["patient_id"],
                s["name"],
                s["birthdate"],
                s["age"],
                s["sex"],
                s["contact"],
                s["eyes"],
                s["diabetes_type"],
                s["duration"],
                s["hba1c"],
                s["prev_treatment"],
                s["notes"],
                s["result"],
                s["confidence"],
                s["screened_at"],
                s["screening_type"],
                s["follow_up"],
                s["followup_label"],
                s["followup_date"],
                "pending",
            ),
        )
    conn.commit()


def records_db_path() -> Path:
    return Path(PATIENT_RECORDS_DB_PATH)


def users_db_path() -> Path:
    return Path(USERS_DB_PATH)

