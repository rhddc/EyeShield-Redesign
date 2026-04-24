from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
USERS_DB = PROJECT_ROOT / "data" / "users.db"
PATIENT_RECORDS_DB = PROJECT_ROOT / "data" / "patient_records.db"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import emr_service as emr
from auth import UserManager


@dataclass(frozen=True)
class FlowCase:
    first_name: str
    last_name: str
    dob: str
    sex: str
    contact: str
    address: str
    height_cm: float
    weight_kg: float
    diabetes_type: str
    dm_duration_years: float
    hba1c: float
    medications: str
    conditions: str
    previous_eye_treatment: str
    note: str
    history_seed: bool = False


CASES = [
    FlowCase(
        first_name="Elena",
        last_name="Navarro",
        dob="1968-04-15",
        sex="Female",
        contact="09171234561",
        address="Bacolod City, Negros Occidental",
        height_cm=157.0,
        weight_kg=69.5,
        diabetes_type="Type 2",
        dm_duration_years=11,
        hba1c=7.4,
        medications="Metformin 500 mg BID, Losartan 50 mg OD",
        conditions="Hypertension",
        previous_eye_treatment="None",
        note="FLOW TEST - single-eye save path",
        history_seed=False,
    ),
    FlowCase(
        first_name="Miguel",
        last_name="Santos",
        dob="1971-09-03",
        sex="Male",
        contact="09181234562",
        address="Iloilo City, Iloilo",
        height_cm=171.0,
        weight_kg=82.0,
        diabetes_type="Type 2",
        dm_duration_years=14,
        hba1c=8.1,
        medications="Metformin 1 g BID, Gliclazide 60 mg OD",
        conditions="Hypertension, dyslipidemia",
        previous_eye_treatment="Cataract surgery (OD) 2021",
        note="FLOW TEST - bilateral path",
        history_seed=False,
    ),
    FlowCase(
        first_name="Grace",
        last_name="Villanueva",
        dob="1980-01-22",
        sex="Female",
        contact="09191234563",
        address="Kabankalan City, Negros Occidental",
        height_cm=160.0,
        weight_kg=73.0,
        diabetes_type="Type 2",
        dm_duration_years=7,
        hba1c=7.0,
        medications="Metformin 850 mg BID",
        conditions="Obesity",
        previous_eye_treatment="None",
        note="FLOW TEST - follow-up history case",
        history_seed=True,
    ),
    FlowCase(
        first_name="Paolo",
        last_name="Mendoza",
        dob="1963-11-29",
        sex="Male",
        contact="09201234564",
        address="Talisay City, Cebu",
        height_cm=168.0,
        weight_kg=78.0,
        diabetes_type="Type 2",
        dm_duration_years=18,
        hba1c=8.8,
        medications="Insulin glargine HS, Metformin 500 mg BID",
        conditions="Chronic kidney disease stage 2",
        previous_eye_treatment="Laser photocoagulation (OS) 2023",
        note="FLOW TEST - follow-up high-risk case",
        history_seed=True,
    ),
]

SOURCE_IMAGES = [
    "stored_images/ES-260403-2UDPE/20260403_012109_right_eye_source.jpg",
    "stored_images/ES-260403-2UDPE/20260403_012700_left_eye_source.jpg",
    "stored_images/ES-260329-W5PWP/20260329_230135_left_eye_source.jpg",
    "stored_images/ES-260329-W5PWP/20260329_235131_right_eye_source.jpg",
]

HEATMAP_IMAGES = [
    "stored_images/ES-260403-2UDPE/20260403_012109_right_eye_heatmap.png",
    "stored_images/ES-260403-2UDPE/20260403_012700_left_eye_heatmap.png",
    "stored_images/ES-260329-W5PWP/20260329_230135_left_eye_heatmap.png",
    "stored_images/ES-260329-W5PWP/20260329_235131_right_eye_heatmap.png",
]


def _pick_existing(paths: list[str]) -> str:
    for raw in paths:
        if (APP_DIR / raw).exists():
            return raw
    return ""


def _choose_actor_ids() -> tuple[int, int]:
    conn = sqlite3.connect(USERS_DB)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM users WHERE lower(role) = 'frontdesk' ORDER BY id LIMIT 1"
        )
        row = cur.fetchone()
        frontdesk_id = int(row[0]) if row else 1
        cur.execute(
            "SELECT id FROM users WHERE lower(role) = 'clinician' ORDER BY id LIMIT 1"
        )
        row = cur.fetchone()
        clinician_id = int(row[0]) if row else frontdesk_id
        return frontdesk_id, clinician_id
    finally:
        conn.close()


def _cleanup_previous_cases(case_names: list[tuple[str, str]]) -> None:
    patient_record_cols: set[str] = set()
    if PATIENT_RECORDS_DB.exists():
        pr = sqlite3.connect(PATIENT_RECORDS_DB)
        try:
            patient_record_cols = _table_columns(pr, "patient_records")
        finally:
            pr.close()

    conn = sqlite3.connect(USERS_DB)
    try:
        cur = conn.cursor()
        for first_name, last_name in case_names:
            cur.execute(
                "SELECT patient_id, patient_code FROM emr_patients WHERE first_name = ? AND last_name = ?",
                (first_name, last_name),
            )
            rows = cur.fetchall()
            for patient_id, patient_code in rows:
                cur.execute("DELETE FROM emr_queue_entries WHERE patient_id = ?", (patient_id,))
                cur.execute("DELETE FROM emr_screening_eyes WHERE screening_id IN (SELECT screening_id FROM emr_screenings WHERE patient_id = ?)", (patient_id,))
                cur.execute("DELETE FROM emr_screenings WHERE patient_id = ?", (patient_id,))
                cur.execute("DELETE FROM emr_patients WHERE patient_id = ?", (patient_id,))
                if patient_code and patient_record_cols:
                    pr = sqlite3.connect(PATIENT_RECORDS_DB)
                    try:
                        pr.execute("DELETE FROM patient_records WHERE patient_id = ?", (patient_code,))
                        pr.commit()
                    finally:
                        pr.close()
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {str(r[1]) for r in cur.fetchall()}


def _seed_history_rows(patient_code: str, case: FlowCase) -> None:
    if not PATIENT_RECORDS_DB.exists():
        PATIENT_RECORDS_DB.parent.mkdir(parents=True, exist_ok=True)

    src = _pick_existing(SOURCE_IMAGES)
    hm = _pick_existing(HEATMAP_IMAGES)
    if not src:
        return

    rows = [
        {
            "name": f"{case.first_name} {case.last_name}",
            "birthdate": case.dob,
            "sex": case.sex,
            "contact": case.contact,
            "eyes": "Right Eye",
            "result": "Mild DR" if "Grace" in case.first_name else "Moderate DR",
            "confidence": "confidence: 88% uncertainty: 12%",
            "screened_at": (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S"),
            "doctor_findings": "Prior screening seeded for diagnosis-flow test case.",
            "screening_type": "initial",
        },
        {
            "name": f"{case.first_name} {case.last_name}",
            "birthdate": case.dob,
            "sex": case.sex,
            "contact": case.contact,
            "eyes": "Left Eye",
            "result": "Moderate DR" if "Grace" in case.first_name else "Severe DR",
            "confidence": "confidence: 84% uncertainty: 16%",
            "screened_at": (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S"),
            "doctor_findings": "Follow-up screening seeded for diagnosis-flow test case.",
            "screening_type": "follow_up",
        },
    ]

    conn = sqlite3.connect(PATIENT_RECORDS_DB)
    try:
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
        UserManager._ensure_patient_record_columns(conn)
        conn.commit()
        cols = _table_columns(conn, "patient_records")
        if not cols:
            return
        prev_id = None
        for row in rows:
            payload = {
                "patient_id": patient_code,
                "name": row["name"],
                "birthdate": row["birthdate"],
                "age": str(max(0, date.today().year - int(case.dob[:4]))),
                "sex": row["sex"],
                "contact": row["contact"],
                "eyes": row["eyes"],
                "diabetes_type": case.diabetes_type,
                "duration": f"{int(case.dm_duration_years)} years",
                "hba1c": f"{case.hba1c:.1f}",
                "prev_treatment": case.previous_eye_treatment,
                "notes": case.note,
                "result": row["result"],
                "confidence": row["confidence"],
                "screened_at": row["screened_at"],
                "source_image_path": src,
                "heatmap_image_path": hm,
                "doctor_findings": row["doctor_findings"],
                "doctor_classification": row["result"],
                "ai_classification": row["result"],
                "final_diagnosis_icdr": row["result"],
                "decision_mode": "accepted",
                "screening_type": row["screening_type"],
                "previous_screening_id": prev_id,
                "follow_up": "1" if row["screening_type"] == "follow_up" else "0",
                "original_screener_username": "Kelcy",
                "original_screener_name": "Kelcy",
            }
            usable = {k: v for k, v in payload.items() if k in cols}
            if not usable:
                continue
            columns = ", ".join(usable.keys())
            placeholders = ", ".join(["?"] * len(usable))
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO patient_records ({columns}) VALUES ({placeholders})",
                list(usable.values()),
            )
            prev_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()


def seed_cases() -> None:
    frontdesk_id, _ = _choose_actor_ids()
    _cleanup_previous_cases([(c.first_name, c.last_name) for c in CASES])

    created: list[tuple[str, str, int, str]] = []
    for case in CASES:
        patient_id = emr.create_patient(
            created_by=frontdesk_id,
            last_name=case.last_name,
            first_name=case.first_name,
            date_of_birth=case.dob,
            sex=case.sex,
            contact_number=case.contact,
            address=case.address,
            height_cm=case.height_cm,
            weight_kg=case.weight_kg,
            diabetes_type=case.diabetes_type,
            dm_duration_years=case.dm_duration_years,
            hba1c=case.hba1c,
            current_medications=case.medications,
            other_conditions=case.conditions,
            previous_eye_treatment=case.previous_eye_treatment,
            last_eye_exam_date=(date.today() - timedelta(days=180)).isoformat(),
        )
        queue_id = emr.assign_queue_entry(patient_id, frontdesk_id, notes=case.note)
        patient = emr.get_patient(patient_id) or {}
        patient_code = str(patient.get("patient_code") or "")
        if case.history_seed and patient_code:
            _seed_history_rows(patient_code, case)
        created.append((patient_code, f"{case.first_name} {case.last_name}", queue_id, case.note))

    print("Seeded diagnosis-flow queue cases:")
    for patient_code, full_name, queue_id, note in created:
        print(f"- {patient_code} | queue_id={queue_id} | {full_name} | {note}")


if __name__ == "__main__":
    seed_cases()
