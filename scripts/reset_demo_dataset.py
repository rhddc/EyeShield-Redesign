"""
Reset local demo dataset.

- Deletes all legacy `patient_records` rows.
- Deletes all EMR patients/queue/screenings (emr_*).
- Seeds 10 demo patients and 10 demo `patient_records` rows with a requested DR distribution.

Run:
  python scripts/reset_demo_dataset.py
"""

from __future__ import annotations

from datetime import datetime, timedelta
import os
import sys

# Allow running as a standalone script from repo root.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(REPO_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import auth  # noqa: E402
import emr_service as emr  # noqa: E402


DR_DISTRIBUTION: list[str] = (
    ["No DR"] * 4  # at least 10 patients total; user asked 3 but we keep 4 to reach 10
    + ["Mild DR"] * 2
    + ["Moderate DR"] * 2
    + ["Severe DR"] * 1
    + ["Proliferative DR"] * 1
)


def _get_admin_user_id(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE lower(role) = 'admin' ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    if row:
        return int(row[0])
    # Fallback to first user.
    cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    return int(row[0]) if row else 1


def main() -> None:
    conn = auth.get_connection()
    try:
        # Ensure schema exists.
        auth.UserManager._ensure_emr_schema(conn)
        auth.UserManager._ensure_patient_record_columns(conn)

        admin_id = _get_admin_user_id(conn)
        cur = conn.cursor()

        # --- Clear legacy records (Patient Records page) ---
        cur.execute("DELETE FROM patient_records")

        # --- Clear EMR tables (Queue/Visits/Patients) ---
        # Order matters with foreign keys.
        for tbl in (
            "emr_screening_eyes",
            "emr_screenings",
            "emr_queue_entries",
            "emr_visit_details",
            "emr_patients",
        ):
            try:
                cur.execute(f"DELETE FROM {tbl}")
            except Exception:
                # Some deployments may not have all tables.
                pass

        conn.commit()
    finally:
        conn.close()

    # Seed EMR patients via service API (creates audit logs).
    seeded_patients: list[dict] = []
    base_dob = datetime(1975, 1, 1)
    for i in range(10):
        ln = f"Demo{i+1:02d}"
        fn = f"Patient{i+1:02d}"
        dob = (base_dob + timedelta(days=i * 365)).strftime("%Y-%m-%d")
        pid_pk = emr.create_patient(
            admin_id,
            last_name=ln,
            first_name=fn,
            date_of_birth=dob,
            sex="Male" if i % 2 == 0 else "Female",
            contact_number=f"+10000000{i:02d}",
        )
        p = emr.get_patient(pid_pk) or {}
        seeded_patients.append(p)

    # Seed legacy `patient_records` rows so the Patient Records page has demo content.
    conn = auth.get_connection()
    try:
        cur = conn.cursor()
        now = datetime.now()
        for i, (p, grade) in enumerate(zip(seeded_patients, DR_DISTRIBUTION, strict=True)):
            patient_code = str(p.get("patient_code") or f"EYS-DEMO-{i+1:03d}")
            name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
            screened_at = (now - timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                INSERT INTO patient_records (
                    patient_id, name, birthdate, age, sex, contact, eyes,
                    result, confidence, screened_at,
                    ai_classification, doctor_classification, final_diagnosis_icdr, decision_mode,
                    original_screener_username, original_screener_name,
                    archived_at, archived_by, archive_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    patient_code,
                    name,
                    str(p.get("date_of_birth") or ""),
                    str(p.get("age") or ""),
                    str(p.get("sex") or ""),
                    str(p.get("contact_number") or ""),
                    "Both",
                    grade,
                    "0.95",
                    screened_at,
                    grade,
                    grade,
                    grade,
                    "AI",
                    "demo",
                    "Demo Screener",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    print("Done. Seeded 10 demo patients + 10 demo patient records.")


if __name__ == "__main__":
    main()

