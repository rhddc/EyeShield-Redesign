"""
Seed legacy `patient_records` (Patient Records screen) with 25 demo patients:
5 per severity (No/Mild/Moderate/Severe/Proliferative) with follow-up history
spanning the last ~12 months.

This is for the **Patient Records** list (reports/timeline), not the EMR queue.

Run:
  python scripts/seed_patient_records_annual_followups.py
"""

from __future__ import annotations

import os
import random
import sqlite3
import hashlib
from datetime import datetime, timedelta


def _severity_label(grade: int) -> str:
    return {
        0: "No DR",
        1: "Mild DR",
        2: "Moderate DR",
        3: "Severe DR",
        4: "Proliferative DR",
    }.get(int(grade), "No DR")


def _db_path() -> str:
    # Use the same DB path as the app Reports page.
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dir = os.path.join(repo_root, "app")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    from app_paths import PATIENT_RECORDS_DB_PATH

    return str(PATIENT_RECORDS_DB_PATH)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # Create base table and run the app's migration helper to add new columns.
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dir = os.path.join(repo_root, "app")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    from auth import UserManager

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


def main() -> None:
    random.seed(20260424)
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        _ensure_schema(conn)
        cur = conn.cursor()

        # Delete prior demo rows (idempotent).
        cur.execute("DELETE FROM patient_records WHERE patient_id LIKE 'PR-DEMO-%'")
        conn.commit()

        cur.execute("PRAGMA table_info(patient_records)")
        cols = [r[1] for r in cur.fetchall()]
        colset = set(cols)

        now = datetime.now().replace(microsecond=0)
        start = now - timedelta(days=365)

        # Build a pool of real fundus images from app/stored_images.
        # We store paths the same way ScreeningPage does: relative to app/ directory (e.g. stored_images/...).
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app_root = os.path.join(repo_root, "app")
        stored_root = os.path.join(app_root, "stored_images")
        pairs: list[tuple[str, str]] = []
        if os.path.isdir(stored_root):
            for root, _dirs, files in os.walk(stored_root):
                for f in files:
                    lf = f.lower()
                    if "_source" not in lf:
                        continue
                    if not (lf.endswith(".jpg") or lf.endswith(".jpeg") or lf.endswith(".png")):
                        continue
                    src_abs = os.path.join(root, f)
                    # Skip pending/unlinked camera dumps to keep data clean.
                    if os.sep + "pending" + os.sep in src_abs.lower() or os.sep + "unlinked" + os.sep in src_abs.lower():
                        continue
                    base = lf.replace("_source.jpg", "").replace("_source.jpeg", "").replace("_source.png", "")
                    # Find a matching heatmap in same folder.
                    heat = ""
                    for ext in (".png", ".jpg", ".jpeg"):
                        cand = os.path.join(root, base + "_heatmap" + ext)
                        if os.path.isfile(cand):
                            heat = cand
                            break
                    rel_src = os.path.relpath(src_abs, app_root).replace("\\", "/")
                    rel_heat = os.path.relpath(heat, app_root).replace("\\", "/") if heat else ""
                    pairs.append((rel_src, rel_heat))
        random.shuffle(pairs)

        first_names = [
            "Amina", "Noah", "Fatima", "Ethan", "Liam",
            "Sophia", "Olivia", "Mia", "James", "Daniel",
            "Grace", "Hannah", "Leah", "Ivy", "Zara",
        ]
        last_names = ["Dela Cruz", "Santos", "Reyes", "Garcia", "Lopez", "Torres", "Flores", "Ramos", "Rivera"]

        def insert_row(payload: dict) -> int:
            # Only insert columns that exist in this DB.
            keys = [k for k in payload.keys() if k in colset]
            vals = [payload[k] for k in keys]
            qs = ", ".join(["?"] * len(keys))
            cur.execute(f"INSERT INTO patient_records ({', '.join(keys)}) VALUES ({qs})", vals)
            return int(cur.lastrowid)

        for grade in range(5):
            for i in range(1, 6):
                patient_id = f"PR-DEMO-{grade}-{i:02d}"
                name = f"{random.choice(first_names)} {random.choice(last_names)}"
                sex = random.choice(["Male", "Female"])
                dob_year = random.randint(1965, 2002)
                dob = f"{dob_year}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
                age = str(max(0, now.year - dob_year))
                contact = f"09{random.randint(100000000, 999999999)}"

                # Option A: exactly 3 screenings per patient so every row can use a unique
                # real fundus image from the repo image pool (no reuse).
                n = 3
                dt = start + timedelta(days=random.randint(0, 30))
                previous_id: int | None = None

                for k in range(n):
                    if k > 0:
                        # Wider spacing to simulate annual-ish follow-ups.
                        dt = dt + timedelta(days=random.randint(120, 200))
                    screened_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                    screening_type = "initial" if k == 0 else "follow_up"

                    # Make latest screening exactly match the bucket severity.
                    if k == n - 1:
                        final_grade = grade
                    else:
                        # earlier ones wobble around (mostly stable)
                        roll = random.random()
                        if roll < 0.65:
                            final_grade = grade
                        elif roll < 0.85:
                            final_grade = min(4, grade + 1)
                        else:
                            final_grade = max(0, grade - 1)

                    result = _severity_label(final_grade)
                    eye = random.choice(["Right Eye", "Left Eye"])

                    # Unique group per screening (prevents accidental grouping collisions).
                    group_id = f"{patient_id}-{dt.strftime('%Y%m%d%H%M%S')}-{k:02d}"
                    h = random.choice([158, 162, 165, 170, 174])
                    w = random.randint(55, 92)
                    bmi = round(w / ((h / 100.0) ** 2), 1)

                    bp_sys = random.randint(110, 160)
                    bp_dia = random.randint(70, 100)
                    fbs = random.randint(85, 200)
                    rbs = random.randint(100, 280)
                    hba1c = f"{round(random.uniform(5.6, 10.5), 1)}%"
                    dm_type = random.choice(["Type 1", "Type 2"])

                    # Assign a real fundus + heatmap pair (unique per screening row if possible).
                    rel_src = ""
                    rel_heat = ""
                    image_sha256 = ""
                    image_saved_at = screened_at
                    if pairs:
                        rel_src, rel_heat = pairs.pop(0)
                        # Compute sha256 from actual file bytes.
                        abs_src = os.path.join(app_root, rel_src.replace("/", os.sep))
                        try:
                            hasher = hashlib.sha256()
                            with open(abs_src, "rb") as fp:
                                for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                                    hasher.update(chunk)
                            image_sha256 = hasher.hexdigest()
                        except OSError:
                            image_sha256 = ""

                    row = {
                        "patient_id": patient_id,
                        "name": name,
                        "birthdate": dob,
                        "age": age,
                        "sex": sex,
                        "contact": contact,
                        "eyes": eye,
                        "diabetes_type": dm_type,
                        "duration": str(round(random.uniform(1, 15), 1)),
                        "hba1c": hba1c,
                        "prev_treatment": random.choice(["Yes", "No"]),
                        "notes": "Seeded demo patient record.",
                        "result": result,
                        "confidence": f"Confidence: {round(random.uniform(70, 95), 1)}% | Uncertainty: {round(random.uniform(5, 35), 1)}%",
                        "screened_at": screened_at,
                        "ai_classification": result,
                        "doctor_classification": result,
                        "decision_mode": "accepted",
                        "final_diagnosis_icdr": result,
                        "doctor_findings": f"Seeded follow-up {k+1}/{n}.",
                        "decision_by_username": "demo.clinician",
                        "decision_at": screened_at,
                        "visual_acuity_left": "6/9",
                        "visual_acuity_right": "6/12",
                        "blood_pressure_systolic": str(bp_sys),
                        "blood_pressure_diastolic": str(bp_dia),
                        "fasting_blood_sugar": str(fbs),
                        "random_blood_sugar": str(rbs),
                        "source_image_path": rel_src,
                        "heatmap_image_path": rel_heat,
                        "image_sha256": image_sha256,
                        "image_saved_at": image_saved_at,
                        "height": str(h),
                        "weight": str(w),
                        "bmi": str(bmi),
                        "treatment_regimen": random.choice(["Diet", "Oral meds", "Oral + insulin", "Insulin"]),
                        "prev_dr_stage": _severity_label(grade),
                        "follow_up": "Yes" if screening_type == "follow_up" else "",
                        "followup_date": screened_at if screening_type == "follow_up" else "",
                        "followup_label": "Follow-up screening" if screening_type == "follow_up" else "",
                        "screening_type": screening_type,
                        "previous_screening_id": previous_id if previous_id else "",
                        "screening_group_id": group_id,
                        "archived_at": None,
                    }
                    rid = insert_row(row)
                    previous_id = rid

        conn.commit()
        print(f"Seeded 25 demo patients into patient_records: {path}")
        if not pairs:
            print("Note: fundus image pool was exhausted; if you need every row to have a unique image, add more images under app/stored_images/.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

