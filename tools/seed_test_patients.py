from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class SeedPatient:
    code: str
    first_name: str
    last_name: str
    dob: str  # YYYY-MM-DD
    sex: str
    contact: str
    diabetes_type: str


SEVERITY_LABEL = {
    0: "No DR",
    1: "Mild DR",
    2: "Moderate DR",
    3: "Severe DR",
    4: "Proliferative DR",
}


def _age_from_dob(dob: str) -> int | None:
    try:
        born = datetime.strptime(dob[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _now_text(dt: datetime) -> str:
    return dt.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _make_demo_images(*, patient_code: str, side: str, when: datetime, severity_label: str) -> tuple[str, str, str, str]:
    """
    Create simple demo fundus + heatmap images on disk.

    Returns:
    - source_rel (relative to app/): stored_images/<code>/<filename>.jpg
    - heatmap_rel (relative to app/): stored_images/<code>/<filename>.png
    - source_abs
    - heatmap_abs
    """
    # Keep paths compatible with app UI (it resolves relative paths from app/ folder).
    app_dir = Path(__file__).resolve().parents[1] / "app"
    out_dir = app_dir / "stored_images" / patient_code
    _ensure_dir(out_dir)

    ts = when.strftime("%Y%m%d_%H%M%S")
    safe_side = "left" if str(side).lower().startswith("l") else "right"
    base = f"{ts}_{safe_side}_eye"
    source_abs = out_dir / f"{base}_source.jpg"
    heat_abs = out_dir / f"{base}_heatmap.png"

    try:
        from PIL import Image, ImageDraw, ImageFilter  # type: ignore
    except Exception:
        # If Pillow is unavailable, just leave empty paths; UI will fall back.
        return "", "", "", ""

    w, h = 640, 480
    img = Image.new("RGB", (w, h), (15, 23, 42))
    draw = ImageDraw.Draw(img)
    # Fake "retina" gradient blob
    cx, cy = w // 2, h // 2
    r = min(w, h) // 3
    for rr in range(r, 0, -6):
        c = int(30 + (r - rr) * 0.9)
        draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(c, c + 10, c + 25), width=3)
    # Label
    meta = f"{patient_code}  |  {side}  |  {severity_label}\n{when.strftime('%Y-%m-%d %H:%M:%S')}"
    draw.rectangle((16, 16, w - 16, 70), fill=(255, 255, 255))
    draw.text((24, 22), meta, fill=(10, 10, 10))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.3))
    img.save(source_abs, format="JPEG", quality=88)

    # Heatmap: simple red overlay
    heat = img.copy().convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (220, 38, 38, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse((cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2), fill=(220, 38, 38, 90))
    od.ellipse((cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3), fill=(249, 115, 22, 120))
    heat = Image.alpha_composite(heat, overlay)
    heat.save(heat_abs, format="PNG")

    source_rel = str(Path("stored_images") / patient_code / source_abs.name).replace("\\", "/")
    heat_rel = str(Path("stored_images") / patient_code / heat_abs.name).replace("\\", "/")
    return source_rel, heat_rel, str(source_abs), str(heat_abs)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # Import via sys.path hack so this file can be executed from repo root.
    import sys
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    sys.path.insert(0, str(app_dir))
    import auth  # type: ignore

    auth.UserManager._init_db()


def _ensure_patient_records_db_schema(conn: sqlite3.Connection) -> None:
    """
    patient_records.db is the legacy DB used by Dashboard / Reports / Patient Records list.
    Keep its schema aligned with ScreeningPage._ensure_patient_records_schema().
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
    # Add newer columns used by reports/dashboard (best-effort, idempotent).
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


def seed(*, n_patients: int = 10, seed_value: int = 20260424) -> dict[str, int]:
    """
    Seed 10 unique patients, each with multiple screening history + severities.

    Writes to:
    - data/users.db: EMR tables (emr_*)
    - data/patient_records.db: legacy patient_records (Dashboard / Reports / Patient Records list)
    """
    _ensure_schema(sqlite3.connect("data/users.db"))

    conn = sqlite3.connect("data/users.db")
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    legacy_conn = sqlite3.connect("data/patient_records.db")
    legacy_cur = legacy_conn.cursor()
    _ensure_patient_records_db_schema(legacy_conn)

    rng = random.Random(int(seed_value))

    # Pick a clinician user_id to satisfy FK constraints.
    cur.execute("SELECT id FROM users WHERE role = 'clinician' AND is_active = 1 ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("No active clinician user found. Create/activate a clinician account first.")
    clinician_id = int(row[0])
    cur.execute(
        "SELECT username, COALESCE(display_name, full_name, username) FROM users WHERE id = ? LIMIT 1",
        (clinician_id,),
    )
    cprof = cur.fetchone() or ("clinician", "Clinician")
    clinician_username = str(cprof[0] or "clinician").strip() or "clinician"
    clinician_name = str(cprof[1] or clinician_username).strip() or clinician_username

    cur.execute("SELECT id FROM users WHERE role = 'frontdesk' AND is_active = 1 ORDER BY id LIMIT 1")
    row = cur.fetchone()
    frontdesk_id = int(row[0]) if row else clinician_id

    # Generate stable but unique patient identities.
    first_names = ["Ava", "Noah", "Liam", "Mia", "Ethan", "Sofia", "Lucas", "Isla", "Zoe", "Owen", "Kai", "Nina"]
    last_names = ["Reyes", "Santos", "Cruz", "Garcia", "Torres", "Flores", "Navarro", "Mendoza", "Lopez", "Ramos"]

    patients: list[SeedPatient] = []
    used = set()
    base = date.today() - timedelta(days=365 * 45)
    while len(patients) < int(n_patients):
        fn = rng.choice(first_names)
        ln = rng.choice(last_names)
        key = (fn, ln)
        if key in used:
            continue
        used.add(key)
        dob = (base + timedelta(days=rng.randint(0, 365 * 25))).strftime("%Y-%m-%d")
        sex = rng.choice(["Male", "Female"])
        contact = f"09{rng.randint(100000000, 999999999)}"
        dm = rng.choice(["Type 1", "Type 2"])
        code = f"EYS-{date.today().year}-{len(patients)+1:05d}"
        patients.append(SeedPatient(code=code, first_name=fn, last_name=ln, dob=dob, sex=sex, contact=contact, diabetes_type=dm))

    # Remove any existing seeded patients by code to keep idempotent-ish.
    for p in patients:
        cur.execute("DELETE FROM emr_patients WHERE patient_code = ?", (p.code,))
        legacy_cur.execute("DELETE FROM patient_records WHERE patient_id = ?", (p.code,))

    created_patients = 0
    created_visits = 0
    created_screenings = 0
    created_patient_records = 0

    # Distribute severities; each patient gets multiple screenings (2-4).
    severity_pool = [0, 0, 1, 1, 2, 2, 3, 4]

    now = datetime.now()
    for idx, p in enumerate(patients):
        age = _age_from_dob(p.dob)
        cur.execute(
            """
            INSERT INTO emr_patients (
                patient_code, last_name, first_name, middle_name, date_of_birth, age, sex,
                contact_number, email, address, height_cm, weight_kg, bmi,
                diabetes_type, dm_duration_years, hba1c,
                current_medications, known_allergies, other_conditions,
                current_eye_treatment, previous_eye_treatment, last_eye_exam_date,
                created_by
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?)
            """,
            (
                p.code,
                p.last_name,
                p.first_name,
                p.dob,
                age,
                p.sex,
                p.contact,
                p.diabetes_type,
                clinician_id,
            ),
        )
        patient_id = int(cur.lastrowid)
        created_patients += 1

        n_screenings = rng.randint(2, 4)
        # Screenings spread over last 120 days.
        screening_dates = sorted({rng.randint(2, 120) for _ in range(n_screenings)})
        if len(screening_dates) < n_screenings:
            screening_dates = sorted(list(screening_dates) + [rng.randint(2, 120) for _ in range(n_screenings - len(screening_dates))])

        for s_i in range(n_screenings):
            days_ago = screening_dates[s_i]
            s_dt = now - timedelta(days=int(days_ago), hours=rng.randint(0, 8), minutes=rng.randint(0, 59))
            visit_date = s_dt.strftime("%Y-%m-%d")
            queue_number = f"Q-{(idx+1):03d}-{s_i+1}"
            cur.execute(
                """
                INSERT INTO emr_queue_entries
                (patient_id, queue_number, visit_date, status, assigned_by, screening_purpose, notes, created_at, updated_at)
                VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)
                """,
                (
                    patient_id,
                    queue_number,
                    visit_date,
                    frontdesk_id,
                    "follow_up" if s_i > 0 else "new",
                    "Seeded visit",
                    _now_text(s_dt),
                    _now_text(s_dt),
                ),
            )
            queue_id = int(cur.lastrowid)
            created_visits += 1

            eye_screened = rng.choice(["Left", "Right", "Both"])
            cur.execute(
                """
                INSERT INTO emr_screenings
                (patient_id, queue_entry_id, performed_by, screening_date, screening_type, eye_screened, session_status, doctor_notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?)
                """,
                (
                    patient_id,
                    queue_id,
                    clinician_id,
                    _now_text(s_dt),
                    "follow_up" if s_i > 0 else "initial",
                    eye_screened,
                    "Seeded screening",
                    _now_text(s_dt),
                    _now_text(s_dt),
                ),
            )
            screening_id = int(cur.lastrowid)
            created_screenings += 1

            sides = ["Left", "Right"] if eye_screened == "Both" else [eye_screened]

            # Make later screenings trend slightly worse for realism.
            base_grade = rng.choice(severity_pool)
            drift = 1 if (s_i == n_screenings - 1 and rng.random() < 0.35) else 0
            for side in sides:
                final_grade = min(4, max(0, int(base_grade + drift + rng.choice([-1, 0, 0, 1]))))
                ai_grade = min(4, max(0, final_grade + rng.choice([-1, 0, 0, 1])))
                conf = rng.uniform(0.78, 0.98)
                total_unc = rng.uniform(0.02, 0.18)
                severity_label = SEVERITY_LABEL.get(final_grade, "") or "Pending"
                src_rel, heat_rel, src_abs, heat_abs = _make_demo_images(
                    patient_code=p.code,
                    side=side,
                    when=s_dt,
                    severity_label=severity_label,
                )
                cur.execute(
                    """
                    INSERT INTO emr_screening_eyes
                    (screening_id, eye_side, fundus_image_path, gradcam_image_path,
                     image_quality_status, quality_rejection_reason, blur_score, illumination_score, entropy_score,
                     ai_dr_grade, ai_confidence, aleatoric_uncertainty, epistemic_uncertainty, total_uncertainty,
                     uncertainty_status, heatmap_generated, ai_treatment_suggestion,
                     doctor_accepted_ai, final_dr_grade, override_justification, final_treatment_notes,
                     created_at, updated_at)
                    VALUES
                    (?, ?, ?, ?,
                     'gradable', NULL, NULL, NULL, NULL,
                     ?, ?, NULL, NULL, ?,
                     'accepted', 0, NULL,
                     1, ?, NULL, NULL,
                     ?, ?)
                    """,
                    (
                        screening_id,
                        side,
                        src_abs,
                        heat_abs,
                        ai_grade,
                        float(conf),
                        float(total_unc),
                        final_grade,
                        _now_text(s_dt),
                        _now_text(s_dt),
                    ),
                )

                # Also seed legacy patient_records so dashboard & reports have rows immediately.
                # One row per eye (matches how the legacy UI was built).
                result_label = severity_label
                legacy_cur.execute(
                    """
                    INSERT INTO patient_records
                    (patient_id, name, birthdate, age, sex, contact, eyes,
                     diabetes_type, duration, hba1c, prev_treatment, notes, result, confidence,
                     archived_at, archived_by, archive_reason,
                     screened_at, source_image_path, heatmap_image_path,
                     ai_classification, doctor_classification, decision_mode,
                     final_diagnosis_icdr, doctor_findings, screening_type, screening_group_id,
                     original_screener_username, original_screener_name)
                    VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, '', '', '', 'Seeded record', ?, ?, NULL, NULL, NULL,
                     ?, ?, ?, ?, ?, 'accepted', ?, '', ?, ?, ?, ?)
                    """,
                    (
                        p.code,
                        f"{p.first_name} {p.last_name}",
                        p.dob,
                        str(age or ""),
                        p.sex,
                        p.contact,
                        f"{side} Eye",
                        p.diabetes_type,
                        result_label,
                        f"Confidence: {conf*100:.1f}% | Uncertainty: {total_unc*100:.1f}%",
                        _now_text(s_dt),
                        src_rel,
                        heat_rel,
                        result_label,
                        result_label,
                        result_label,
                        "follow_up" if s_i > 0 else "initial",
                        f"queue-{queue_id}",
                        clinician_username,
                        clinician_name,
                    ),
                )
                created_patient_records += 1

            # Vitals / visit details (optional but helps UI look realistic)
            cur.execute(
                """
                INSERT OR REPLACE INTO emr_visit_details
                (queue_id, patient_id, captured_at, captured_by,
                 blood_pressure_systolic, blood_pressure_diastolic,
                 fasting_blood_sugar, random_blood_sugar,
                 diabetes_type, dm_duration_years, hba1c, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    queue_id,
                    patient_id,
                    _now_text(s_dt),
                    clinician_id,
                    rng.randint(110, 160),
                    rng.randint(70, 100),
                    round(rng.uniform(4.8, 9.8), 1),
                    round(rng.uniform(5.8, 13.5), 1),
                    p.diabetes_type,
                    round(rng.uniform(1.0, 18.0), 1),
                    round(rng.uniform(5.4, 10.8), 1),
                    "Seeded vitals",
                    _now_text(s_dt),
                    _now_text(s_dt),
                ),
            )

    conn.commit()
    conn.close()
    legacy_conn.commit()
    legacy_conn.close()

    return {
        "patients": created_patients,
        "visits": created_visits,
        "screenings": created_screenings,
        "patient_records_rows": created_patient_records,
    }


if __name__ == "__main__":
    result = seed()
    print(result)

