"""
EyeShield EMR data access — patients, queue, screenings (emr_* tables in users.db).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any, Optional

import numpy as np
from PIL import Image

try:
    from .auth import UserManager, get_connection
    from .activity_logger import log_action as log_activity
except Exception:  # pragma: no cover
    from auth import UserManager, get_connection
    from activity_logger import log_action as log_activity

# -------------------- M5 configurable thresholds (named constants; env can override) --------------------
BLUR_THRESHOLD = float(os.environ.get("EYESHIELD_BLUR_THRESHOLD", os.environ.get("EYESHIELD_QUALITY_BLUR_MIN", "100.0")))
ILLUMINATION_MIN = float(os.environ.get("EYESHIELD_ILLUMINATION_MIN", os.environ.get("EYESHIELD_QUALITY_ILLUM_MIN", "40.0")))
ILLUMINATION_MAX = float(os.environ.get("EYESHIELD_ILLUMINATION_MAX", os.environ.get("EYESHIELD_QUALITY_ILLUM_MAX", "220.0")))
ENTROPY_THRESHOLD = float(os.environ.get("EYESHIELD_ENTROPY_THRESHOLD", os.environ.get("EYESHIELD_QUALITY_ENTROPY_MIN", "4.5")))
UNCERTAINTY_THRESHOLD = float(
    os.environ.get("EYESHIELD_UNCERTAINTY_THRESHOLD", os.environ.get("EYESHIELD_UNCERTAINTY_REJECT_THRESHOLD", "0.25"))
)
# Legacy aliases
QUALITY_BLUR_MIN = BLUR_THRESHOLD
QUALITY_ILLUM_MIN = ILLUMINATION_MIN
QUALITY_ILLUM_MAX = ILLUMINATION_MAX
QUALITY_ENTROPY_MIN = ENTROPY_THRESHOLD
UNCERTAINTY_REJECT_THRESHOLD = UNCERTAINTY_THRESHOLD

AI_TREATMENT_BY_GRADE = {
    0: "No DR detected. Recommend annual screening.",
    1: "Mild NPDR detected. Optimize glycemic and blood pressure control. Rescreen in 12 months.",
    2: "Moderate NPDR detected. Refer to ophthalmologist. Rescreen in 6 months.",
    3: "Severe NPDR detected. Urgent ophthalmology referral required. Do not delay.",
    4: "Proliferative DR detected. Immediate ophthalmology referral required.",
}


def _open_conn() -> sqlite3.Connection:
    conn = get_connection()
    UserManager._ensure_emr_schema(conn)
    return conn


def _role_for_user_id(user_id: Optional[int]) -> str:
    if not user_id:
        return ""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT role, is_active FROM users WHERE id = ?", (int(user_id),))
        row = cur.fetchone()
        if not row:
            return ""
        if int(row[1] or 0) != 1:
            return ""
        return str(row[0] or "").strip().lower()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


def _is_allowed(user_id: Optional[int], allowed: set[str]) -> bool:
    role = _role_for_user_id(user_id)
    return bool(role and role in {r.lower() for r in allowed})


def get_user_id(username: str) -> Optional[int]:
    u = (username or "").strip()
    if not u:
        return None
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE lower(username) = lower(?)", (u,))
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def get_user_label(user_id: Optional[int]) -> tuple[str, str]:
    """
    Return (username, display_label) for a user id.

    display_label prefers display_name, then full_name, then username.
    """
    if not user_id:
        return "", ""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT username, display_name, full_name, is_active FROM users WHERE id = ?",
            (int(user_id),),
        )
        row = cur.fetchone()
        if not row:
            return "", ""
        if int(row[3] or 0) != 1:
            return "", ""
        username = str(row[0] or "").strip()
        display = str(row[1] or "").strip() or str(row[2] or "").strip() or username
        return username, display
    except sqlite3.Error:
        return "", ""
    finally:
        conn.close()


def log_emr_action(
    user_id: Optional[int],
    action: str,
    target_type: str = "",
    target_id: Optional[int] = None,
    detail: str = "",
    ip_address: Optional[str] = None,
) -> None:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(emr_action_logs)")
        cols = {row[1] for row in cur.fetchall()}
        if "ip_address" in cols:
            cur.execute(
                """
                INSERT INTO emr_action_logs (user_id, action, target_type, target_id, detail, ip_address)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, action, target_type or None, target_id, detail or None, ip_address or "local"),
            )
        else:
            cur.execute(
                """
                INSERT INTO emr_action_logs (user_id, action, target_type, target_id, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, action, target_type or None, target_id, detail or None),
            )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    # Mirror to canonical audit trail (auth.activity_logs) best-effort.
    try:
        log_activity(
            user_id=user_id,
            action=str(action),
            target_type=str(target_type or ""),
            target_id=target_id,
            detail={"detail": detail, "ip": ip_address or "local"} if detail or ip_address else {},
            event_type="EMR",
        )
    except Exception:
        pass


def next_patient_code() -> str:
    year = date.today().year
    prefix = f"EYS-{year}-"
    conn = _open_conn()
    try:
        cur = conn.cursor()
        # IMPORTANT: Do not use COUNT(*) here.
        # If rows were deleted (gaps), COUNT+1 can collide with an existing later code.
        # Use MAX(existing numeric suffix)+1 instead.
        start_idx = len(prefix) + 1  # SQLite substr is 1-indexed
        cur.execute(
            """
            SELECT MAX(CAST(SUBSTR(patient_code, ?) AS INTEGER))
            FROM emr_patients
            WHERE patient_code LIKE ?
            """,
            (start_idx, f"{prefix}%"),
        )
        row = cur.fetchone()
        max_suffix = int(row[0] or 0)
        n = max_suffix + 1
        return f"{prefix}{n:05d}"
    finally:
        conn.close()


def _next_patient_code_in_tx(cur: sqlite3.Cursor, *, year: Optional[int] = None) -> str:
    """
    Generate the next patient_code inside an existing transaction.

    This avoids races and avoids COUNT(*) collisions when there are gaps.
    """
    y = int(year or date.today().year)
    prefix = f"EYS-{y}-"
    start_idx = len(prefix) + 1  # SQLite substr is 1-indexed
    cur.execute(
        """
        SELECT MAX(CAST(SUBSTR(patient_code, ?) AS INTEGER))
        FROM emr_patients
        WHERE patient_code LIKE ?
        """,
        (start_idx, f"{prefix}%"),
    )
    row = cur.fetchone()
    max_suffix = int(row[0] or 0)
    return f"{prefix}{(max_suffix + 1):05d}"


def find_duplicate_patient(first_name: str, last_name: str, date_of_birth: str) -> Optional[dict[str, Any]]:
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    dob = (date_of_birth or "").strip()[:10]
    if not fn or not ln or not dob:
        return None
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT patient_id, patient_code, first_name, last_name, date_of_birth
            FROM emr_patients
            WHERE lower(trim(first_name)) = lower(trim(?)) AND lower(trim(last_name)) = lower(trim(?)) AND date_of_birth = ?
            LIMIT 1
            """,
            (fn, ln, dob),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "patient_id": row[0],
            "patient_code": row[1],
            "first_name": row[2],
            "last_name": row[3],
            "date_of_birth": row[4],
        }
    finally:
        conn.close()


def find_patient_by_name_dob(first_name: str, last_name: str, date_of_birth: str) -> Optional[dict[str, Any]]:
    """
    Resolve an existing patient using the project's chosen identity key: (first_name, last_name, date_of_birth).

    This intentionally matches the same predicate as `find_duplicate_patient`, but returns the full patient row
    so the caller can reuse/update it without creating duplicates.
    """
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    dob = (date_of_birth or "").strip()[:10]
    if not fn or not ln or not dob:
        return None
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM emr_patients
            WHERE lower(trim(first_name)) = lower(trim(?))
              AND lower(trim(last_name)) = lower(trim(?))
              AND date_of_birth = ?
            ORDER BY patient_id ASC
            LIMIT 1
            """,
            (fn, ln, dob),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def upsert_patient_by_name_dob(
    acting_user_id: int,
    *,
    last_name: str,
    first_name: str,
    date_of_birth: str,
    middle_name: str = "",
    sex: str = "",
    contact_number: str = "",
    email: str = "",
    address: str = "",
    height_cm: Optional[float] = None,
    weight_kg: Optional[float] = None,
    diabetes_type: str = "",
    dm_duration_years: Optional[float] = None,
    hba1c: Optional[float] = None,
    current_medications: str = "",
    known_allergies: str = "",
    other_conditions: str = "",
    current_eye_treatment: str = "",
    previous_eye_treatment: str = "",
    last_eye_exam_date: str = "",
) -> tuple[int, bool]:
    """
    Upsert patient using (first_name,last_name,dob) as identity.

    Returns (patient_id, created_new).
    """
    existing = find_patient_by_name_dob(first_name, last_name, date_of_birth)
    if not existing:
        pid = create_patient(
            acting_user_id,
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name,
            date_of_birth=date_of_birth,
            sex=sex,
            contact_number=contact_number,
            email=email,
            address=address,
            height_cm=height_cm,
            weight_kg=weight_kg,
            diabetes_type=diabetes_type,
            dm_duration_years=dm_duration_years,
            hba1c=hba1c,
            current_medications=current_medications,
            known_allergies=known_allergies,
            other_conditions=other_conditions,
            current_eye_treatment=current_eye_treatment,
            previous_eye_treatment=previous_eye_treatment,
            last_eye_exam_date=last_eye_exam_date,
        )
        return pid, True

    pid = int(existing["patient_id"])
    fields: dict[str, Any] = {
        # Keep identity fields consistent too (normalise casing/spacing through the UI).
        "last_name": (last_name or "").strip(),
        "first_name": (first_name or "").strip(),
        "middle_name": (middle_name or "").strip() or None,
        "date_of_birth": (date_of_birth or "").strip()[:10],
        "sex": (sex or "").strip() or None,
        "contact_number": (contact_number or "").strip() or None,
        "email": (email or "").strip() or None,
        "address": (address or "").strip() or None,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "diabetes_type": (diabetes_type or "").strip() or None,
        "dm_duration_years": dm_duration_years,
        "hba1c": hba1c,
        "current_medications": (current_medications or "").strip() or None,
        "known_allergies": (known_allergies or "").strip() or None,
        "other_conditions": (other_conditions or "").strip() or None,
        "current_eye_treatment": (current_eye_treatment or "").strip() or None,
        "previous_eye_treatment": (previous_eye_treatment or "").strip() or None,
        "last_eye_exam_date": (last_eye_exam_date or "").strip() or None,
    }
    # Best-effort update; if nothing changes, it's fine.
    update_patient_fields(pid, fields, acting_user_id, action="UPSERT_PATIENT_IDENTITY", target_type="patient")
    return pid, False


def ensure_visit_details_row(queue_id: int, patient_id: int, captured_by: Optional[int]) -> bool:
    """
    Ensure a visit has a persisted encounter row even before diagnosis/images.
    """
    qid = int(queue_id)
    pid = int(patient_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT visit_detail_id FROM emr_visit_details WHERE queue_id = ? LIMIT 1", (qid,))
        existing = cur.fetchone()
        if existing:
            return True
        cur.execute(
            "INSERT INTO emr_visit_details (queue_id, patient_id, captured_by) VALUES (?, ?, ?)",
            (qid, pid, captured_by),
        )
        conn.commit()
        log_emr_action(captured_by, "CREATE_VISIT_DETAILS_STUB", "queue_entries", qid, "")
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def ensure_legacy_patient_record_stub(
    *,
    queue_id: int,
    patient_id: int,
    captured_by: Optional[int],
    screening_purpose: str = "new",
) -> bool:
    """
    Ensure the "Patient Records" (legacy `data/patient_records.db`) has a row for this visit
    even before any fundus image exists.

    The UI for Patient Records (Reports/Dashboard) is backed by `patient_records.db`, so this
    creates a minimal "pending" record that can later be superseded by the clinician screening
    entries.
    """
    qid = int(queue_id)
    pid = int(patient_id)
    purpose = str(screening_purpose or "new").strip().lower()
    stype = "follow_up" if purpose == "follow_up" else "initial"
    group_id = f"queue-{qid}"

    # Import lazily to avoid hard coupling / circular imports at module load.
    try:
        from db import get_records_conn, ensure_patient_records_db_schema
    except Exception:  # pragma: no cover
        from .db import get_records_conn, ensure_patient_records_db_schema

    # Source demographics from EMR patient table.
    patient = get_patient(pid) or {}
    if not patient:
        return False

    name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
    patient_code = str(patient.get("patient_code") or "").strip()
    birthdate = str(patient.get("date_of_birth") or "").strip()[:10]
    age = str(patient.get("age") or "").strip()
    sex = str(patient.get("sex") or "").strip()
    contact = str(patient.get("contact_number") or "").strip()
    phone = str(patient.get("contact_number") or "").strip()
    address = str(patient.get("address") or "").strip()
    diabetes_type = str(patient.get("diabetes_type") or "").strip()
    duration = str(patient.get("dm_duration_years") or "").strip()
    hba1c = str(patient.get("hba1c") or "").strip()

    conn = None
    try:
        conn = get_records_conn()
        ensure_patient_records_db_schema(conn)
        cur = conn.cursor()
        # Idempotency: if this queue encounter already has a legacy stub, don't insert again.
        cur.execute(
            "SELECT id FROM patient_records WHERE archived_at IS NULL AND screening_group_id = ? LIMIT 1",
            (group_id,),
        )
        if cur.fetchone():
            return True

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        follow_up_flag = "Yes" if stype == "follow_up" else ""
        followup_date = now if stype == "follow_up" else ""
        followup_label = "Follow-up screening" if stype == "follow_up" else ""

        cur.execute(
            """
            INSERT INTO patient_records (
                patient_id, name, birthdate, age, sex, contact, phone, address, eyes,
                diabetes_type, duration, hba1c, prev_treatment, notes,
                result, confidence, screened_at,
                follow_up, followup_date, followup_label, screening_type, previous_screening_id, screening_group_id,
                decision_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_code,
                name,
                birthdate,
                age,
                sex,
                contact,
                phone,
                address,
                "",  # eyes unknown at intake
                diabetes_type,
                duration,
                hba1c,
                "",  # prev_treatment
                "",  # notes
                "",  # result => Pending
                "",  # confidence
                now,
                follow_up_flag,
                followup_date,
                followup_label,
                stype,
                None,
                group_id,
                "pending",
            ),
        )
        conn.commit()
        # Best-effort audit in EMR action logs.
        log_emr_action(captured_by, "CREATE_LEGACY_VISIT_STUB", "patient_records", None, group_id)
        return True
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            if conn is not None:
                conn.close()


def _normalize_records_asset_path(path_value: str) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    raw = os.path.normpath(raw)
    if not os.path.isabs(raw):
        return raw.replace("\\", "/")
    try:
        app_root = Path(__file__).resolve().parent
        rel = os.path.relpath(raw, str(app_root))
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except Exception:
        pass
    return raw.replace("\\", "/")


def upsert_legacy_patient_record_for_queue_eye(
    *,
    queue_id: int,
    patient_id: int,
    captured_by: Optional[int],
    eye_label: str,
    screening_type: str,
    screened_at: str,
    source_image_path: str,
    heatmap_image_path: str,
    ai_classification: str,
    doctor_classification: str,
    decision_mode: str,
    override_justification: str,
    final_diagnosis_icdr: str,
    doctor_findings: str,
    confidence: str = "",
) -> bool:
    """
    Update (or insert) the legacy `patient_records` row(s) for an EMR queue visit so
    Patient Records/Reports show the saved fundus image + heatmap + results under the
    same visit group (`screening_group_id = queue-{queue_id}`).

    - First eye save: reuse the existing stub row when possible (eyes is blank).
    - Second eye save: insert a second row under the same group id.
    """
    qid = int(queue_id)
    pid = int(patient_id)
    group_id = f"queue-{qid}"
    eye = str(eye_label or "").strip() or ""
    stype = "follow_up" if str(screening_type or "").strip().lower() == "follow_up" else "initial"
    cap_uid = int(captured_by) if captured_by is not None and str(captured_by).strip() != "" else None
    cap_username, cap_label = get_user_label(cap_uid)

    # Import lazily to avoid hard coupling / circular imports at module load.
    try:
        from db import get_records_conn, ensure_patient_records_db_schema
    except Exception:  # pragma: no cover
        from .db import get_records_conn, ensure_patient_records_db_schema

    patient = get_patient(pid) or {}
    if not patient:
        return False
    name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
    patient_code = str(patient.get("patient_code") or "").strip()
    birthdate = str(patient.get("date_of_birth") or "").strip()[:10]
    age = str(patient.get("age") or "").strip()
    sex = str(patient.get("sex") or "").strip()
    contact = str(patient.get("contact_number") or "").strip()
    phone = str(patient.get("contact_number") or "").strip()
    address = str(patient.get("address") or "").strip()
    diabetes_type = str(patient.get("diabetes_type") or "").strip()
    duration = str(patient.get("dm_duration_years") or "").strip()
    hba1c = str(patient.get("hba1c") or "").strip()

    src_path = _normalize_records_asset_path(source_image_path)
    hm_path = _normalize_records_asset_path(heatmap_image_path)
    ts = str(screened_at or "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    follow_up_flag = "Yes" if stype == "follow_up" else ""
    followup_date = ts if stype == "follow_up" else ""
    followup_label = "Follow-up screening" if stype == "follow_up" else ""

    conn = None
    try:
        conn = get_records_conn()
        ensure_patient_records_db_schema(conn)
        cur = conn.cursor()

        # If an eye-specific row already exists under this queue group, update it.
        cur.execute(
            """
            SELECT id, eyes
            FROM patient_records
            WHERE archived_at IS NULL
              AND screening_group_id = ?
              AND lower(eyes) = lower(?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (group_id, eye),
        )
        row = cur.fetchone()
        target_id = int(row[0]) if row else 0

        # Otherwise, reuse the stub row (eyes blank) if it exists.
        if not target_id:
            cur.execute(
                """
                SELECT id
                FROM patient_records
                WHERE archived_at IS NULL
                  AND screening_group_id = ?
                  AND (eyes IS NULL OR trim(eyes) = '')
                ORDER BY id ASC
                LIMIT 1
                """,
                (group_id,),
            )
            stub = cur.fetchone()
            target_id = int(stub[0]) if stub else 0

        if target_id:
            cur.execute(
                """
                UPDATE patient_records SET
                    patient_id = ?, name = ?, birthdate = ?, age = ?, sex = ?, contact = ?, phone = ?, address = ?, eyes = ?,
                    diabetes_type = ?, duration = ?, hba1c = ?,
                    result = ?, confidence = ?, screened_at = ?,
                    ai_classification = ?, doctor_classification = ?, decision_mode = ?, override_justification = ?,
                    final_diagnosis_icdr = ?, doctor_findings = ?, decision_by_username = ?, decision_at = ?,
                    source_image_path = ?, heatmap_image_path = ?,
                    follow_up = ?, followup_date = ?, followup_label = ?, screening_type = ?,
                    screening_group_id = ?,
                    original_screener_username = COALESCE(NULLIF(original_screener_username, ''), ?),
                    original_screener_name = COALESCE(NULLIF(original_screener_name, ''), ?)
                WHERE id = ?
                """,
                (
                    patient_code,
                    name,
                    birthdate,
                    age,
                    sex,
                    contact,
                    phone,
                    address,
                    eye,
                    diabetes_type,
                    duration,
                    hba1c,
                    final_diagnosis_icdr or doctor_classification or ai_classification,
                    confidence,
                    ts,
                    ai_classification,
                    doctor_classification,
                    decision_mode,
                    override_justification,
                    final_diagnosis_icdr,
                    doctor_findings,
                    "emr",
                    ts,
                    src_path,
                    hm_path,
                    follow_up_flag,
                    followup_date,
                    followup_label,
                    stype,
                    group_id,
                    cap_username or "emr",
                    cap_label or "EMR",
                    int(target_id),
                ),
            )
            conn.commit()
            return True

        # No stub exists (unexpected): insert a fresh row under the queue group.
        cur.execute(
            """
            INSERT INTO patient_records (
                patient_id, name, birthdate, age, sex, contact, phone, address, eyes,
                diabetes_type, duration, hba1c, prev_treatment, notes,
                result, confidence, screened_at,
                ai_classification, doctor_classification, decision_mode, override_justification,
                final_diagnosis_icdr, doctor_findings, decision_by_username, decision_at,
                source_image_path, heatmap_image_path,
                follow_up, followup_date, followup_label, screening_type, previous_screening_id, screening_group_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                patient_code,
                name,
                birthdate,
                age,
                sex,
                contact,
                phone,
                address,
                eye,
                diabetes_type,
                duration,
                hba1c,
                "",
                "",
                final_diagnosis_icdr or doctor_classification or ai_classification,
                confidence,
                ts,
                ai_classification,
                doctor_classification,
                decision_mode,
                override_justification,
                final_diagnosis_icdr,
                doctor_findings,
                "emr",
                ts,
                src_path,
                hm_path,
                follow_up_flag,
                followup_date,
                followup_label,
                stype,
                None,
                group_id,
            ),
        )
        conn.commit()
        # Best-effort backfill of screener identity (legacy schema field names).
        if cap_username or cap_label:
            with contextlib.suppress(Exception):
                cur.execute(
                    """
                    UPDATE patient_records
                    SET original_screener_username = COALESCE(NULLIF(original_screener_username, ''), ?),
                        original_screener_name = COALESCE(NULLIF(original_screener_name, ''), ?)
                    WHERE id = ?
                    """,
                    (cap_username or "emr", cap_label or "EMR", int(cur.lastrowid)),
                )
                conn.commit()
        return True
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            if conn is not None:
                conn.close()
def get_today_active_queue_for_patient(patient_id: int) -> Optional[dict[str, Any]]:
    """Active = visit_date today and status in (waiting, in_progress)."""
    vd = date.today().isoformat()
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT queue_id, patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes
            FROM emr_queue_entries
            WHERE patient_id = ? AND visit_date = ? AND status IN ('waiting', 'in_progress')
            ORDER BY queue_id DESC
            LIMIT 1
            """,
            (patient_id, vd),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = ["queue_id", "patient_id", "queue_number", "visit_date", "status", "assigned_by", "screening_purpose", "notes"]
        return dict(zip(cols, row))
    finally:
        conn.close()


def get_latest_queue_for_patient(patient_id: int) -> Optional[dict[str, Any]]:
    """Return most recent queue row for patient (any date/status)."""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT queue_id, patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes
            FROM emr_queue_entries
            WHERE patient_id = ?
            ORDER BY queue_id DESC
            LIMIT 1
            """,
            (int(patient_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = ["queue_id", "patient_id", "queue_number", "visit_date", "status", "assigned_by", "screening_purpose", "notes"]
        return dict(zip(cols, row))
    finally:
        conn.close()


def get_latest_diabetes_diagnosis_date(patient_id: int) -> str:
    """Return the most recent non-empty diabetes diagnosis date for a patient (from visit details)."""
    pid = int(patient_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT d.diabetes_diagnosis_date
            FROM emr_visit_details d
            JOIN emr_queue_entries q ON q.queue_id = d.queue_id
            WHERE q.patient_id = ?
              AND d.diabetes_diagnosis_date IS NOT NULL
              AND trim(d.diabetes_diagnosis_date) != ''
            ORDER BY d.visit_detail_id DESC
            LIMIT 1
            """,
            (pid,),
        )
        row = cur.fetchone()
        return str(row[0] or "").strip() if row else ""
    finally:
        conn.close()


def log_open_patient_record(user_id: Optional[int], queue_id: int, patient_id: int) -> None:
    log_emr_action(
        user_id,
        "OPEN_PATIENT_RECORD",
        "queue_entries",
        queue_id,
        json.dumps({"patient_id": patient_id}),
    )


def _next_queue_seq(visit_date: str) -> int:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM emr_queue_entries WHERE visit_date = ? AND status IN ('waiting', 'in_progress')",
            (visit_date,),
        )
        row = cur.fetchone()
        return int(row[0] or 0) + 1
    finally:
        conn.close()


def next_queue_label(visit_date: Optional[str] = None) -> str:
    vd = visit_date or date.today().isoformat()
    n = _next_queue_seq(vd)
    return f"Q-{n:03d}"


def create_patient(
    created_by: int,
    last_name: str,
    first_name: str,
    date_of_birth: str,
    patient_code: str = "",
    sex: str = "",
    contact_number: str = "",
    email: str = "",
    address: str = "",
    middle_name: str = "",
    height_cm: Optional[float] = None,
    weight_kg: Optional[float] = None,
    diabetes_type: str = "",
    dm_duration_years: Optional[float] = None,
    hba1c: Optional[float] = None,
    current_medications: str = "",
    known_allergies: str = "",
    other_conditions: str = "",
    current_eye_treatment: str = "",
    previous_eye_treatment: str = "",
    last_eye_exam_date: str = "",
) -> int:
    code = (patient_code or "").strip() or next_patient_code()
    last_name = (last_name or "").strip()
    first_name = (first_name or "").strip()
    if not last_name or not first_name:
        raise ValueError("last_name and first_name are required")
    dob = (date_of_birth or "").strip()
    if not dob:
        raise ValueError("date_of_birth is required")

    age = None
    try:
        born = datetime.strptime(dob[:10], "%Y-%m-%d").date()
        today = date.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    except ValueError:
        pass

    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO emr_patients (
                patient_code, last_name, first_name, middle_name, date_of_birth, age, sex,
                contact_number, email, address, height_cm, weight_kg,
                diabetes_type, dm_duration_years, hba1c,
                current_medications, known_allergies, other_conditions,
                current_eye_treatment, previous_eye_treatment, last_eye_exam_date,
                created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                last_name,
                first_name,
                (middle_name or "").strip() or None,
                dob,
                age,
                (sex or "").strip() or None,
                (contact_number or "").strip() or None,
                (email or "").strip() or None,
                (address or "").strip() or None,
                height_cm,
                weight_kg,
                (diabetes_type or "").strip() or None,
                dm_duration_years,
                hba1c,
                (current_medications or "").strip() or None,
                (known_allergies or "").strip() or None,
                (other_conditions or "").strip() or None,
                (current_eye_treatment or "").strip() or None,
                (previous_eye_treatment or "").strip() or None,
                (last_eye_exam_date or "").strip() or None,
                created_by,
            ),
        )
        pid = int(cur.lastrowid)
        conn.commit()
        log_emr_action(created_by, "CREATE_PATIENT", "patient", pid, json.dumps({"patient_code": code}))
        return pid
    finally:
        conn.close()


def get_patient(patient_id: int) -> Optional[dict[str, Any]]:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM emr_patients WHERE patient_id = ?", (patient_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def get_patient_by_code(patient_code: str) -> Optional[dict[str, Any]]:
    code = str(patient_code or "").strip()
    if not code:
        return None
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM emr_patients WHERE patient_code = ?", (code,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def search_patients(query: str, limit: int = 50) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if len(q) < 1:
        return []
    like = f"%{q}%"
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT patient_id, patient_code, last_name, first_name, date_of_birth, contact_number
            FROM emr_patients
            WHERE patient_code LIKE ? OR last_name LIKE ? OR first_name LIKE ?
                OR (ifnull(first_name,'') || ' ' || ifnull(last_name,'')) LIKE ?
            ORDER BY last_name, first_name
            LIMIT ?
            """,
            (like, like, like, like, limit),
        )
        cols = ["patient_id", "patient_code", "last_name", "first_name", "date_of_birth", "contact_number"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def update_patient_fields(
    patient_id: int,
    fields: dict[str, Any],
    acting_user_id: int,
    *,
    action: str = "UPDATE_PATIENT",
    target_type: str = "patient",
) -> bool:
    allowed = {
        "last_name",
        "first_name",
        "middle_name",
        "date_of_birth",
        "age",
        "sex",
        "contact_number",
        "email",
        "address",
        "height_cm",
        "weight_kg",
        "diabetes_type",
        "dm_duration_years",
        "hba1c",
        "current_medications",
        "known_allergies",
        "other_conditions",
        "current_eye_treatment",
        "previous_eye_treatment",
        "last_eye_exam_date",
    }
    sets = []
    values: list[Any] = []
    for k, v in (fields or {}).items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ?")
        values.append(v)
    if not sets:
        return False
    values.append(patient_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE emr_patients SET {', '.join(sets)} WHERE patient_id = ?",
            values,
        )
        ok = cur.rowcount > 0
        conn.commit()
        if ok:
            log_emr_action(acting_user_id, action, target_type, patient_id, "")
        return ok
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def get_queue_entry(queue_id: int) -> Optional[dict[str, Any]]:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT queue_id, patient_id, queue_number, visit_date, status, assigned_by, screening_purpose,
                   archived_at, archived_by, archive_reason,
                   notes
            FROM emr_queue_entries
            WHERE queue_id = ?
            """,
            (queue_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [
            "queue_id",
            "patient_id",
            "queue_number",
            "visit_date",
            "status",
            "assigned_by",
            "screening_purpose",
            "archived_at",
            "archived_by",
            "archive_reason",
            "notes",
        ]
        return dict(zip(cols, row))
    finally:
        conn.close()


def get_visit_details(queue_id: int) -> Optional[dict[str, Any]]:
    """Return per-visit clinical details for a queue entry (or None)."""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM emr_visit_details WHERE queue_id = ? LIMIT 1", (int(queue_id),))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def upsert_visit_details(
    *,
    queue_id: int,
    patient_id: int,
    captured_by: Optional[int],
    details: dict[str, Any],
) -> bool:
    """
    Create or update per-visit details for the queue entry.
    Only fields in the allowed whitelist are persisted.
    """
    allowed = {
        "visual_acuity_left",
        "visual_acuity_right",
        "blood_pressure_systolic",
        "blood_pressure_diastolic",
        "fasting_blood_sugar",
        "random_blood_sugar",
        "diabetes_type",
        "dm_duration_years",
        "hba1c",
        "diabetes_diagnosis_date",
        "treatment_regimen",
        "prev_dr_stage",
        "prev_treatment",
        "symptom_blurred_vision",
        "symptom_floaters",
        "symptom_flashes",
        "symptom_vision_loss",
        "symptom_other",
        "height_cm",
        "weight_kg",
        "notes",
    }
    payload: dict[str, Any] = {}
    for k, v in (details or {}).items():
        if k not in allowed:
            continue
        payload[k] = v
    if not payload:
        return False

    qid = int(queue_id)
    pid = int(patient_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT visit_detail_id FROM emr_visit_details WHERE queue_id = ? LIMIT 1", (qid,))
        existing = cur.fetchone()
        if not existing:
            cols = ["queue_id", "patient_id", "captured_by"] + list(payload.keys())
            vals = [qid, pid, captured_by] + [payload[c] for c in payload.keys()]
            placeholders = ", ".join(["?"] * len(cols))
            cur.execute(
                f"INSERT INTO emr_visit_details ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
            conn.commit()
            log_emr_action(captured_by, "CREATE_VISIT_DETAILS", "queue_entries", qid, "")
            return True

        sets = ", ".join([f"{k} = ?" for k in payload.keys()] + ["captured_by = ?", "captured_at = datetime('now')", "updated_at = datetime('now')"])
        vals2 = [payload[k] for k in payload.keys()] + [captured_by, qid]
        cur.execute(
            f"UPDATE emr_visit_details SET {sets} WHERE queue_id = ?",
            vals2,
        )
        ok = cur.rowcount > 0
        conn.commit()
        if ok:
            log_emr_action(captured_by, "UPDATE_VISIT_DETAILS", "queue_entries", qid, "")
        return ok
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def frontdesk_save_and_queue(
    *,
    acting_user_id: int,
    last_name: str,
    first_name: str,
    date_of_birth: str,
    sex: str = "",
    contact_number: str = "",
    email: str = "",
    address: str = "",
    screening_purpose: str = "new",
    visit_details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Fast-path for frontdesk "Save & Queue Patient".

    Collapses multiple open/commit cycles into a single SQLite transaction to reduce UI latency.
    Returns: {"patient_id","patient_code","queue_id","queue_number"}.
    """
    uid = int(acting_user_id)
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    dob = (date_of_birth or "").strip()[:10]
    if not fn or not ln or not dob:
        raise ValueError("first_name, last_name, date_of_birth required")
    purpose = "follow_up" if str(screening_purpose or "").strip().lower() == "follow_up" else "new"
    vd = date.today().isoformat()

    # Visit details whitelist mirrors upsert_visit_details.
    allowed_details = {
        "visual_acuity_left",
        "visual_acuity_right",
        "blood_pressure_systolic",
        "blood_pressure_diastolic",
        "fasting_blood_sugar",
        "random_blood_sugar",
        "diabetes_type",
        "dm_duration_years",
        "hba1c",
        "diabetes_diagnosis_date",
        "treatment_regimen",
        "prev_dr_stage",
        "prev_treatment",
        "symptom_blurred_vision",
        "symptom_floaters",
        "symptom_flashes",
        "symptom_vision_loss",
        "symptom_other",
        "height_cm",
        "weight_kg",
        "notes",
    }
    payload: dict[str, Any] = {}
    for k, v in (visit_details or {}).items():
        if k in allowed_details:
            payload[k] = v

    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")

        # Patient upsert (identity = first,last,dob).
        cur.execute(
            """
            SELECT patient_id, patient_code
            FROM emr_patients
            WHERE lower(trim(first_name)) = lower(trim(?))
              AND lower(trim(last_name)) = lower(trim(?))
              AND date_of_birth = ?
            ORDER BY patient_id ASC
            LIMIT 1
            """,
            (fn, ln, dob),
        )
        prow = cur.fetchone()
        if not prow:
            # Generate a unique patient_code resilient to gaps (MAX+1), and retry if a
            # concurrent writer created the same code between read and insert.
            patient_id = 0
            patient_code = ""
            last_err: Optional[Exception] = None
            for _ in range(5):
                code = _next_patient_code_in_tx(cur)
                try:
                    cur.execute(
                        """
                        INSERT INTO emr_patients (
                            patient_code, last_name, first_name, date_of_birth, sex,
                            contact_number, email, address, created_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            code,
                            ln,
                            fn,
                            dob,
                            (sex or "").strip() or None,
                            (contact_number or "").strip() or None,
                            (email or "").strip() or None,
                            (address or "").strip() or None,
                            uid,
                        ),
                    )
                    patient_id = int(cur.lastrowid)
                    patient_code = code
                    break
                except sqlite3.IntegrityError as err:
                    # Only retry on patient_code uniqueness collision.
                    msg = str(err).lower()
                    last_err = err
                    if "unique constraint" in msg and "emr_patients.patient_code" in msg:
                        continue
                    raise
            if not patient_id:
                raise sqlite3.IntegrityError(str(last_err or "Could not allocate unique patient code"))
        else:
            patient_id = int(prow[0])
            patient_code = str(prow[1] or "").strip()
            cur.execute(
                """
                UPDATE emr_patients
                SET last_name = ?, first_name = ?, date_of_birth = ?,
                    sex = ?, contact_number = ?, email = ?, address = ?
                WHERE patient_id = ?
                """,
                (
                    ln,
                    fn,
                    dob,
                    (sex or "").strip() or None,
                    (contact_number or "").strip() or None,
                    (email or "").strip() or None,
                    (address or "").strip() or None,
                    patient_id,
                ),
            )

        # If frontdesk didn't re-enter diagnosis date, keep the last known one.
        dd = str(payload.get("diabetes_diagnosis_date") or "").strip()
        if not dd:
            with contextlib.suppress(Exception):
                last_dd = get_latest_diabetes_diagnosis_date(int(patient_id))
                if last_dd:
                    payload["diabetes_diagnosis_date"] = last_dd

        # Guard: no second active queue entry today.
        cur.execute(
            """
            SELECT queue_number, status
            FROM emr_queue_entries
            WHERE patient_id = ? AND visit_date = ? AND status IN ('waiting','in_progress')
            ORDER BY queue_id DESC
            LIMIT 1
            """,
            (patient_id, vd),
        )
        ex = cur.fetchone()
        if ex:
            qn = str(ex[0] or "")
            st = str(ex[1] or "unknown")
            raise ValueError(f"Patient already has an active visit today: {qn} (status: {st}).")

        # Assign queue number for today.
        cur.execute("SELECT COUNT(*) FROM emr_queue_entries WHERE visit_date = ?", (vd,))
        qseq = int((cur.fetchone() or [0])[0] or 0) + 1
        queue_number = f"Q-{qseq:03d}"
        cur.execute(
            """
            INSERT INTO emr_queue_entries (patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes)
            VALUES (?, ?, ?, 'waiting', ?, ?, '')
            """,
            (patient_id, queue_number, vd, uid, purpose),
        )
        queue_id = int(cur.lastrowid)

        # Upsert visit details for this queue entry (if provided).
        if payload:
            cur.execute("SELECT visit_detail_id FROM emr_visit_details WHERE queue_id = ? LIMIT 1", (queue_id,))
            existing = cur.fetchone()
            if not existing:
                cols = ["queue_id", "patient_id", "captured_by"] + list(payload.keys())
                vals = [queue_id, patient_id, uid] + [payload[c] for c in payload.keys()]
                placeholders = ", ".join(["?"] * len(cols))
                cur.execute(
                    f"INSERT INTO emr_visit_details ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
            else:
                sets = ", ".join([f"{k} = ?" for k in payload.keys()] + ["captured_by = ?", "captured_at = datetime('now')", "updated_at = datetime('now')"])
                vals2 = [payload[k] for k in payload.keys()] + [uid, queue_id]
                cur.execute(f"UPDATE emr_visit_details SET {sets} WHERE queue_id = ?", vals2)

        conn.commit()
        return {
            "patient_id": patient_id,
            "patient_code": patient_code,
            "queue_id": queue_id,
            "queue_number": queue_number,
        }
    except Exception:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        conn.close()

def list_emr_timeline_records(patient_id: int) -> list[dict[str, Any]]:
    """
    Build timeline records compatible with legacy `patient_records` dicts so existing UI
    can render without rework. These are derived from EMR screenings + per-eye rows + visit details.
    """
    pid = int(patient_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM emr_patients WHERE patient_id = ?", (pid,))
        prow = cur.fetchone()
        if not prow:
            return []
        pcols = [d[0] for d in cur.description]
        patient = dict(zip(pcols, prow))

        # Fallback clinical history values for patients whose screenings are not attached to a queue entry
        # (e.g., older flows or recovery paths). Prefer the most recent non-empty visit_details values.
        cur.execute(
            """
            SELECT prev_dr_stage
            FROM emr_visit_details
            WHERE patient_id = ?
              AND prev_dr_stage IS NOT NULL
              AND trim(prev_dr_stage) != ''
              AND lower(trim(prev_dr_stage)) NOT IN ('select')
            ORDER BY datetime(captured_at) DESC, visit_detail_id DESC
            LIMIT 1
            """,
            (pid,),
        )
        _row_prev = cur.fetchone()
        fallback_prev_dr_stage = str(_row_prev[0] or "").strip() if _row_prev else ""

        cur.execute(
            """
            SELECT screening_id, queue_entry_id, screening_date, screening_type, eye_screened, session_status, performed_by, doctor_notes
            FROM emr_screenings
            WHERE patient_id = ?
            ORDER BY screening_date ASC, screening_id ASC
            """,
            (pid,),
        )
        screenings = cur.fetchall()

        # Resolve "screened by" / doctor label from the existing performed_by user id.
        # (No UI input: this is derived from the authenticated clinician that started the screening.)
        performed_ids = sorted({int(r[6]) for r in screenings if r and r[6] is not None})
        performed_map: dict[int, dict[str, str]] = {}
        if performed_ids:
            placeholders = ",".join(["?"] * len(performed_ids))
            cur.execute(
                f"""
                SELECT id,
                       COALESCE(NULLIF(display_name,''), NULLIF(full_name,''), NULLIF(username,''), '') AS label,
                       COALESCE(NULLIF(username,''), '') AS username
                FROM users
                WHERE id IN ({placeholders})
                """,
                performed_ids,
            )
            for uid, label, username in cur.fetchall():
                performed_map[int(uid)] = {"label": str(label or "").strip(), "username": str(username or "").strip()}

        # Visit-level archive state (single source of truth): emr_queue_entries.
        queue_ids = sorted({int(r[1]) for r in screenings if r and r[1] is not None})
        archive_map: dict[int, tuple[str, Optional[int], str, str]] = {}
        if queue_ids:
            placeholders = ",".join(["?"] * len(queue_ids))
            cur.execute(
                f"""
                SELECT q.queue_id, q.archived_at, q.archived_by, q.archive_reason,
                       COALESCE(NULLIF(u.display_name,''), NULLIF(u.full_name,''), NULLIF(u.username,''), '') AS archived_by_label
                FROM emr_queue_entries q
                LEFT JOIN users u ON u.id = q.archived_by
                WHERE q.queue_id IN ({placeholders})
                """,
                queue_ids,
            )
            for qid, archived_at, archived_by, archive_reason, archived_by_label in cur.fetchall():
                archive_map[int(qid)] = (
                    str(archived_at or ""),
                    int(archived_by) if archived_by is not None else None,
                    str(archive_reason or ""),
                    str(archived_by_label or ""),
                )

        out: list[dict[str, Any]] = []
        for (sid, qid, sdate, stype, eye_screened, sess_status, performed_by, doctor_notes) in screenings:
            performer_label = ""
            performer_username = ""
            if performed_by is not None:
                try:
                    hit = performed_map.get(int(performed_by)) or {}
                    performer_label = str(hit.get("label") or "").strip()
                    performer_username = str(hit.get("username") or "").strip()
                except (TypeError, ValueError):
                    performer_label = ""
                    performer_username = ""
            cur.execute(
                """
                SELECT eye_side, ai_dr_grade, ai_confidence, total_uncertainty, doctor_accepted_ai,
                       final_dr_grade, override_justification, final_treatment_notes,
                       fundus_image_path, gradcam_image_path
                FROM emr_screening_eyes
                WHERE screening_id = ?
                """,
                (int(sid),),
            )
            eye_rows = cur.fetchall()
            visit_details = get_visit_details(int(qid)) if qid else None

            archived_at = None
            archived_by_user_id = None
            archived_by_label = None
            archive_reason = None
            if qid:
                hit = archive_map.get(int(qid))
                if hit:
                    a_at, a_by, a_reason, a_label = hit
                    archived_at = a_at or None
                    archived_by_user_id = a_by
                    archive_reason = a_reason or None
                    archived_by_label = a_label or None

            for (eye_side, ai_grade, ai_conf, total_unc, doc_accept, final_grade, override_j, final_notes, fundus_path, grad_path) in eye_rows:
                # Map EMR row into legacy-ish record fields.
                eye_label = "Right Eye" if str(eye_side) == "Right" else "Left Eye"
                final_grade_i = int(final_grade) if final_grade is not None and str(final_grade).strip() != "" else None
                ai_grade_i = int(ai_grade) if ai_grade is not None and str(ai_grade).strip() != "" else None
                severity = {0: "No DR", 1: "Mild DR", 2: "Moderate DR", 3: "Severe DR", 4: "Proliferative DR"}
                final_label = severity.get(final_grade_i, "")
                ai_label = severity.get(ai_grade_i, "")

                conf_pct = None
                unc_pct = None
                try:
                    conf_pct = float(ai_conf) * 100.0 if ai_conf is not None else None
                except (TypeError, ValueError):
                    conf_pct = None
                try:
                    unc_pct = float(total_unc) * 100.0 if total_unc is not None else None
                except (TypeError, ValueError):
                    unc_pct = None
                conf_text = []
                if conf_pct is not None:
                    conf_text.append(f"Confidence: {conf_pct:.1f}%")
                if unc_pct is not None:
                    conf_text.append(f"Uncertainty: {unc_pct:.1f}%")

                rec: dict[str, Any] = {
                    # "id" must be unique; use screening eye uniqueness.
                    "id": int(sid) * 10 + (1 if str(eye_side) == "Left" else 0),
                    "patient_id": str(patient.get("patient_code") or ""),
                    "name": f"{patient.get('first_name','')} {patient.get('last_name','')}".strip(),
                    "birthdate": str(patient.get("date_of_birth") or ""),
                    "age": str(patient.get("age") or ""),
                    "sex": str(patient.get("sex") or ""),
                    "contact": str(patient.get("contact_number") or ""),
                    "email": str(patient.get("email") or ""),
                    "address": str(patient.get("address") or ""),
                    "eyes": eye_label,
                    "diabetes_type": str((visit_details or {}).get("diabetes_type") or patient.get("diabetes_type") or ""),
                    "duration": str((visit_details or {}).get("dm_duration_years") or patient.get("dm_duration_years") or ""),
                    "hba1c": str((visit_details or {}).get("hba1c") or patient.get("hba1c") or ""),
                    "prev_treatment": str((visit_details or {}).get("prev_treatment") or ""),
                    "notes": str((visit_details or {}).get("notes") or doctor_notes or ""),
                    "result": final_label or ai_label or "",
                    "confidence": " | ".join(conf_text),
                    "screened_at": str(sdate or ""),
                    # Attribution (compat with legacy patient_records UI)
                    "original_screener_username": performer_username,
                    "original_screener_name": performer_label,
                    # Some UIs expect a "doctor" field; use display label (not editable).
                    "decision_by_username": performer_label or performer_username,
                    "ai_classification": ai_label,
                    "doctor_classification": final_label or ai_label,
                    # Raw clinician decision fields (authoritative).
                    "doctor_accepted_ai": int(doc_accept) if doc_accept is not None and str(doc_accept).strip() != "" else None,
                    "final_dr_grade": final_grade_i,
                    "decision_mode": "accepted" if (doc_accept in (1, True, "1", "true")) else ("overridden" if final_grade is not None else "pending"),
                    "override_justification": str(override_j or ""),
                    "final_diagnosis_icdr": final_label or ai_label,
                    "doctor_findings": str(final_notes or doctor_notes or ""),
                    "source_image_path": str(fundus_path or ""),
                    "heatmap_image_path": str(grad_path or ""),
                    "height": str((visit_details or {}).get("height_cm") or ""),
                    "weight": str((visit_details or {}).get("weight_kg") or ""),
                    "bmi": str((visit_details or {}).get("bmi") or ""),
                    "visual_acuity_left": str((visit_details or {}).get("visual_acuity_left") or ""),
                    "visual_acuity_right": str((visit_details or {}).get("visual_acuity_right") or ""),
                    "blood_pressure_systolic": str((visit_details or {}).get("blood_pressure_systolic") or ""),
                    "blood_pressure_diastolic": str((visit_details or {}).get("blood_pressure_diastolic") or ""),
                    "fasting_blood_sugar": str((visit_details or {}).get("fasting_blood_sugar") or ""),
                    "random_blood_sugar": str((visit_details or {}).get("random_blood_sugar") or ""),
                    "diabetes_diagnosis_date": str((visit_details or {}).get("diabetes_diagnosis_date") or ""),
                    "treatment_regimen": str((visit_details or {}).get("treatment_regimen") or ""),
                    "prev_dr_stage": str(
                        (visit_details or {}).get("prev_dr_stage")
                        or patient.get("prev_dr_stage")
                        or fallback_prev_dr_stage
                        or ""
                    ),
                    "symptom_blurred_vision": "Yes" if (visit_details or {}).get("symptom_blurred_vision") else "No",
                    "symptom_floaters": "Yes" if (visit_details or {}).get("symptom_floaters") else "No",
                    "symptom_flashes": "Yes" if (visit_details or {}).get("symptom_flashes") else "No",
                    "symptom_vision_loss": "Yes" if (visit_details or {}).get("symptom_vision_loss") else "No",
                    "follow_up": "Yes" if str(stype or "").lower() == "follow_up" else "",
                    "followup_date": str(sdate or "") if str(stype or "").lower() == "follow_up" else "",
                    "followup_label": "Follow-up screening" if str(stype or "").lower() == "follow_up" else "",
                    "screening_type": str(stype or ""),
                    "previous_screening_id": None,
                    # Group by queue entry so bilateral results group into one "visit".
                    "screening_group_id": f"queue-{int(qid)}" if qid else f"screening-{int(sid)}",
                    # Visit-level archive state (same for both eyes in the visit).
                    "archived_at": archived_at,
                    "archived_by": archived_by_label,
                    "archived_by_user_id": archived_by_user_id,
                    "archive_reason": archive_reason,
                }
                out.append(rec)
        return out
    finally:
        conn.close()


def archive_visit(
    queue_id: int,
    archived: bool,
    actor_user_id: Optional[int],
    reason: Optional[str] = None,
) -> bool:
    """
    Archive/unarchive an entire visit record (queue entry). This is the sole source of truth
    for clinician-facing archive state.
    """
    qid = int(queue_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        # Ensure schema/migrations are applied for older DBs.
        try:
            UserManager._ensure_emr_schema(conn)
        except Exception:
            pass
        if archived:
            cur.execute(
                """
                UPDATE emr_queue_entries
                SET archived_at = datetime('now'),
                    archived_by = ?,
                    archive_reason = ?,
                    updated_at = datetime('now')
                WHERE queue_id = ?
                """,
                (int(actor_user_id) if actor_user_id is not None else None, (reason or "").strip() or None, qid),
            )
        else:
            cur.execute(
                """
                UPDATE emr_queue_entries
                SET archived_at = NULL,
                    archived_by = NULL,
                    archive_reason = NULL,
                    updated_at = datetime('now')
                WHERE queue_id = ?
                """,
                (qid,),
            )
        ok = cur.rowcount > 0
        conn.commit()
        if ok:
            log_emr_action(
                actor_user_id,
                "ARCHIVE_VISIT" if archived else "RESTORE_VISIT",
                "queue_entries",
                qid,
                json.dumps({"reason": (reason or "").strip() or None}),
            )
        return ok
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def is_visit_archived(queue_id: int) -> bool:
    qid = int(queue_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT archived_at FROM emr_queue_entries WHERE queue_id = ?", (qid,))
        row = cur.fetchone()
        return bool(row and str(row[0] or "").strip())
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def delete_visit(queue_id: int, actor_user_id: Optional[int]) -> bool:
    """
    Permanently delete a visit (queue entry). This cascades to screenings/visit details.
    Use only for archived visits from the UI's 'Delete Selected' action.
    """
    qid = int(queue_id)
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT archived_at FROM emr_queue_entries WHERE queue_id = ?", (qid,))
        row = cur.fetchone()
        if not row or not str(row[0] or "").strip():
            return False

        # IMPORTANT:
        # `emr_screenings.queue_entry_id` is defined as ON DELETE SET NULL,
        # so deleting only the queue entry would leave orphan screenings that still
        # appear in the timeline. Delete the visit's screenings explicitly.
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("DELETE FROM emr_screenings WHERE queue_entry_id = ?", (qid,))
        cur.execute("DELETE FROM emr_queue_entries WHERE queue_id = ?", (qid,))
        ok = cur.rowcount > 0
        conn.commit()
        if ok:
            log_emr_action(actor_user_id, "DELETE_ARCHIVED_VISIT", "queue_entries", qid, "")
        return ok
    except sqlite3.Error:
        with contextlib.suppress(Exception):
            conn.rollback()
        return False
    finally:
        conn.close()


def get_today_queue_for_patient(patient_id: int) -> Optional[dict[str, Any]]:
    vd = date.today().isoformat()
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT queue_id, patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes
            FROM emr_queue_entries
            WHERE patient_id = ? AND visit_date = ?
            ORDER BY queue_id DESC
            LIMIT 1
            """,
            (patient_id, vd),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = ["queue_id", "patient_id", "queue_number", "visit_date", "status", "assigned_by", "screening_purpose", "notes"]
        return dict(zip(cols, row))
    finally:
        conn.close()


def get_today_in_progress_queue_for_patient(patient_id: int) -> Optional[dict[str, Any]]:
    vd = date.today().isoformat()
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT queue_id, patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes
            FROM emr_queue_entries
            WHERE patient_id = ? AND visit_date = ? AND status = 'in_progress'
            ORDER BY queue_id DESC
            LIMIT 1
            """,
            (patient_id, vd),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = ["queue_id", "patient_id", "queue_number", "visit_date", "status", "assigned_by", "screening_purpose", "notes"]
        return dict(zip(cols, row))
    finally:
        conn.close()


def assign_queue_entry(
    patient_id: int,
    assigned_by: int,
    visit_date: Optional[str] = None,
    *,
    screening_purpose: str = "new",
    notes: str = "",
) -> int:
    if not _is_allowed(assigned_by, {"frontdesk", "admin"}):
        raise PermissionError("Only front desk or admin can assign queue entries.")
    vd = visit_date or date.today().isoformat()
    label = next_queue_label(vd)
    purpose = str(screening_purpose or "new").strip().lower()
    if purpose not in {"new", "follow_up"}:
        purpose = "new"
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO emr_queue_entries (patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes)
            VALUES (?, ?, ?, 'waiting', ?, ?, ?)
            """,
            (patient_id, label, vd, assigned_by, purpose, (notes or "").strip() or None),
        )
        qid = int(cur.lastrowid)
        # Create the "encounter" row immediately so the patient visit exists in records
        # even before any fundus images / diagnosis are attached.
        with contextlib.suppress(Exception):
            ensure_visit_details_row(qid, int(patient_id), int(assigned_by) if assigned_by is not None else None)
        conn.commit()
        log_emr_action(
            assigned_by,
            "ASSIGN_QUEUE",
            "queue_entries",
            qid,
            json.dumps({"queue_number": label, "visit_date": vd, "patient_id": patient_id}),
        )
        return qid
    finally:
        conn.close()


def add_queue_entry(patient_id: int, assigned_by: int, visit_date: Optional[str] = None, notes: str = "") -> int:
    """Backward-compatible alias for older UI code."""
    return assign_queue_entry(patient_id, assigned_by, visit_date=visit_date, screening_purpose="new", notes=notes)


def list_queue_rows(visit_date: Optional[str] = None) -> list[dict[str, Any]]:
    vd = visit_date or date.today().isoformat()
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                q.queue_id, q.queue_number, q.visit_date, q.status, q.patient_id, q.screening_purpose, q.notes,
                p.patient_code, p.last_name, p.first_name, p.date_of_birth, p.sex, q.created_at
            FROM emr_queue_entries q
            JOIN emr_patients p ON p.patient_id = q.patient_id
            WHERE q.visit_date = ? AND q.status IN ('waiting', 'in_progress')
            ORDER BY
                CASE q.status
                    WHEN 'in_progress' THEN 0
                    WHEN 'waiting' THEN 1
                    ELSE 2
                END,
                CAST(SUBSTR(q.queue_number, 3) AS INTEGER) ASC, q.queue_id ASC
            """,
            (vd,),
        )
        out = []
        for r in cur.fetchall():
            out.append(
                {
                    "queue_id": r[0],
                    "queue_number": r[1],
                    "visit_date": r[2],
                    "status": r[3],
                    "patient_id": r[4],
                    "screening_purpose": r[5] or "new",
                    "notes": r[6] or "",
                    "patient_code": r[7],
                    "last_name": r[8],
                    "first_name": r[9],
                    "date_of_birth": r[10],
                    "sex": r[11] if len(r) > 11 else "",
                    "created_at": r[12] if len(r) > 12 else None,
                }
            )
        return out
    finally:
        conn.close()


def set_queue_status(queue_id: int, status: str, user_id: Optional[int] = None) -> bool:
    if status not in ("waiting", "in_progress", "completed", "cancelled"):
        return False
    # Permission: who is allowed to perform which mutation.
    if status in ("in_progress", "completed"):
        if not _is_allowed(user_id, {"clinician", "admin"}):
            return False
    elif status in ("waiting", "cancelled"):
        if not _is_allowed(user_id, {"frontdesk", "clinician", "admin"}):
            return False
    conn = _open_conn()
    try:
        cur = conn.cursor()
        # Enforce valid transition graph.
        cur.execute("SELECT status FROM emr_queue_entries WHERE queue_id = ?", (queue_id,))
        row = cur.fetchone()
        current = str(row[0] or "").strip().lower() if row else ""
        allowed_transitions = {
            "waiting": {"in_progress", "cancelled"},
            "in_progress": {"completed", "cancelled"},
            "completed": set(),
            "cancelled": set(),
            "": {"waiting", "in_progress", "completed", "cancelled"},
        }
        if current in allowed_transitions and status not in allowed_transitions[current]:
            return False
        cur.execute(
            "UPDATE emr_queue_entries SET status = ? WHERE queue_id = ?",
            (status, queue_id),
        )
        ok = cur.rowcount > 0
        conn.commit()
        if ok and user_id is not None:
            if status == "cancelled":
                log_emr_action(user_id, "CANCEL_QUEUE", "queue_entries", queue_id, "")
            else:
                log_emr_action(user_id, f"QUEUE_{status.upper()}", "queue_entries", queue_id, "")
        return ok
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def clear_queue(visit_date: Optional[str] = None, *, user_id: Optional[int] = None) -> int:
    """Delete queue entries for a given visit date (default: today). Returns rows deleted."""
    # Operational action: allow core clinical/frontdesk roles to clear the worklist
    # when resetting a session/demo dataset.
    if not _is_allowed(user_id, {"admin", "frontdesk", "clinician", "doctor"}):
        return 0
    vd = visit_date or date.today().isoformat()
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM emr_queue_entries WHERE visit_date = ?", (vd,))
        deleted = int(cur.rowcount or 0)
        conn.commit()
        if user_id is not None:
            log_emr_action(user_id, "CLEAR_QUEUE", "queue_entries", None, json.dumps({"visit_date": vd, "deleted": deleted}))
        return deleted
    finally:
        conn.close()


def count_screenings_for_patient(patient_id: int) -> int:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM emr_screenings WHERE patient_id = ?", (patient_id,))
        r = cur.fetchone()
        return int(r[0] or 0) if r else 0
    finally:
        conn.close()


def list_screenings_for_patient(patient_id: int) -> list[dict[str, Any]]:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.screening_id, s.screening_type, s.screening_date, s.eye_screened, s.session_status,
                   s.queue_entry_id,
                   COALESCE(NULLIF(u.display_name,''), NULLIF(u.full_name,''), NULLIF(u.username,''), '') AS performed_by_label,
                   COALESCE(NULLIF(u.username,''), '') AS performed_by_username
            FROM emr_screenings s
            LEFT JOIN users u ON u.id = s.performed_by
            WHERE s.patient_id = ?
            ORDER BY s.screening_date DESC, s.screening_id DESC
            """,
            (patient_id,),
        )
        screenings = []
        for r in cur.fetchall():
            screening_id = int(r[0])
            cur.execute(
                """
                SELECT eye_side, ai_dr_grade, ai_confidence, uncertainty_status, doctor_accepted_ai,
                       final_dr_grade, final_treatment_notes, override_justification, image_quality_status,
                       fundus_image_path, gradcam_image_path
                FROM emr_screening_eyes
                WHERE screening_id = ?
                ORDER BY CASE eye_side WHEN 'Left' THEN 0 ELSE 1 END
                """,
                (screening_id,),
            )
            eye_rows = cur.fetchall()
            eyes = []
            final_labels = []
            for eye in eye_rows:
                side = str(eye[0] or "")
                final_grade = eye[5]
                ai_grade = eye[1]
                final_labels.append(f"{side}:{final_grade if final_grade is not None else 'Pending'}")
                eyes.append(
                    {
                        "eye_side": side,
                        "ai_dr_grade": ai_grade,
                        "ai_confidence": eye[2],
                        "uncertainty_status": eye[3] or "pending",
                        "doctor_accepted_ai": eye[4],
                        "final_dr_grade": final_grade,
                        "final_treatment_notes": eye[6] or "",
                        "override_justification": eye[7] or "",
                        "image_quality_status": eye[8] or "pending",
                        "fundus_image_path": eye[9] or "",
                        "gradcam_image_path": eye[10] or "",
                    }
                )
            screenings.append(
                {
                    "screening_id": screening_id,
                    "screening_type": r[1],
                    "screening_date": r[2],
                    "eye_screened": r[3],
                    "session_status": r[4],
                    "queue_entry_id": r[5],
                    "performed_by_label": r[6] or "",
                    "performed_by_username": r[7] or "",
                    "final_grade_summary": ", ".join(final_labels) if final_labels else "Pending",
                    "eyes": eyes,
                }
            )
        return screenings
    finally:
        conn.close()


def list_patient_ids_with_screenings() -> list[int]:
    """Return distinct EMR patient_ids that have at least one screening."""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT patient_id FROM emr_screenings ORDER BY patient_id ASC")
        return [int(r[0]) for r in cur.fetchall() if r and r[0] is not None]
    finally:
        conn.close()


def create_screening_session(
    patient_id: int,
    queue_entry_id: Optional[int],
    performed_by: int,
    screening_type: str,
    eye_screened: str,
    fundus_paths: Any,
) -> int:
    if not _is_allowed(performed_by, {"clinician", "admin"}):
        raise PermissionError("Only clinicians can start screenings.")
    """
    Inserts emr_screenings + emr_screening_eyes (Left/Right/Both). fundus_path copied per eye row.
    """
    if screening_type not in ("initial", "follow_up"):
        raise ValueError("screening_type")
    if eye_screened not in ("Left", "Right", "Both"):
        raise ValueError("eye_screened")
    sides = ("Left", "Right") if eye_screened == "Both" else (eye_screened,)
    if isinstance(fundus_paths, str):
        raw = (fundus_paths or "").strip()
        side_path_map = {side: raw for side in sides}
    else:
        side_path_map = {}
        for side in sides:
            side_path_map[side] = str((fundus_paths or {}).get(side, "")).strip()
    for side in sides:
        if not side_path_map.get(side):
            raise ValueError(f"fundus path required for {side}")

    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO emr_screenings (
                patient_id, queue_entry_id, performed_by, screening_type, eye_screened, session_status
            ) VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (patient_id, queue_entry_id, performed_by, screening_type, eye_screened),
        )
        sid = int(cur.lastrowid)
        upload_dir = Path(__file__).resolve().parent / "uploads" / "fundus" / str(sid)
        upload_dir.mkdir(parents=True, exist_ok=True)
        for side in sides:
            src = side_path_map[side]
            ext = Path(src).suffix.lower() or ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
                raise ValueError(f"Unsupported image type for {side}")
            if not _is_valid_image_magic_bytes(str(src)):
                raise ValueError(f"File is not a valid image: {side}")
            dst = upload_dir / f"{side.lower()}{ext}"
            shutil.copy2(src, dst)
            if not _is_valid_image_magic_bytes(str(dst)):
                raise ValueError(f"Failed to store a valid image on disk: {side}")
            cur.execute(
                """
                INSERT INTO emr_screening_eyes (screening_id, eye_side, fundus_image_path, image_quality_status)
                VALUES (?, ?, ?, 'pending')
                """,
                (sid, side, str(dst)),
            )
        conn.commit()
        log_emr_action(
            performed_by,
            "START_SCREENING",
            "screening",
            sid,
            json.dumps({"type": screening_type, "eyes": eye_screened}),
        )
        trigger_ai_pipeline_async(sid)
        return sid
    finally:
        conn.close()


def ensure_screening_eye_row(
    *,
    screening_id: int,
    eye_side: str,
    fundus_source_path: str,
    performed_by: Optional[int] = None,
) -> bool:
    """
    Ensure `emr_screening_eyes` has a row for the given eye.

    This is a recovery helper for cases where an `emr_screenings` row exists but the
    corresponding eye row was not created (e.g., interrupted session creation).
    """
    side_raw = str(eye_side or "").strip()
    side = "Left" if side_raw.lower().startswith("l") else "Right" if side_raw.lower().startswith("r") else ""
    if not side:
        return False
    src = str(fundus_source_path or "").strip()
    if not src or not os.path.isfile(src):
        return False
    if not _is_valid_image_magic_bytes(str(src)):
        return False

    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM emr_screening_eyes WHERE screening_id = ? AND eye_side = ? LIMIT 1",
            (int(screening_id), side),
        )
        if cur.fetchone():
            return True

        upload_dir = Path(__file__).resolve().parent / "uploads" / "fundus" / str(int(screening_id))
        upload_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(src).suffix.lower() or ".jpg"
        if ext not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            ext = ".jpg"
        dst = upload_dir / f"{side.lower()}{ext}"
        shutil.copy2(src, dst)
        if not _is_valid_image_magic_bytes(str(dst)):
            return False

        cur.execute(
            """
            INSERT INTO emr_screening_eyes (screening_id, eye_side, fundus_image_path, image_quality_status)
            VALUES (?, ?, ?, 'pending')
            """,
            (int(screening_id), side, str(dst)),
        )
        conn.commit()
        log_emr_action(performed_by, "RECOVER_SCREENING_EYE_ROW", "screening_eyes", int(cur.lastrowid), json.dumps({"sid": int(screening_id), "eye": side}))
        return True
    except Exception:
        return False
    finally:
        conn.close()


def retrigger_ai_pipeline(screening_id: int) -> None:
    """M4: retry AI / M5 — same as initial async trigger (re-run when user clicks retry)."""
    trigger_ai_pipeline_async(screening_id)


def trigger_ai_pipeline_async(screening_id: int) -> None:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT performed_by FROM emr_screenings WHERE screening_id = ?", (screening_id,))
        row = cur.fetchone()
        performed_by = int(row[0]) if row and row[0] is not None else None
    finally:
        conn.close()

    log_emr_action(performed_by, "AI_PIPELINE_QUEUED", "screening", screening_id, "")

    def _runner() -> None:
        _run_screening_ai_pipeline(screening_id, performed_by)

    threading.Thread(target=_runner, daemon=True, name=f"eyeshield-ai-{screening_id}").start()


def attach_gradcam_to_eye(
    *,
    eye_id: int,
    screening_id: int,
    eye_side: str,
    gradcam_source_path: str,
    performed_by: int | None = None,
) -> str | None:
    """
    Attach a locally-generated Grad-CAM image to an EMR screening eye.

    This is used when inference/heatmap generation happened outside the EMR pipeline
    (e.g., local model runs from the Screening UI) but we still want the Patient
    Overview timeline to render the heatmap image.
    """
    src = str(gradcam_source_path or "").strip()
    if not src or not os.path.isfile(src):
        return None
    side = str(eye_side or "").strip()
    if side not in {"Left", "Right"}:
        return None

    grad_dir = Path(__file__).resolve().parent / "uploads" / "gradcam" / str(int(screening_id))
    grad_dir.mkdir(parents=True, exist_ok=True)
    dst = grad_dir / f"{side.lower()}_gradcam.jpg"
    shutil.copy2(src, dst)

    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE emr_screening_eyes
            SET gradcam_image_path = ?, heatmap_generated = 1
            WHERE eye_id = ?
            """,
            (str(dst), int(eye_id)),
        )
        conn.commit()
    finally:
        conn.close()

    with contextlib.suppress(Exception):
        log_emr_action(performed_by, "ATTACH_GRADCAM", "screening_eyes", int(eye_id), str(dst))
    return str(dst)


def _laplacian_variance(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    lap = (
        g[1:-1, 1:-1] * -4.0
        + g[:-2, 1:-1]
        + g[2:, 1:-1]
        + g[1:-1, :-2]
        + g[1:-1, 2:]
    )
    return float(np.var(lap))


def _compute_quality_scores(image_path: str) -> tuple[float, float, float]:
    arr = np.array(Image.open(image_path).convert("RGB"))
    gray = np.dot(arr[..., :3], np.array([0.299, 0.587, 0.114], dtype=np.float32)).astype(np.uint8)
    blur_score = _laplacian_variance(gray)
    illumination_score = float(arr[..., 1].mean())  # green channel mean
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    probs = hist / (hist.sum() + 1e-12)
    probs = probs[probs > 0]
    entropy_score = float(-np.sum(probs * np.log2(probs)))
    return blur_score, illumination_score, entropy_score


def _quality_rejection_reason(blur: float, illum: float, entropy: float) -> str:
    if blur < BLUR_THRESHOLD:
        return "blur"
    if illum < ILLUMINATION_MIN:
        return "underexposed"
    if illum > ILLUMINATION_MAX:
        return "overexposed"
    if entropy < ENTROPY_THRESHOLD:
        return "low_detail"
    return ""


def _is_valid_image_magic_bytes(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if len(head) >= 4 and head[:4] in (b"II*\x00", b"MM\x00*"):
        return True
    return False


def _parse_confidence_uncertainty(conf_text: str) -> tuple[float, float]:
    text = str(conf_text or "")
    nums = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if len(nums) >= 2:
        return float(nums[0]) / 100.0, float(nums[1]) / 100.0
    if len(nums) == 1:
        return float(nums[0]) / 100.0, 0.0
    return 0.0, 0.0


def _update_screening_session_post_pipeline(screening_id: int, user_id: Optional[int]) -> None:
    """M5: after all per-eye work, set session_status when appropriate; log PIPELINE_COMPLETE."""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT image_quality_status FROM emr_screening_eyes WHERE screening_id = ?", (screening_id,))
        statuses = [str(r[0] or "pending") for r in cur.fetchall()]
        if not statuses:
            return
        if any(s == "pending" for s in statuses):
            return
        if all(s == "rejected" for s in statuses):
            new_status = "rejected_all"
        elif any(s == "rejected" for s in statuses) and any(s == "gradable" for s in statuses):
            new_status = "partial"
        else:
            log_emr_action(
                user_id,
                "PIPELINE_COMPLETE",
                "screening",
                screening_id,
                json.dumps({"session_status": "pending", "note": "all_gradable_awaiting_verification"}),
            )
            return
        cur.execute("UPDATE emr_screenings SET session_status = ? WHERE screening_id = ?", (new_status, screening_id))
        conn.commit()
        log_emr_action(
            user_id,
            "PIPELINE_COMPLETE",
            "screening",
            screening_id,
            json.dumps({"session_status": new_status}),
        )
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def _run_screening_ai_pipeline(screening_id: int, performed_by: Optional[int]) -> None:
    try:
        conn = _open_conn()
        cur = conn.cursor()
        cur.execute("SELECT eye_id FROM emr_screening_eyes WHERE screening_id = ?", (screening_id,))
        eye_ids = [int(r[0]) for r in cur.fetchall()]
        conn.close()
        for eye_id in eye_ids:
            _process_eye_pipeline(screening_id, eye_id, performed_by)
        _update_screening_session_post_pipeline(screening_id, performed_by)
    except Exception as exc:
        log_emr_action(performed_by, "AI_PIPELINE_FAILED", "screening", screening_id, str(exc))


def _process_eye_pipeline(screening_id: int, eye_id: int, performed_by: Optional[int]) -> None:
    from model_inference import is_model_available, predict_image, generate_heatmap

    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT eye_side, fundus_image_path, image_quality_status
            FROM emr_screening_eyes
            WHERE eye_id = ? AND screening_id = ?
            """,
            (eye_id, screening_id),
        )
        row = cur.fetchone()
        if not row:
            return
        eye_side = str(row[0] or "")
        image_path = str(row[1] or "")
        existing_qs = str(row[2] or "pending")
        if existing_qs != "pending":
            return
    finally:
        conn.close()

    if not image_path or not os.path.isfile(image_path):
        conn = _open_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE emr_screening_eyes
                SET image_quality_status = 'rejected', quality_rejection_reason = 'file_not_found'
                WHERE eye_id = ?
                """,
                (eye_id,),
            )
            conn.commit()
        finally:
            conn.close()
        log_emr_action(
            performed_by,
            "QUALITY_CHECK_FAILED",
            "screening_eyes",
            eye_id,
            json.dumps({"reason": "file_not_found"}),
        )
        return

    if not _is_valid_image_magic_bytes(image_path):
        conn = _open_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE emr_screening_eyes
                SET image_quality_status = 'rejected', quality_rejection_reason = 'invalid_image'
                WHERE eye_id = ?
                """,
                (eye_id,),
            )
            conn.commit()
        finally:
            conn.close()
        log_emr_action(
            performed_by,
            "QUALITY_CHECK",
            "screening_eyes",
            eye_id,
            json.dumps({"status": "rejected", "reason": "not_valid_image"}),
        )
        return

    # ---- M5-A quality check ----
    blur, illum, entropy = _compute_quality_scores(image_path)
    rejection_reason = _quality_rejection_reason(blur, illum, entropy)
    quality_status = "rejected" if rejection_reason else "gradable"
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE emr_screening_eyes
            SET blur_score = ?, illumination_score = ?, entropy_score = ?,
                image_quality_status = ?, quality_rejection_reason = ?
            WHERE eye_id = ?
            """,
            (blur, illum, entropy, quality_status, rejection_reason or None, eye_id),
        )
        conn.commit()
    finally:
        conn.close()
    log_emr_action(
        performed_by, "QUALITY_CHECK", "screening_eyes", eye_id, json.dumps({"status": quality_status, "reason": rejection_reason})
    )
    if quality_status == "rejected":
        return

    # ---- M5-B inference + uncertainty ----
    if not is_model_available():
        conn = _open_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE emr_screening_eyes
                SET uncertainty_status = 'rejected', quality_rejection_reason = 'model_unavailable', ai_treatment_suggestion = NULL
                WHERE eye_id = ?
                """,
                (eye_id,),
            )
            conn.commit()
        finally:
            conn.close()
        log_emr_action(
            performed_by, "RUN_INFERENCE", "screening_eyes", eye_id, json.dumps({"error": "model_unavailable"}),
        )
        return

    label, conf_text, class_idx = predict_image(image_path)
    conf, total_unc = _parse_confidence_uncertainty(conf_text)
    total_unc = max(0.0, min(1.0, total_unc))
    aleatoric = total_unc * (1.0 - conf) if conf <= 1.0 else 0.0
    epistemic = max(0.0, total_unc - aleatoric)
    uncertainty_status = "rejected" if total_unc > UNCERTAINTY_THRESHOLD else "accepted"
    treatment = AI_TREATMENT_BY_GRADE.get(int(class_idx), "")
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE emr_screening_eyes
            SET ai_dr_grade = ?, ai_confidence = ?, aleatoric_uncertainty = ?,
                epistemic_uncertainty = ?, total_uncertainty = ?, uncertainty_status = ?,
                ai_treatment_suggestion = ?
            WHERE eye_id = ?
            """,
            (int(class_idx), float(conf), float(aleatoric), float(epistemic), float(total_unc), uncertainty_status, treatment or None, eye_id),
        )
        conn.commit()
    finally:
        conn.close()
    log_emr_action(
        performed_by,
        "RUN_INFERENCE",
        "screening_eyes",
        eye_id,
        json.dumps(
            {"label": label, "confidence": conf, "total_uncertainty": total_unc, "uncertainty_status": uncertainty_status, "grade": int(class_idx)}
        ),
    )

    if uncertainty_status != "accepted":
        return

    # ---- M5-C GradCAM++ (optional; write failure does not fail screening) ----
    heatmap_tmp = generate_heatmap(image_path, int(class_idx))
    grad_dir = Path(__file__).resolve().parent / "uploads" / "gradcam" / str(screening_id)
    grad_dir.mkdir(parents=True, exist_ok=True)
    grad_path = grad_dir / f"{eye_side.lower()}_gradcam.jpg"
    try:
        if not heatmap_tmp or not os.path.isfile(heatmap_tmp):
            raise OSError("no_heatmap")
        shutil.copy2(heatmap_tmp, grad_path)
    except OSError:
        log_emr_action(
            performed_by, "GRADCAM_FAILED", "screening_eyes", eye_id, json.dumps({"reason": "disk_write_error"}),
        )
        conn = _open_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE emr_screening_eyes SET heatmap_generated = 0, gradcam_image_path = NULL WHERE eye_id = ?",
                (eye_id,),
            )
            conn.commit()
        finally:
            conn.close()
    else:
        with contextlib.suppress(Exception):
            if heatmap_tmp and os.path.isfile(heatmap_tmp):
                os.remove(heatmap_tmp)
        conn = _open_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE emr_screening_eyes
                SET gradcam_image_path = ?, heatmap_generated = 1
                WHERE eye_id = ?
                """,
                (str(grad_path), eye_id),
            )
            conn.commit()
        finally:
            conn.close()
        log_emr_action(performed_by, "GENERATE_GRADCAM", "screening_eyes", eye_id, str(grad_path))


def get_screening(screening_id: int) -> Optional[dict[str, Any]]:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT screening_id, patient_id, queue_entry_id, performed_by, screening_date,
                   screening_type, eye_screened, session_status, doctor_notes
            FROM emr_screenings
            WHERE screening_id = ?
            """,
            (screening_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [
            "screening_id",
            "patient_id",
            "queue_entry_id",
            "performed_by",
            "screening_date",
            "screening_type",
            "eye_screened",
            "session_status",
            "doctor_notes",
        ]
        out = dict(zip(cols, row))
        cur.execute(
            """
            SELECT eye_id, eye_side, fundus_image_path, gradcam_image_path, image_quality_status,
                   ai_dr_grade, ai_confidence, uncertainty_status, doctor_accepted_ai,
                   final_dr_grade, override_justification, final_treatment_notes
            FROM emr_screening_eyes
            WHERE screening_id = ?
            ORDER BY CASE eye_side WHEN 'Left' THEN 0 ELSE 1 END
            """,
            (screening_id,),
        )
        out["eyes"] = [
            {
                "eye_id": r[0],
                "eye_side": r[1],
                "fundus_image_path": r[2] or "",
                "gradcam_image_path": r[3] or "",
                "image_quality_status": r[4] or "pending",
                "ai_dr_grade": r[5],
                "ai_confidence": r[6],
                "uncertainty_status": r[7] or "pending",
                "doctor_accepted_ai": r[8],
                "final_dr_grade": r[9],
                "override_justification": r[10] or "",
                "final_treatment_notes": r[11] or "",
            }
            for r in cur.fetchall()
        ]
        return out
    finally:
        conn.close()


def verify_screening(screening_id: int, user_id: int, eye_updates: list[dict[str, Any]]) -> bool:
    if not _is_allowed(user_id, {"clinician", "admin"}):
        return False
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT session_status FROM emr_screenings WHERE screening_id = ?", (screening_id,))
        prev_row = cur.fetchone()
        prev_status = str(prev_row[0] or "") if prev_row else ""
        for payload in eye_updates:
            eye_id = int(payload.get("eye_id"))
            accepted = payload.get("doctor_accepted_ai")
            final_grade = payload.get("final_dr_grade")
            justification = str(payload.get("override_justification") or "").strip()
            notes = str(payload.get("final_treatment_notes") or "").strip()
            cur.execute(
                """
                UPDATE emr_screening_eyes
                SET doctor_accepted_ai = ?, final_dr_grade = ?, override_justification = ?, final_treatment_notes = ?
                WHERE eye_id = ? AND screening_id = ?
                """,
                (accepted, final_grade, justification or None, notes or None, eye_id, screening_id),
            )
        cur.execute(
            """
            SELECT image_quality_status, uncertainty_status, doctor_accepted_ai, final_dr_grade
            FROM emr_screening_eyes
            WHERE screening_id = ?
            """,
            (screening_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return False
        rejected = [
            (str(r[0] or "").lower() == "rejected") or (str(r[1] or "").lower() == "rejected")
            for r in rows
        ]
        all_verified = all(r[2] is not None and r[3] is not None for r in rows)
        if all(rejected):
            session_status = "rejected_all"
        elif any(rejected):
            session_status = "partial"
        elif all_verified:
            session_status = "completed"
        else:
            session_status = "pending"
        cur.execute(
            "UPDATE emr_screenings SET session_status = ? WHERE screening_id = ?",
            (session_status, screening_id),
        )
        conn.commit()
        log_emr_action(user_id, "VERIFY_SCREENING", "screening", screening_id, json.dumps({"session_status": session_status}))
        if session_status == "completed" and prev_status != "completed":
            log_emr_action(
                user_id, "FINALIZE_SCREENING", "screening", screening_id, json.dumps({"session_status": session_status})
            )
        return True
    except Exception:
        return False
    finally:
        conn.close()


def update_screening_doctor_notes(screening_id: int, user_id: int, doctor_notes: str) -> bool:
    """Update emr_screenings.doctor_notes (free text) and log."""
    if not _is_allowed(user_id, {"clinician", "admin"}):
        return False
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE emr_screenings SET doctor_notes = ?, updated_at = datetime('now') WHERE screening_id = ?",
            (str(doctor_notes or "").strip() or None, int(screening_id)),
        )
        ok = cur.rowcount > 0
        conn.commit()
        if ok:
            log_emr_action(user_id, "UPDATE_SCREENING_NOTES", "screening", int(screening_id), "")
        return ok
    except sqlite3.Error:
        return False
    finally:
        conn.close()


# ===========================================================================
# Workflow guard-rail helpers
# ---------------------------------------------------------------------------
# Every mutating UI action routes through these so behaviour is enforced in
# one place. Each helper returns (ok: bool, reason: str) so the UI can both
# block and surface an explanation to the user.
# ===========================================================================


def count_visit_screenings(queue_id: int) -> int:
    """How many screening sessions are attached to a given visit (queue entry)."""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM emr_screenings WHERE queue_entry_id = ?",
            (queue_id,),
        )
        r = cur.fetchone()
        return int(r[0] or 0) if r else 0
    finally:
        conn.close()


# Latest session is a failed / redo-safe state: allow a new EMR screening without a second confirmation.
_NEW_SCREENING_WITHOUT_PROMPT_STATUSES = frozenset({"rejected_all"})


def should_prompt_before_new_visit_screening(latest: Optional[dict[str, Any]]) -> bool:
    """
    When a visit already has a screening row, we usually confirm before creating another.

    Skip that prompt when the latest session cannot block a fresh attempt (e.g. all fundus
    images were rejected as ungradable).
    """
    if not latest:
        return False
    status = str(latest.get("session_status") or "").strip().lower()
    if status in _NEW_SCREENING_WITHOUT_PROMPT_STATUSES:
        return False
    return True


def latest_visit_screening(queue_id: int) -> Optional[dict[str, Any]]:
    """Return the most recent screening row attached to the visit (or None)."""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT screening_id, screening_type, screening_date, eye_screened, session_status
            FROM emr_screenings
            WHERE queue_entry_id = ?
            ORDER BY screening_id DESC
            LIMIT 1
            """,
            (queue_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "screening_id": int(row[0]),
            "screening_type": row[1],
            "screening_date": row[2],
            "eye_screened": row[3],
            "session_status": row[4],
        }
    finally:
        conn.close()


def can_create_visit_for_patient(patient_id: int) -> tuple[bool, str]:
    """Front desk guard: reject a second simultaneous active visit for the same patient today."""
    ex = get_today_active_queue_for_patient(patient_id)
    if ex:
        return False, (
            f"Patient already has an active visit today: "
            f"{ex.get('queue_number', '')} (status: {ex.get('status', 'unknown')})."
        )
    return True, ""


def can_cancel_visit(queue_id: int) -> tuple[bool, str]:
    """Front desk guard: only cancellable while visit is open and has no finalised screening."""
    v = get_queue_entry(queue_id)
    if not v:
        return False, "Visit not found."
    status = str(v.get("status") or "").strip().lower()
    if status in ("completed", "cancelled"):
        return False, f"Visit is already {status}; it cannot be cancelled."
    latest = latest_visit_screening(queue_id)
    if latest:
        sess = str(latest.get("session_status") or "").lower()
        if sess in {"pending", "partial", "completed"}:
            return False, "A screening is in progress or already finalized for this visit; it cannot be cancelled."
    return True, ""


def can_complete_visit(queue_id: int) -> tuple[bool, str]:
    """Doctor guard: visit can only be marked completed after a screening session exists."""
    v = get_queue_entry(queue_id)
    if not v:
        return False, "Visit not found."
    status = str(v.get("status") or "").strip().lower()
    if status == "completed":
        return False, "Visit is already marked completed."
    if status == "cancelled":
        return False, "Cancelled visits cannot be marked completed."
    if count_visit_screenings(queue_id) == 0:
        return False, "No screening was recorded for this visit yet. Start a diagnosis first."
    latest = latest_visit_screening(queue_id) or {}
    sess = str(latest.get("session_status") or "").lower()
    if sess == "pending":
        return False, "The screening for this visit is still pending AI/verification. Finish verifying before completing the visit."
    return True, ""


def can_start_screening(patient_id: int, queue_id: Optional[int]) -> tuple[bool, str]:
    """Doctor guard: a screening must be anchored to an open visit for queueing integrity."""
    if not queue_id:
        return False, "No active visit for this patient. Ask the front desk to assign a queue number before diagnosing."
    v = get_queue_entry(queue_id)
    if not v:
        return False, "Visit not found."
    status = str(v.get("status") or "").strip().lower()
    if status in ("completed", "cancelled"):
        return False, f"This visit is {status}; start a new visit before diagnosing."
    if int(v.get("patient_id") or 0) != int(patient_id):
        return False, "Visit does not belong to this patient."
    return True, ""


def mark_visit_in_progress(queue_id: int, user_id: int) -> tuple[bool, str]:
    """Move a waiting visit to in_progress when the doctor opens it."""
    v = get_queue_entry(queue_id)
    if not v:
        return False, "Visit not found."
    status = str(v.get("status") or "").strip().lower()
    if status == "in_progress":
        return True, ""
    if status != "waiting":
        return False, f"Cannot move visit to in-progress from status '{status}'."
    if set_queue_status(queue_id, "in_progress", user_id):
        return True, ""
    return False, "Could not update visit status."


def count_visits_today_for_patient(patient_id: int) -> int:
    """How many visits this patient had today (all statuses). Useful for UI hints."""
    vd = date.today().isoformat()
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM emr_queue_entries WHERE patient_id = ? AND visit_date = ?",
            (patient_id, vd),
        )
        r = cur.fetchone()
        return int(r[0] or 0) if r else 0
    finally:
        conn.close()
