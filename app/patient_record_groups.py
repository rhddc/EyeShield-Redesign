from __future__ import annotations

from datetime import datetime


_SEVERITY_RANK = {
    "No DR": 0,
    "Mild DR": 1,
    "Moderate DR": 2,
    "Severe DR": 3,
    "Proliferative DR": 4,
}


def parse_datetime_value(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def normalize_eye_side(value: str) -> str:
    eye = str(value or "").strip().lower()
    if not eye:
        return ""
    if "right" in eye or eye in {"r", "od"}:
        return "right"
    if "left" in eye or eye in {"l", "os"}:
        return "left"
    return eye


def canonical_eye_label(value: str) -> str:
    key = normalize_eye_side(value)
    if key == "right":
        return "Right Eye"
    if key == "left":
        return "Left Eye"
    return str(value or "").strip() or "Eye not set"


def eye_sort_key(value: str) -> tuple[int, str]:
    eye = normalize_eye_side(value)
    if eye == "right":
        return (0, eye)
    if eye == "left":
        return (1, eye)
    return (2, str(value or "").strip().lower())


def normalize_severity(value: str) -> str:
    text = str(value or "").strip()
    lower = text.lower()
    if not lower:
        return ""
    if "proliferative" in lower or lower == "pdr":
        return "Proliferative DR"
    if "severe" in lower:
        return "Severe DR"
    if "moderate" in lower:
        return "Moderate DR"
    if "mild" in lower:
        return "Mild DR"
    if "no dr" in lower or lower == "normal":
        return "No DR"
    return text


def display_severity(record: dict) -> str:
    value = (
        str(record.get("final_diagnosis_icdr") or "").strip()
        or str(record.get("doctor_classification") or "").strip()
        or str(record.get("ai_classification") or "").strip()
        or str(record.get("result") or "").strip()
    )
    return normalize_severity(value) or "Pending"


def severity_rank(value: str) -> int:
    return _SEVERITY_RANK.get(normalize_severity(value), -1)


def _record_sort_key(record: dict) -> tuple[datetime, int]:
    return (parse_datetime_value(record.get("screened_at")) or datetime.min, int(record.get("id") or 0))


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def group_patient_record_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    group_order: list[str] = []

    for raw_row in rows or []:
        row = dict(raw_row or {})
        group_id = str(row.get("screening_group_id") or "").strip() or f"record-{int(row.get('id') or 0)}"
        if group_id not in grouped:
            grouped[group_id] = []
            group_order.append(group_id)
        grouped[group_id].append(row)

    grouped_records: list[dict] = []
    for group_id in group_order:
        source_rows = sorted(grouped[group_id], key=lambda item: (eye_sort_key(item.get("eyes")), _record_sort_key(item)))
        latest_row = max(source_rows, key=_record_sort_key)
        worst_row = max(source_rows, key=lambda item: (severity_rank(display_severity(item)), _record_sort_key(item)))

        eye_details: list[dict] = []
        for row in source_rows:
            eye_label = canonical_eye_label(row.get("eyes"))
            eye_key = normalize_eye_side(row.get("eyes")) or f"eye_{len(eye_details) + 1}"
            detail = dict(row)
            detail["eye_label"] = eye_label
            detail["eye_key"] = eye_key
            detail["display_result"] = display_severity(row)
            eye_details.append(detail)

        eye_labels = _dedupe_strings([detail["eye_label"] for detail in eye_details])
        eye_result_lines = [f"{detail['eye_label']}: {detail['display_result']}" for detail in eye_details]
        confidence_lines = [
            f"{detail['eye_label']}: {detail.get('confidence') or '—'}"
            for detail in eye_details
        ]
        finding_lines = [
            f"{detail['eye_label']}: {str(detail.get('doctor_findings') or detail.get('notes') or '—').strip() or '—'}"
            for detail in eye_details
        ]

        if {"right", "left"}.issubset({detail["eye_key"] for detail in eye_details}):
            visit_eye_label = "Both Eyes"
        elif len(eye_labels) == 1:
            visit_eye_label = eye_labels[0]
        else:
            visit_eye_label = ", ".join(eye_labels) or "Eye not set"

        follow_up_values = _dedupe_strings([str(row.get("screening_type") or "").strip() for row in source_rows])
        screen_type = "follow_up" if "follow_up" in follow_up_values else (follow_up_values[0] if follow_up_values else "")
        previous_screening_id = next(
            (
                row.get("previous_screening_id")
                for row in reversed(source_rows)
                if str(row.get("previous_screening_id") or "").strip()
            ),
            None,
        )
        record_ids = [int(row.get("id") or 0) for row in source_rows if int(row.get("id") or 0)]
        primary_record_id = int(latest_row.get("id") or 0) or (record_ids[-1] if record_ids else 0)

        summary = dict(latest_row)
        summary.update(
            {
                "id": primary_record_id,
                "screening_group_id": group_id,
                "record_ids": record_ids,
                "source_rows": source_rows,
                "eye_details": eye_details,
                "eyes": visit_eye_label,
                "eye_summary": "\n".join(eye_result_lines) if eye_result_lines else visit_eye_label,
                "result": display_severity(worst_row),
                "ai_classification": display_severity(worst_row),
                "doctor_classification": display_severity(worst_row),
                "final_diagnosis_icdr": display_severity(worst_row),
                "confidence": "\n".join(confidence_lines) if confidence_lines else str(latest_row.get("confidence") or ""),
                "doctor_findings": "\n".join(finding_lines) if finding_lines else str(latest_row.get("doctor_findings") or ""),
                "screened_at": str(latest_row.get("screened_at") or ""),
                "screening_type": screen_type,
                "previous_screening_id": previous_screening_id,
                "primary_record_id": primary_record_id,
                "primary_record": latest_row,
                "has_multiple_eyes": len(eye_details) > 1,
                "source_image_path": str(worst_row.get("source_image_path") or latest_row.get("source_image_path") or ""),
                "heatmap_image_path": str(worst_row.get("heatmap_image_path") or latest_row.get("heatmap_image_path") or ""),
                "selected_eye_key": eye_details[0]["eye_key"] if eye_details else "",
            }
        )
        grouped_records.append(summary)

    grouped_records.sort(key=lambda item: _record_sort_key(item), reverse=False)
    return grouped_records
