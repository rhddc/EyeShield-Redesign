from __future__ import annotations

import os
import random
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
STORED = APP_DIR / "stored_images"

SEED_PATIENT_PREFIX = "EYS-"


@dataclass(frozen=True)
class Asset:
    patient_folder: str
    side: str  # Left/Right
    source_abs: str
    heat_abs: str
    source_rel: str
    heat_rel: str


_SIDE_RE = re.compile(r"_(left|right)_eye_", re.I)


def _side_from_name(name: str) -> str:
    m = _SIDE_RE.search(str(name or ""))
    if not m:
        return ""
    return "Left" if m.group(1).lower() == "left" else "Right"


def _rel_from_abs(abs_path: Path) -> str:
    rel = abs_path.resolve().relative_to(APP_DIR.resolve())
    return str(rel).replace("\\", "/")


def _is_real_folder(folder_name: str) -> bool:
    """
    Heuristic:
    - Exclude seeded folders (EYS-*)
    - Exclude pending / UNLINKED
    Everything else inside stored_images counts as "real pool".
    """
    f = str(folder_name or "").strip()
    if not f:
        return False
    lower = f.lower()
    if lower.startswith(SEED_PATIENT_PREFIX.lower()):
        return False
    if lower in {"pending", "unlinked"}:
        return False
    return True


def _collect_real_assets() -> list[Asset]:
    if not STORED.exists():
        return []

    assets: list[Asset] = []
    for src in STORED.rglob("*_source.*"):
        if not src.is_file():
            continue
        folder = src.parent.name
        if not _is_real_folder(folder):
            continue
        side = _side_from_name(src.name)
        if not side:
            continue

        # Dedicated heatmap pairing (same base filename).
        base = src.name
        heat_candidates = []
        for ext in (".png", ".jpg", ".jpeg"):
            heat_candidates.append(src.with_name(_SIDE_RE.sub(lambda m: f"_{m.group(1).lower()}_eye_", base).replace("_source", "_heatmap")).with_suffix(ext))
        # Also handle the common pattern: just replace "_source." with "_heatmap." keeping extension.
        heat_candidates.append(src.with_name(src.name.replace("_source.", "_heatmap.")))

        heat = next((h for h in heat_candidates if h.exists()), None)
        if heat is None:
            continue  # enforce dedicated heatmap presence
        heat_abs = str(heat.resolve())
        heat_rel = _rel_from_abs(heat)

        assets.append(
            Asset(
                patient_folder=folder,
                side=side,
                source_abs=str(src.resolve()),
                heat_abs=heat_abs,
                source_rel=_rel_from_abs(src),
                heat_rel=heat_rel,
            )
        )
    return assets


def _ensure_cols(conn: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    existing = {r[1] for r in cur.fetchall()}
    for name, typ in cols.items():
        if name in existing:
            continue
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    conn.commit()


def main(seed_value: int = 20260424) -> None:
    real_assets = _collect_real_assets()
    if not real_assets:
        print("No REAL assets found in app/stored_images (excluding EYS-*, pending, UNLINKED).")
        return

    by_side = {
        "Left": [a for a in real_assets if a.side == "Left"],
        "Right": [a for a in real_assets if a.side == "Right"],
    }
    # Fallback pools if one side is empty.
    all_pool = list(real_assets)

    rng = random.Random(int(seed_value))

    # Update legacy patient_records.db
    legacy = sqlite3.connect(os.path.join("data", "patient_records.db"))
    try:
        _ensure_cols(
            legacy,
            "patient_records",
            {
                "source_image_path": "TEXT",
                "heatmap_image_path": "TEXT",
            },
        )
        cur = legacy.cursor()
        cur.execute("SELECT id, patient_id, eyes FROM patient_records WHERE patient_id LIKE ?", (f"{SEED_PATIENT_PREFIX}%",))
        rows = cur.fetchall()
        updated = 0
        for rid, pid, eyes in rows:
            eye_text = str(eyes or "").lower()
            side = "Left" if "left" in eye_text else ("Right" if "right" in eye_text else "")
            pool = by_side.get(side) or all_pool
            chosen = rng.choice(pool)
            cur.execute(
                "UPDATE patient_records SET source_image_path = ?, heatmap_image_path = ? WHERE id = ?",
                (chosen.source_rel, chosen.heat_rel, int(rid)),
            )
            updated += 1
        legacy.commit()
        print("patient_records.db updated_rows", updated)
    finally:
        legacy.close()

    # Update EMR users.db
    users = sqlite3.connect(os.path.join("data", "users.db"))
    users.execute("PRAGMA foreign_keys = ON")
    try:
        _ensure_cols(
            users,
            "emr_screening_eyes",
            {
                "fundus_image_path": "TEXT",
                "gradcam_image_path": "TEXT",
            },
        )
        cur = users.cursor()
        cur.execute(
            """
            SELECT e.eye_id, p.patient_code, e.eye_side
            FROM emr_screening_eyes e
            JOIN emr_screenings s ON s.screening_id = e.screening_id
            JOIN emr_patients p ON p.patient_id = s.patient_id
            WHERE p.patient_code LIKE ?
            """,
            (f"{SEED_PATIENT_PREFIX}%",),
        )
        rows = cur.fetchall()
        updated = 0
        for eye_id, patient_code, eye_side in rows:
            side = str(eye_side or "").strip().capitalize()
            pool = by_side.get(side) or all_pool
            chosen = rng.choice(pool)
            cur.execute(
                "UPDATE emr_screening_eyes SET fundus_image_path = ?, gradcam_image_path = ? WHERE eye_id = ?",
                (chosen.source_abs, chosen.heat_abs, int(eye_id)),
            )
            updated += 1
        users.commit()
        print("users.db emr_screening_eyes updated_rows", updated)
    finally:
        users.close()

    # Quick summary: how many distinct folders got used.
    used = {a.patient_folder for a in real_assets}
    print("real_pool_folders", len(used), "real_pool_assets", len(real_assets))


if __name__ == "__main__":
    main()

