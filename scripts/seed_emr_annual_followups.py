"""
Seed EMR demo data: annual-ish follow-up screening history.

Creates 25 demo patients (5 per severity) with 3–6 screenings each, dated across
the last ~12 months with random intervals. Each visit has emr_visit_details and
each screening has completed per-eye rows (Right/Left randomized).

Run:
  python scripts/seed_emr_annual_followups.py
"""

from __future__ import annotations

import os
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta


def _connect_users_db() -> sqlite3.Connection:
    # Uses the same DB as the app (users.db / USERS_DB_PATH).
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dir = os.path.join(repo_root, "app")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    from auth import get_connection, UserManager

    conn = get_connection()
    UserManager._ensure_emr_schema(conn)
    return conn


def _pick_actor_user_id(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    return int(row[0]) if row else 1


def _severity_label(grade: int) -> str:
    return {0: "No DR", 1: "Mild DR", 2: "Moderate DR", 3: "Severe DR", 4: "Proliferative DR"}.get(int(grade), "No DR")


@dataclass
class SeedPatient:
    patient_code: str
    first_name: str
    last_name: str
    sex: str
    dob: str


def main() -> None:
    random.seed(20260424)
    conn = _connect_users_db()
    try:
        actor_id = _pick_actor_user_id(conn)
        cur = conn.cursor()

        # Create a deterministic pool of patients.
        severities = [0, 1, 2, 3, 4]
        patients: list[tuple[int, int]] = []  # (patient_id, base_grade)
        created_codes: list[str] = []

        for grade in severities:
            for i in range(1, 6):
                code = f"DEMO-{grade}-{i:02d}"
                created_codes.append(code)
                first = f"Demo{_severity_label(grade).split()[0]}{i}"
                last = "Patient"
                sex = random.choice(["Male", "Female"])
                dob = f"{random.randint(1965, 2002)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"

                # Clean any existing prior seed for idempotence.
                cur.execute("DELETE FROM emr_patients WHERE patient_code = ?", (code,))

                cur.execute(
                    """
                    INSERT INTO emr_patients (
                        patient_code, last_name, first_name, date_of_birth, sex, contact_number, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (code, last, first, dob, sex, f"07{random.randint(10000000,99999999)}", actor_id),
                )
                pid = int(cur.lastrowid)
                patients.append((pid, grade))

        conn.commit()

        now = datetime.now().replace(microsecond=0)
        start = now - timedelta(days=365)
        today = now.date().isoformat()

        # For each patient: create 3–6 visits with screenings.
        for (pid, base_grade) in patients:
            # Pull code/name for queue labels.
            cur.execute("SELECT patient_code, first_name, last_name, sex, date_of_birth FROM emr_patients WHERE patient_id = ?", (pid,))
            prow = cur.fetchone()
            if not prow:
                continue
            pcode = str(prow[0])

            # Ensure every demo patient appears in today's queue list (many screens default to today's visit_date).
            # This is separate from historical completed visits below.
            cur.execute(
                """
                INSERT INTO emr_queue_entries (patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes)
                VALUES (?, ?, ?, 'waiting', ?, 'follow_up', ?)
                """,
                (pid, f"Q-DEMO-TODAY-{pcode}", today, actor_id, "seed_today"),
            )

            n_visits = random.randint(3, 6)
            dt = start + timedelta(days=random.randint(0, 45))

            last_grade = base_grade
            for visit_idx in range(n_visits):
                # Advance date by 30–120 days (simulate follow-up cadence)
                if visit_idx > 0:
                    dt = dt + timedelta(days=random.randint(30, 120))
                visit_date = dt.date().isoformat()

                purpose = "new" if visit_idx == 0 else "follow_up"
                cur.execute(
                    """
                    INSERT INTO emr_queue_entries (patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes)
                    VALUES (?, ?, ?, 'completed', ?, ?, ?)
                    """,
                    (pid, f"Q-DEMO-{random.randint(1,999):03d}", visit_date, actor_id, purpose, "seed"),
                )
                qid = int(cur.lastrowid)

                # Visit details (vary per visit)
                height = random.choice([158, 162, 165, 170, 174])
                weight = random.randint(55, 92)
                bmi = round(weight / ((height / 100.0) ** 2), 2)
                bp_sys = random.randint(110, 160)
                bp_dia = random.randint(70, 100)
                fbs = random.randint(85, 200)
                rbs = random.randint(100, 280)
                hba1c = round(random.uniform(5.6, 10.5), 1)
                dm_dur = round(random.uniform(1, 15), 1)

                cur.execute(
                    """
                    INSERT INTO emr_visit_details (
                        queue_id, patient_id, captured_at, captured_by,
                        visual_acuity_left, visual_acuity_right,
                        blood_pressure_systolic, blood_pressure_diastolic,
                        fasting_blood_sugar, random_blood_sugar,
                        diabetes_type, dm_duration_years, hba1c,
                        diabetes_diagnosis_date, treatment_regimen, prev_dr_stage, prev_treatment,
                        symptom_blurred_vision, symptom_floaters, symptom_flashes, symptom_vision_loss, symptom_other,
                        height_cm, weight_kg, bmi, notes
                    ) VALUES (?, ?, ?, ?,
                              ?, ?,
                              ?, ?,
                              ?, ?,
                              ?, ?, ?,
                              ?, ?, ?, ?,
                              ?, ?, ?, ?, ?,
                              ?, ?, ?, ?)
                    """,
                    (
                        qid,
                        pid,
                        dt.strftime("%Y-%m-%d %H:%M:%S"),
                        actor_id,
                        "6/9",
                        "6/12",
                        bp_sys,
                        bp_dia,
                        float(fbs),
                        float(rbs),
                        random.choice(["Type 1", "Type 2"]),
                        float(dm_dur),
                        float(hba1c),
                        (dt - timedelta(days=int(dm_dur * 365))).date().isoformat(),
                        random.choice(["Diet", "Oral meds", "Oral + insulin", "Insulin"]),
                        _severity_label(max(0, min(4, last_grade))),
                        random.choice(["Yes", "No"]),
                        1 if random.random() < 0.25 else 0,
                        1 if random.random() < 0.10 else 0,
                        1 if random.random() < 0.06 else 0,
                        1 if random.random() < 0.04 else 0,
                        "",
                        float(height),
                        float(weight),
                        float(bmi),
                        "seed visit",
                    ),
                )

                # Screening session for this visit.
                screening_type = "initial" if visit_idx == 0 else "follow_up"
                eye_screened = random.choice(["Left", "Right"])

                cur.execute(
                    """
                    INSERT INTO emr_screenings (
                        patient_id, queue_entry_id, performed_by, screening_date,
                        screening_type, eye_screened, session_status, doctor_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?)
                    """,
                    (
                        pid,
                        qid,
                        actor_id,
                        dt.strftime("%Y-%m-%d %H:%M:%S"),
                        screening_type,
                        eye_screened,
                        f"seed screening {visit_idx+1} for {pcode}",
                    ),
                )
                sid = int(cur.lastrowid)

                # Progression logic: mostly stable with occasional worsening/improving.
                if visit_idx == 0:
                    current_grade = base_grade
                else:
                    roll = random.random()
                    if roll < 0.65:
                        current_grade = last_grade  # stable
                    elif roll < 0.85:
                        current_grade = min(4, last_grade + 1)  # worsening
                    else:
                        current_grade = max(0, last_grade - 1)  # improving
                last_grade = current_grade

                ai_conf = round(random.uniform(0.65, 0.95), 3)
                total_unc = round(random.uniform(0.05, 0.35), 3)

                cur.execute(
                    """
                    INSERT INTO emr_screening_eyes (
                        screening_id, eye_side,
                        fundus_image_path, gradcam_image_path,
                        image_quality_status, uncertainty_status,
                        ai_dr_grade, ai_confidence, total_uncertainty,
                        doctor_accepted_ai, final_dr_grade,
                        override_justification, final_treatment_notes
                    ) VALUES (?, ?, ?, ?,
                              'gradable', 'accepted',
                              ?, ?, ?,
                              ?, ?,
                              ?, ?)
                    """,
                    (
                        sid,
                        eye_screened,
                        "",
                        "",
                        int(current_grade),  # ai_dr_grade
                        float(ai_conf),
                        float(total_unc),
                        int(1),  # doctor_accepted_ai
                        int(current_grade),  # final_dr_grade
                        "",  # override_justification
                        f"Final: {_severity_label(current_grade)}",
                    ),
                )

        conn.commit()
        print("Seeded EMR demo patients.")
        print("Created patient codes:", ", ".join(created_codes[:5]), "... (total 25)")
        print("Open EMR queue / screening history to browse seeded annual follow-ups.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

