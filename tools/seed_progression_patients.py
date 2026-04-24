from __future__ import annotations

import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
DB_PATH = PROJECT_ROOT / "data" / "patient_records.db"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from auth import UserManager


@dataclass(frozen=True)
class ScreeningSeed:
    when: str
    eye: str
    result: str
    confidence: str
    findings: str
    screening_type: str
    follow_up: str
    followup_label: str


@dataclass(frozen=True)
class ProgressionPatient:
    patient_id: str
    name: str
    birthdate: str
    sex: str
    contact: str
    diabetes_type: str
    duration: str
    hba1c: str
    prev_treatment: str
    notes: str
    records: tuple[ScreeningSeed, ...]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def _backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"patient_records_progression_seed_{stamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def _pick_existing(paths: list[str]) -> str:
    for raw in paths:
        if (APP_DIR / raw).exists():
            return raw
    return ""


def _build_patients() -> list[ProgressionPatient]:
    now = datetime.now().replace(microsecond=0)
    return [
        ProgressionPatient(
            patient_id="FU-2026-0101",
            name="Lucia Fernandez",
            birthdate="1967-08-14",
            sex="Female",
            contact="09175550101",
            diabetes_type="Type 2",
            duration="9 years",
            hba1c="6.9",
            prev_treatment="None",
            notes="SEEDED FOLLOW-UP CASE - stable mild DR",
            records=(
                ScreeningSeed(
                    when=(now - timedelta(days=150)).strftime("%Y-%m-%d 09:20:00"),
                    eye="Right Eye",
                    result="Mild DR",
                    confidence="confidence: 89% uncertainty: 11%",
                    findings="Stable mild non-proliferative diabetic retinopathy. Continue routine 4-month review.",
                    screening_type="initial",
                    follow_up="0",
                    followup_label="",
                ),
                ScreeningSeed(
                    when=(now - timedelta(days=18)).strftime("%Y-%m-%d 10:05:00"),
                    eye="Right Eye",
                    result="Mild DR",
                    confidence="confidence: 91% uncertainty: 9%",
                    findings="No meaningful interval change from prior screening. Findings remain stable.",
                    screening_type="follow_up",
                    follow_up="1",
                    followup_label="4-month follow-up",
                ),
            ),
        ),
        ProgressionPatient(
            patient_id="FU-2026-0102",
            name="Ramon Delos Reyes",
            birthdate="1959-03-26",
            sex="Male",
            contact="09175550102",
            diabetes_type="Type 2",
            duration="15 years",
            hba1c="8.4",
            prev_treatment="Focal laser (OD) 2024",
            notes="SEEDED FOLLOW-UP CASE - worsening DR",
            records=(
                ScreeningSeed(
                    when=(now - timedelta(days=132)).strftime("%Y-%m-%d 11:10:00"),
                    eye="Left Eye",
                    result="Moderate DR",
                    confidence="confidence: 86% uncertainty: 14%",
                    findings="Background changes with moderate DR. Return in 3 months.",
                    screening_type="initial",
                    follow_up="0",
                    followup_label="",
                ),
                ScreeningSeed(
                    when=(now - timedelta(days=12)).strftime("%Y-%m-%d 14:40:00"),
                    eye="Left Eye",
                    result="Severe DR",
                    confidence="confidence: 83% uncertainty: 17%",
                    findings="Progression since previous exam with more severe retinopathy; expedite retina referral.",
                    screening_type="follow_up",
                    follow_up="1",
                    followup_label="3-month follow-up",
                ),
            ),
        ),
        ProgressionPatient(
            patient_id="FU-2026-0103",
            name="Marites Alonzo",
            birthdate="1973-01-09",
            sex="Female",
            contact="09175550103",
            diabetes_type="Type 2",
            duration="11 years",
            hba1c="7.6",
            prev_treatment="None",
            notes="SEEDED FOLLOW-UP CASE - slow progression",
            records=(
                ScreeningSeed(
                    when=(now - timedelta(days=180)).strftime("%Y-%m-%d 08:35:00"),
                    eye="Right Eye",
                    result="No DR",
                    confidence="confidence: 94% uncertainty: 6%",
                    findings="No diabetic retinopathy detected on baseline screening.",
                    screening_type="initial",
                    follow_up="0",
                    followup_label="",
                ),
                ScreeningSeed(
                    when=(now - timedelta(days=28)).strftime("%Y-%m-%d 13:25:00"),
                    eye="Right Eye",
                    result="Mild DR",
                    confidence="confidence: 88% uncertainty: 12%",
                    findings="New mild DR changes compared with prior screening; closer follow-up advised.",
                    screening_type="follow_up",
                    follow_up="1",
                    followup_label="6-month follow-up",
                ),
            ),
        ),
    ]


def seed_progression_patients() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")

    backup_path = _backup_db()
    patients = _build_patients()
    image_sources = [
        "stored_images/ES-260403-2UDPE/20260403_012109_right_eye_source.jpg",
        "stored_images/ES-260403-2UDPE/20260403_012700_left_eye_source.jpg",
        "stored_images/ES-260329-W5PWP/20260329_235131_right_eye_source.jpg",
        "stored_images/ES-260329-W5PWP/20260329_230135_left_eye_source.jpg",
    ]
    heatmaps = [
        "stored_images/ES-260403-2UDPE/20260403_012109_right_eye_heatmap.png",
        "stored_images/ES-260403-2UDPE/20260403_012700_left_eye_heatmap.png",
        "stored_images/ES-260329-W5PWP/20260329_235131_right_eye_heatmap.png",
        "stored_images/ES-260329-W5PWP/20260329_230135_left_eye_heatmap.png",
    ]
    src = _pick_existing(image_sources)
    hm = _pick_existing(heatmaps)

    conn = sqlite3.connect(DB_PATH)
    try:
        UserManager._ensure_patient_record_columns(conn)
        cols = _table_columns(conn, "patient_records")
        cur = conn.cursor()

        for patient in patients:
            cur.execute("DELETE FROM patient_records WHERE patient_id = ?", (patient.patient_id,))

        inserted: list[tuple[str, str, str, str]] = []
        for patient in patients:
            previous_id: int | None = None
            age = str(max(0, datetime.now().year - int(patient.birthdate[:4])))
            for record in patient.records:
                payload = {
                    "patient_id": patient.patient_id,
                    "name": patient.name,
                    "birthdate": patient.birthdate,
                    "age": age,
                    "sex": patient.sex,
                    "contact": patient.contact,
                    "eyes": record.eye,
                    "diabetes_type": patient.diabetes_type,
                    "duration": patient.duration,
                    "hba1c": patient.hba1c,
                    "prev_treatment": patient.prev_treatment,
                    "notes": patient.notes,
                    "result": record.result,
                    "confidence": record.confidence,
                    "screened_at": record.when,
                    "doctor_findings": record.findings,
                    "doctor_classification": record.result,
                    "ai_classification": record.result,
                    "final_diagnosis_icdr": record.result,
                    "decision_mode": "accepted",
                    "decision_at": record.when,
                    "screening_type": record.screening_type,
                    "previous_screening_id": previous_id,
                    "follow_up": record.follow_up,
                    "followup_date": record.when.split(" ")[0],
                    "followup_label": record.followup_label,
                    "source_image_path": src,
                    "heatmap_image_path": hm,
                    "image_saved_at": record.when,
                    "original_screener_username": "demo_clinician",
                    "original_screener_name": "Demo Clinician",
                }
                usable = {key: value for key, value in payload.items() if key in cols}
                columns = ", ".join(usable.keys())
                placeholders = ", ".join(["?"] * len(usable))
                cur.execute(
                    f"INSERT INTO patient_records ({columns}) VALUES ({placeholders})",
                    list(usable.values()),
                )
                previous_id = int(cur.lastrowid)
                inserted.append((patient.patient_id, patient.name, record.result, record.when))

        conn.commit()
    finally:
        conn.close()

    print(f"Backup created: {backup_path}")
    print(f"Inserted {len(inserted)} follow-up history rows:")
    for patient_id, name, result, when in inserted:
        print(f"- {patient_id} | {name} | {result} | {when}")


if __name__ == "__main__":
    seed_progression_patients()
