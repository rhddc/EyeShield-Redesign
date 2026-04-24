from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DB = PROJECT_ROOT / "data" / "users.db"
APP_DIR = PROJECT_ROOT / "app"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {str(r[1]) for r in cur.fetchall()}


def _pick_existing(*candidates: str) -> str:
    for raw in candidates:
        if not raw:
            continue
        p = (PROJECT_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        if p.exists():
            # Store relative-to-app path so PatientTimelineDialog can resolve it.
            try:
                rel = p.relative_to(APP_DIR)
                return str(rel).replace("\\", "/")
            except ValueError:
                return str(p)
    return ""


@dataclass(frozen=True)
class SeedPatient:
    patient_id: str
    name: str
    birthdate: str
    sex: str


def reset_and_seed() -> None:
    if not DATA_DB.exists():
        raise SystemExit(f"DB not found at: {DATA_DB}")

    conn = sqlite3.connect(DATA_DB)
    try:
        cur = conn.cursor()
        cols = _table_columns(conn, "patient_records")

        cur.execute("DELETE FROM patient_records")

        # Real image files already in repo (relative to app/...)
        # Using a small set is fine; preview/zoom needs valid paths.
        sources = [
            "stored_images/ES-260403-2UDPE/20260403_012700_left_eye_source.jpg",
            "stored_images/ES-260403-2UDPE/20260403_012109_right_eye_source.jpg",
            "stored_images/ES-260329-W5PWP/20260329_230135_left_eye_source.jpg",
            "stored_images/ES-260329-W5PWP/20260329_235131_right_eye_source.jpg",
            "stored_images/ES-260402-KXKBD/20260402_232953_left_eye_source.png",
        ]
        heatmaps = [
            "stored_images/ES-260403-2UDPE/20260403_012700_left_eye_heatmap.png",
            "stored_images/ES-260403-2UDPE/20260403_012109_right_eye_heatmap.png",
            "stored_images/ES-260329-W5PWP/20260329_230135_left_eye_heatmap.png",
            "stored_images/ES-260329-W5PWP/20260329_235131_right_eye_heatmap.png",
            "stored_images/ES-260402-KXKBD/20260402_232953_left_eye_heatmap.png",
        ]
        # Ensure the paths actually exist in this workspace.
        sources = [p for p in sources if (APP_DIR / p).exists()]
        heatmaps = [p for p in heatmaps if (APP_DIR / p).exists()]

        patients = [
            SeedPatient("TP-0001", "Test Patient One", "1988-06-12", "Male"),
            SeedPatient("TP-0002", "Test Patient Two", "1975-02-03", "Female"),
            SeedPatient("TP-0003", "Test Patient Three", "1992-11-27", "Other"),
            SeedPatient("TP-0004", "Test Patient Four", "1969-09-15", "Female"),
        ]

        screener_pool = [
            ("tester", "Test Clinician"),
            ("clinician1", "Clinician One"),
            ("clinician2", "Clinician Two"),
        ]
        # Progression patterns (oldest -> newest). DR isn't curable; records can be stable or worsen.
        trajectories: dict[str, list[str]] = {
            # Stable cases
            "stable_no_dr": ["No DR", "No DR", "No DR", "No DR"],
            "stable_mild": ["Mild DR", "Mild DR", "Mild DR", "Mild DR"],
            # Slow progression
            "slow_progression": ["No DR", "No DR", "Mild DR", "Mild DR"],
            # Clear worsening over time
            "worsening": ["Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"],
        }

        now = datetime.now().replace(microsecond=0)

        inserts = 0
        patient_trajectory = {
            "TP-0001": trajectories["stable_no_dr"],
            "TP-0002": trajectories["slow_progression"],
            "TP-0003": trajectories["worsening"],
            "TP-0004": trajectories["stable_mild"],
        }

        def _rank(sev: str) -> int:
            sev = str(sev or "").strip().lower()
            if "proliferative" in sev:
                return 4
            if "severe" in sev:
                return 3
            if "moderate" in sev:
                return 2
            if "mild" in sev:
                return 1
            if "no" in sev:
                return 0
            return 0

        for p in patients:
            prev_id: int | None = None
            series = patient_trajectory.get(p.patient_id, trajectories["slow_progression"])
            for j in range(4):
                # Newest record is j=0; oldest record is j=3.
                screened_at = (now - timedelta(days=(j * 14 + random.randint(0, 3)))).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                # Oldest -> newest mapping for severity progression.
                severity = series[max(0, min(3, 3 - j))]
                rank = _rank(severity)
                # Confidence tends to drop as severity increases (and uncertainty rises).
                conf = random.randint(90, 96) if rank <= 0 else random.randint(80, 92) if rank <= 2 else random.randint(72, 88)
                unc = 100 - conf
                scr_user, scr_name = random.choice(screener_pool)

                src = random.choice(sources) if sources else ""
                hm = random.choice(heatmaps) if heatmaps else ""

                payload = {
                    "patient_id": p.patient_id,
                    "name": p.name,
                    "birthdate": p.birthdate,
                    "age": str(max(0, int(now.strftime("%Y")) - int(p.birthdate.split("-")[0]))),
                    "sex": p.sex,
                    "contact": "0000000000",
                    "eyes": "Left" if j % 2 == 0 else "Right",
                    "diabetes_type": "Type 2",
                    "duration": f"{5 + (3 - j)} years",
                    "hba1c": f"{6.6 + ((3 - j) * 0.2):.1f}",
                    "prev_treatment": "Metformin",
                    "notes": "Seeded test history row (progression demo)",
                    "result": severity,
                    "confidence": f"confidence: {conf}% uncertainty: {unc}%",
                    "screened_at": screened_at,
                    "archived_at": None,
                    "archived_by": None,
                    "archive_reason": None,
                    "original_screener_username": scr_user,
                    "original_screener_name": scr_name,
                    "doctor_findings": (
                        "Stable findings; continue regular follow-up."
                        if series in (trajectories["stable_no_dr"], trajectories["stable_mild"])
                        else "Progression noted; reinforce glycemic/BP control and follow-up plan."
                        if series == trajectories["slow_progression"]
                        else "Worsening DR; urgent referral and close follow-up recommended."
                    ),
                    "source_image_path": src,
                    "heatmap_image_path": hm,
                    "screening_type": "initial" if j == 0 else "follow_up",
                    "previous_screening_id": prev_id,
                    "follow_up": "1" if j > 0 else "0",
                    "followup_date": "",
                    "followup_label": "",
                }

                usable = {k: v for k, v in payload.items() if k in cols}
                if not usable:
                    continue

                columns = ", ".join(usable.keys())
                placeholders = ", ".join(["?"] * len(usable))
                cur.execute(
                    f"INSERT INTO patient_records ({columns}) VALUES ({placeholders})",
                    list(usable.values()),
                )
                prev_id = int(cur.lastrowid)
                inserts += 1

        conn.commit()
        print(f"Reset complete. Inserted {inserts} test patient_records rows into {DATA_DB}.")
        print("Test patient IDs:", ", ".join(p.patient_id for p in patients))
    finally:
        conn.close()


if __name__ == "__main__":
    reset_and_seed()
