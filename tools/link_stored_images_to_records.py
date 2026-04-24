from __future__ import annotations

import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
STORED = APP_DIR / "stored_images"


@dataclass(frozen=True)
class EyeAsset:
    source_abs: str
    heat_abs: str
    source_rel: str
    heat_rel: str


_SIDE_RE = re.compile(r"_(left|right)_eye_", re.I)


def _side_from_name(name: str) -> str:
    m = _SIDE_RE.search(str(name or ""))
    if not m:
        return ""
    return m.group(1).capitalize()


def _rel_from_abs(abs_path: Path) -> str:
    # convert app/<rel> into <rel> with forward slashes
    rel = abs_path.resolve().relative_to(APP_DIR.resolve())
    return str(rel).replace("\\", "/")


def _collect_assets() -> dict[tuple[str, str], EyeAsset]:
    """
    Build mapping: (patient_code, side) -> latest asset paths.
    We choose the most recent file name lexicographically (timestamp prefix).
    """
    by_key: dict[tuple[str, str], list[Path]] = defaultdict(list)
    if not STORED.exists():
        return {}

    for p in STORED.rglob("*_source.*"):
        if not p.is_file():
            continue
        side = _side_from_name(p.name)
        if not side:
            continue
        try:
            patient_code = p.parent.name
        except Exception:
            continue
        by_key[(patient_code, side)].append(p)

    out: dict[tuple[str, str], EyeAsset] = {}
    for (patient_code, side), sources in by_key.items():
        sources_sorted = sorted(sources, key=lambda x: x.name)
        src = sources_sorted[-1]
        # try heatmap with same prefix
        heat = None
        candidate = src.with_name(src.name.replace("_source.", "_heatmap."))
        if candidate.exists():
            heat = candidate
        else:
            # any heatmap in folder for that side
            heats = sorted(src.parent.glob(f"*_{side.lower()}_eye_heatmap.*"), key=lambda x: x.name)
            if heats:
                heat = heats[-1]
        heat = heat or Path("")

        out[(patient_code, side)] = EyeAsset(
            source_abs=str(src.resolve()),
            heat_abs=str(heat.resolve()) if heat and heat.exists() else "",
            source_rel=_rel_from_abs(src),
            heat_rel=_rel_from_abs(heat) if heat and heat.exists() else "",
        )
    return out


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, col_type: str = "TEXT") -> None:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in cur.fetchall()}
    if col in cols:
        return
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    conn.commit()


def main() -> None:
    assets = _collect_assets()
    if not assets:
        print("No stored image assets found under app/stored_images.")
        return

    # Update legacy patient_records.db (relative paths)
    legacy_db = Path("data") / "patient_records.db"
    legacy = sqlite3.connect(str(legacy_db))
    try:
        _ensure_column(legacy, "patient_records", "source_image_path")
        _ensure_column(legacy, "patient_records", "heatmap_image_path")

        cur = legacy.cursor()
        cur.execute("SELECT id, patient_id, eyes FROM patient_records")
        rows = cur.fetchall()
        updated = 0
        for rid, patient_id, eyes in rows:
            pid = str(patient_id or "").strip()
            side = "Left" if "left" in str(eyes or "").lower() else ("Right" if "right" in str(eyes or "").lower() else "")
            if not pid or not side:
                continue
            asset = assets.get((pid, side))
            if not asset:
                continue
            cur.execute(
                "UPDATE patient_records SET source_image_path = ?, heatmap_image_path = ? WHERE id = ?",
                (asset.source_rel, asset.heat_rel, int(rid)),
            )
            updated += 1
        legacy.commit()
        print("patient_records.db updated_rows", updated)
    finally:
        legacy.close()

    # Update EMR DB users.db (absolute paths)
    users_db = Path("data") / "users.db"
    users = sqlite3.connect(str(users_db))
    try:
        _ensure_column(users, "emr_screening_eyes", "fundus_image_path")
        _ensure_column(users, "emr_screening_eyes", "gradcam_image_path")

        cur = users.cursor()
        cur.execute(
            """
            SELECT e.eye_id, p.patient_code, e.eye_side
            FROM emr_screening_eyes e
            JOIN emr_screenings s ON s.screening_id = e.screening_id
            JOIN emr_patients p ON p.patient_id = s.patient_id
            """
        )
        rows = cur.fetchall()
        updated = 0
        for eye_id, patient_code, eye_side in rows:
            pid = str(patient_code or "").strip()
            side = str(eye_side or "").strip().capitalize()
            asset = assets.get((pid, side))
            if not asset:
                continue
            cur.execute(
                "UPDATE emr_screening_eyes SET fundus_image_path = ?, gradcam_image_path = ? WHERE eye_id = ?",
                (asset.source_abs, asset.heat_abs, int(eye_id)),
            )
            updated += 1
        users.commit()
        print("users.db emr_screening_eyes updated_rows", updated)
    finally:
        users.close()


if __name__ == "__main__":
    main()

