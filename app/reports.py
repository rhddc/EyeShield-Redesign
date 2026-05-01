# "reports.py"
"""
Reports module for EyeShield EMR application.
Provides offline summary analytics from local patient_records data.
"""

import csv
import json
from html import escape
import os
try:
    from .patientInfo import handle_patient_info_double_click
    from .patient_timeline_dialog import PatientTimelineDialog
except Exception:  # pragma: no cover
    from patientInfo import handle_patient_info_double_click
    from patient_timeline_dialog import PatientTimelineDialog
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QLineEdit, QComboBox, QHeaderView,
    QFileDialog, QDialog, QMessageBox, QMenu, QScrollArea, QFrame,
    QTextEdit, QProgressBar, QStackedWidget, QStyledItemDelegate,
    QApplication, QStyle, QStyleOptionViewItem,
)
from PySide6.QtCore import Qt, QSize, QRect, QMarginsF, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter, QFont, QImage, QPdfWriter, QPageSize, QPageLayout, QTextDocument

try:
    from .auth import UserManager
    from .app_paths import PATIENT_RECORDS_DB_PATH
    from .patient_record_groups import group_patient_record_rows
    from . import emr_service as emr
    from .ui_feedback import apply_dialog_style
except Exception:  # pragma: no cover
    from auth import UserManager
    from app_paths import PATIENT_RECORDS_DB_PATH
    from patient_record_groups import group_patient_record_rows
    import emr_service as emr
    from ui_feedback import apply_dialog_style
try:
    from .screening_widgets import ClickableImageLabel
except Exception:
    from screening_widgets import ClickableImageLabel

DB_FILE = str(PATIENT_RECORDS_DB_PATH)


def _queue_id_from_screening_group_id(screening_group_id: str) -> int:
    """
    UI record unit = visit group.
    In the UI this is already represented as screening_group_id = "queue-<qid>".
    """
    raw = str(screening_group_id or "").strip()
    if raw.lower().startswith("queue-"):
        try:
            return int(raw.split("-", 1)[1])
        except (ValueError, TypeError):
            return 0
    return 0


def _is_truthy_flag(value) -> bool:
    """Normalize legacy/new boolean-like values stored in SQLite text fields."""
    return str(value or "").strip().lower() in {"true", "1", "yes", "checked", "y"}


_APP_ROOT = Path(__file__).resolve().parent
_SEVERITY_RANK = {
    "No DR": 0,
    "Mild DR": 1,
    "Moderate DR": 2,
    "Severe DR": 3,
    "Proliferative DR": 4,
}


def _parse_datetime_value(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    dt = None
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    
    if dt and dt.tzinfo is None:
        # Standardize naive datetimes to UTC for consistent comparison
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _to_local_datetime(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # dt is now always aware (UTC) because of _parse_datetime_value standardization
    return dt.astimezone() # Converts to system local time


@staticmethod
def _format_screening_datetime(value: str) -> str:
    parsed = _parse_datetime_value(value)
    if not parsed:
        return str(value or "—").strip() or "—"
    
    # Convert to local system time (PH)
    local_dt = _to_local_datetime(parsed)
    hour = local_dt.strftime("%I").lstrip("0") or "0"
    return f"{local_dt.strftime('%B')} {local_dt.day}, {local_dt.year} - {hour}:{local_dt.strftime('%M')} {local_dt.strftime('%p').lower()}"


def _format_screening_datetime_label(value: str) -> str:
    parsed = _parse_datetime_value(value)
    if not parsed:
        return str(value or "-")
    local_dt = _to_local_datetime(parsed)
    return local_dt.strftime("%B %d, %Y  %I:%M %p")


def generate_unified_patient_report(parent, patient_record, eye_records, username, output_path=None):
    """
    Shared logic for generating a premium, minimalist PDF report.
    Used by both historic record viewing and immediate post-screening review.
    """
    if not output_path:
        patient_name_raw = str(patient_record.get("name") or "Patient").strip().replace(" ", "_")
        default_name = f"EyeShield_Report_{patient_name_raw}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        output_path, _ = QFileDialog.getSaveFileName(parent, "Save Patient Report", default_name, "PDF Files (*.pdf)")
    
    if not output_path:
        return False

    def esc(v) -> str:
        return escape(str(v or "").strip() or "—")

    # Metadata
    report_date = datetime.now().strftime("%B %d, %Y %I:%M %p")
    created_by = str(os.environ.get("EYESHIELD_CURRENT_NAME", "") or username or "Staff").strip()

    # Diagnosis logic (pick most severe or just the latest if only one)
    final_dx = "Pending"
    for r in eye_records:
        grade = str(r.get("final_diagnosis_icdr") or r.get("result") or "Pending").strip()
        if grade != "Pending":
            if final_dx == "Pending" or _SEVERITY_RANK.get(grade, 0) > _SEVERITY_RANK.get(final_dx, 0):
                final_dx = grade

    # Helpers for images
    def resolve_image_path(path_value: str) -> str:
        raw = str(path_value or "").strip()
        if not raw: return ""
        candidate = raw if os.path.isabs(raw) else os.path.join(os.path.dirname(os.path.abspath(__file__)), raw)
        if os.path.isfile(candidate):
            return str(Path(candidate).resolve())
        return ""

    def build_b64_image(path_value: str, width: int = 400) -> str:
        res = resolve_image_path(path_value)
        if not res: return ""
        img = QImage(res)
        if img.isNull(): return ""
        
        # Scale for report
        scaled = img.scaled(width, width, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        scaled.save(buf, "PNG")
        buf.close()
        return f"data:image/png;base64,{bytes(ba.toBase64()).decode('ascii')}"

    # Sections
    def sec(title):
        return (
            f'<div style="margin:20px 0 10px;padding-bottom:8px;border-bottom:2px solid #334155;">'
            f'<span style="font-size:10pt;font-weight:800;color:#1e293b;letter-spacing:1px;text-transform:uppercase;">{title}</span>'
            f'</div>'
        )

    def field_row(label, value):
        return (
            f'<tr>'
            f'<td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:9.5pt;color:#64748b;font-weight:500;width:35%;">{label}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:9.5pt;color:#0f172a;font-weight:600;">{value}</td>'
            f'</tr>'
        )

    # Image pair block
    def eye_result_block(eye_record: dict) -> str:
        eye_label = str(eye_record.get("eyes") or eye_record.get("eye") or "Eye").strip()
        result = str(eye_record.get("result") or "No DR").strip()
        
        src_b64 = build_b64_image(eye_record.get("source_image_path") or eye_record.get("image_path"))
        heat_b64 = build_b64_image(eye_record.get("heatmap_image_path") or eye_record.get("heatmap_path"))
        
        def img_tag(b64, label):
            if not b64:
                return '<div style="height:200px;background:#f8fafc;border:1px solid #e2e8f0;display:flex;align-items:center;justify-content:center;color:#94a3b8;font-size:9pt;font-style:italic;">' + label + ' not available</div>'
            return f'<img src="{b64}" style="width:100%;max-width:340px;border:1px solid #cbd5e1;border-radius:6px;" />'

        return f"""
        <div style="page-break-inside:avoid;margin-bottom:24px;background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;padding:20px;">
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:15px;">
                <tr>
                    <td style="font-size:12pt;font-weight:700;color:#1e3a8a;">{esc(eye_label)}</td>
                    <td align="right" style="font-size:10pt;color:#1e40af;font-weight:600;">Result: {esc(result)}</td>
                </tr>
            </table>
            <table width="100%" cellpadding="0" cellspacing="12">
                <tr>
                    <td width="50%" align="center">
                        <div style="font-size:8pt;font-weight:700;color:#64748b;margin-bottom:6px;text-transform:uppercase;">Source Fundus</div>
                        {img_tag(src_b64, "Source")}
                    </td>
                    <td width="50%" align="center">
                        <div style="font-size:8pt;font-weight:700;color:#64748b;margin-bottom:6px;text-transform:uppercase;">AI Analysis (Heatmap)</div>
                        {img_tag(heat_b64, "Heatmap")}
                    </td>
                </tr>
            </table>
        </div>
        """

    # Categorize eyes for layout
    right_eye_record = next((r for r in eye_records if "right" in str(r.get("eyes") or r.get("eye") or "").lower()), None)
    left_eye_record = next((r for r in eye_records if "left" in str(r.get("eyes") or r.get("eye") or "").lower()), None)
    
    # Diagnosis summary by eye (for all eyes in the encounter)
    diagnosis_by_eye_html = ""
    for r in eye_records:
        label = str(r.get("eyes") or r.get("eye") or "Eye").strip()
        grade = str(r.get("final_diagnosis_icdr") or r.get("result") or "Pending").strip()
        diagnosis_by_eye_html += field_row(f"{esc(label)} Diagnosis", esc(grade))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #1e293b; line-height: 1.6; margin: 0; padding: 0; }}
    table {{ border-collapse: collapse; }}
    .header {{ background: #1e3a8a; color: #ffffff; padding: 35px 40px; border-bottom: 5px solid #1e40af; text-align: center; }}
    .content {{ padding: 30px 40px; }}
    .footer {{ padding: 20px 40px; border-top: 1px solid #e2e8f0; font-size: 8pt; color: #94a3b8; }}
    .page-break {{ page-break-before: always; }}
    .section-title {{ margin:20px 0 10px; padding-bottom:8px; border-bottom:2px solid #334155; }}
    .section-label {{ font-size:10pt; font-weight:800; color:#1e293b; letter-spacing:1px; text-transform:uppercase; }}
</style>
</head><body>
    <!-- PAGE 1: IDENTITY & HISTORY -->
    <div class="header">
        <div style="font-size:24pt;font-weight:800;letter-spacing:-0.5px;margin-bottom:5px;">Patient Screening Report</div>
        <div style="margin-top:10px;font-size:10pt;opacity:0.9;">
            <b>ID:</b> {esc(patient_record.get('patient_id'))} &nbsp;|&nbsp; <b>Date:</b> {report_date} &nbsp;|&nbsp; <b>By:</b> {esc(created_by)}
        </div>
    </div>
    
    <div class="content">
        <div class="section-title"><span class="section-label">Patient Identity</span></div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin-bottom:20px;">
            {field_row("Full Name", esc(patient_record.get('name')))}
            {field_row("Date of Birth", esc(patient_record.get('birthdate')))}
            {field_row("Age", esc(patient_record.get('age')))}
            {field_row("Sex", esc(patient_record.get('sex')))}
            {field_row("Contact Number", esc(patient_record.get('phone')))}
            {field_row("Email Address", esc(patient_record.get('email')))}
            {field_row("Residential Address", esc(patient_record.get('address')))}
        </table>

        <div class="section-title"><span class="section-label">Diabetic History</span></div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin-bottom:20px;">
            {field_row("Diabetes Type", esc(patient_record.get('diabetes_type')))}
            {field_row("Diagnosis Date", esc(patient_record.get('diag_date')))}
            {field_row("Duration", f"{esc(patient_record.get('duration'))} years" if patient_record.get('duration') else "—")}
            {field_row("Treatment Regimen", esc(patient_record.get('treatment_regimen')))}
        </table>
    </div>

    <!-- PAGE 2: SUMMARY & RIGHT EYE -->
    <div class="page-break content">
        <div class="section-title"><span class="section-label">Screening Summary</span></div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin-bottom:24px;">
            {diagnosis_by_eye_html}
            {field_row("Reviewer Comments", esc(eye_records[-1].get('doctor_findings') if eye_records else ""))}
        </table>

        {"<!-- Right Eye Analysis -->" if right_eye_record else ""}
        {f'<div class="section-title"><span class="section-label">Right Eye Analysis</span></div>' if right_eye_record else ""}
        {eye_result_block(right_eye_record) if right_eye_record else ""}
    </div>

    <!-- PAGE 3: LEFT EYE -->
    {f'<div class="page-break content">' if left_eye_record else ""}
        {f'<div class="section-title"><span class="section-label">Left Eye Analysis</span></div>' if left_eye_record else ""}
        {eye_result_block(left_eye_record) if left_eye_record else ""}
    {f'</div>' if left_eye_record else ""}

    <div class="footer">
        <table width="100%">
            <tr>
                <td><b>EyeShield Clinical Suite</b> - Automated Diabetic Retinopathy Screening</td>
                <td align="right">Digital Clinical Report</td>
            </tr>
        </table>
        <div style="margin-top:10px;font-style:italic;">
            Disclaimer: This report is generated by an automated AI system and serves as clinical decision support. 
            All findings must be confirmed by a licensed medical professional before clinical action is taken.
        </div>
    </div>
</body></html>"""

    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setHtml(html)

    writer = QPdfWriter(output_path)
    writer.setResolution(300)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)

    doc.print_(writer)
    return True


def _normalize_severity(value: str) -> str:
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


def _display_severity(record: dict) -> str:
    value = (
        str(record.get("final_diagnosis_icdr") or "").strip()
        or str(record.get("doctor_classification") or "").strip()
        or str(record.get("ai_classification") or "").strip()
        or str(record.get("result") or "").strip()
    )
    return _normalize_severity(value) or "Pending"


def _severity_rank_for(value: str) -> int:
    return _SEVERITY_RANK.get(_normalize_severity(value), -1)


def _risk_status_for(value: str) -> tuple[str, str]:
    rank = _severity_rank_for(value)
    if rank <= 0:
        return "Low risk", "#16a34a"
    if rank == 1:
        return "Medium risk", "#ca8a04"
    return "High risk", "#dc2626"


def _parse_confidence_metrics(confidence_text: str) -> tuple[str, str, float | None, float | None]:
    raw = str(confidence_text or "").strip()
    if not raw:
        return "Confidence: —", "Uncertainty: —", None, None

    conf_match = __import__("re").search(r"confidence\s*:?\s*(\d+(?:\.\d+)?)\s*%", raw, __import__("re").IGNORECASE)
    unc_match = __import__("re").search(r"uncertainty\s*:?\s*(\d+(?:\.\d+)?)\s*%", raw, __import__("re").IGNORECASE)
    conf_pct = float(conf_match.group(1)) if conf_match else None
    unc_pct = float(unc_match.group(1)) if unc_match else (100.0 - conf_pct if conf_pct is not None else None)

    conf_label = f"Confidence: {conf_pct:.1f}%" if conf_pct is not None else f"Confidence: {raw}"
    unc_label = f"Uncertainty: {unc_pct:.1f}%" if unc_pct is not None else "Uncertainty: —"
    return conf_label, unc_label, conf_pct, unc_pct


def _resolve_media_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute() and path.exists():
        return str(path)
    candidate = (_APP_ROOT / raw).resolve()
    if candidate.exists():
        return str(candidate)
    return ""


def _timeline_sort_key(record: dict) -> tuple[datetime, int]:
    parsed = _parse_datetime_value(record.get("screened_at"))
    return (parsed or datetime.min, int(record.get("id") or 0))


class ScreeningComparisonDialog(QDialog):
    def __init__(self, all_records: list[dict], parent=None):
        # Parent deep inside EMR stacks can yield odd auxiliary-window sizing; prefer the top-level window.
        root = parent.window() if parent is not None and parent.window() is not None else parent
        super().__init__(root)
        self.setWindowFlag(Qt.WindowType.Window, True)
        # Filter for completed screenings only (ignore pending/on-going sessions)
        self.all_records = [
            rec for rec in (all_records or [])
            if any(self._has_real_eye_payload(d) for d in (rec.get("eye_details") or []))
        ]
        
        if not self.all_records:
            # Fallback if no completed records found (should be caught by caller)
            self.latest_record = {}
            self.previous_record = {}
        else:
            # We always compare against the LATEST COMPLETED screening.
            self.latest_record = self.all_records[-1]
            # Initially, we compare against the one immediately before it.
            self.previous_record = self.all_records[-2] if len(self.all_records) >= 2 else self.latest_record
        
        self.setWindowTitle("Compare Screenings")
        self.resize(1280, 720)
        self.setMinimumSize(1024, 600)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(18, 16, 18, 16)
        self._root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Screening Comparison")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#0f172a;")
        header.addWidget(title)
        header.addStretch(1)
        
        header.addWidget(QLabel("Compare latest with:"), 0, Qt.AlignVCenter)
        self._prev_selector = QComboBox()
        self._prev_selector.setFixedWidth(240)
        self._prev_selector.setStyleSheet(
            "QComboBox{background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:4px 12px;font-weight:600;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#ffffff;border:1px solid #cbd5e1;selection-background-color:#dbeafe;}"
        )
        
        # Populate selector with historical dates (excluding the latest one)
        history = self.all_records[:-1]
        history.reverse() # Newest first
        for rec in history:
            dt = _format_screening_datetime_label(rec.get("screened_at"))
            self._prev_selector.addItem(dt, rec)
        
        self._prev_selector.currentIndexChanged.connect(self._on_previous_changed)
        header.addWidget(self._prev_selector)
        self._root.addLayout(header)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setMinimumHeight(60)
        self._summary.setStyleSheet(
            "background:#f1f5f9;border:1px solid #cbd5e1;border-radius:12px;padding:12px 16px;font-size:14px;color:#334155;"
        )
        self._root.addWidget(self._summary)

        # Eye toggle (OD/OS)
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        self._toggle_row_wrap = QWidget()
        self._toggle_row_wrap.setStyleSheet("background:transparent;")
        self._toggle_row_layout = toggle_row
        toggle_row.addWidget(QLabel("View:"), 0, Qt.AlignVCenter)

        def _seg_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(30)
            b.setStyleSheet(
                "QPushButton{background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:0 12px;"
                "font-weight:700;color:#334155;}"
                "QPushButton:hover{background:#f8fafc;}"
                "QPushButton:checked{background:#dbeafe;border-color:#93c5fd;color:#1e3a8a;}"
            )
            return b

        self._btn_od = _seg_btn("Right (OD)")
        self._btn_os = _seg_btn("Left (OS)")
        self._btn_od.clicked.connect(lambda: self._set_mode("od"))
        self._btn_os.clicked.connect(lambda: self._set_mode("os"))

        for b in (self._btn_od, self._btn_os):
            toggle_row.addWidget(b)
        toggle_row.addStretch(1)
        self._toggle_row_wrap.setLayout(toggle_row)
        self._root.addWidget(self._toggle_row_wrap)

        # Scrollable body
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(10)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidget(self._content)
        self._root.addWidget(self._scroll, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        self._root.addWidget(close_btn, 0, Qt.AlignRight)

        # Resolve maps and default
        self._eye_map_latest = self._build_eye_map(self.latest_record)
        self._eye_map_prev = self._build_eye_map(self.previous_record)

        # Setup toggle enabling: enable if eye exists ANYWHERE in history
        available_sides = set()
        for rec in self.all_records:
            available_sides.update(self._build_eye_map(rec).keys())
            
        has_od = "od" in available_sides
        has_os = "os" in available_sides
        
        self._btn_od.setEnabled(has_od)
        self._btn_os.setEnabled(has_os)
        
        # Default to OD if available, otherwise OS, otherwise OD.
        self._active_side = "od" if has_od else ("os" if has_os else "od")
        self._set_mode(self._active_side)

    def _on_previous_changed(self, index: int):
        if index < 0: return
        self.previous_record = self._prev_selector.itemData(index)
        self._eye_map_prev = self._build_eye_map(self.previous_record)
        self._set_mode(self._active_side)

    @staticmethod
    def _guess_side(detail: dict) -> str:
        """Return 'od', 'os', or '' from a per-eye detail record."""
        blob = " ".join(
            str(detail.get(k) or "")
            for k in ("eye_key", "eye_label", "eyes", "eye_side", "side")
        ).strip().lower()
        if "right" in blob or " od" in f" {blob} " or blob.endswith("od") or blob.startswith("od"):
            return "od"
        if "left" in blob or " os" in f" {blob} " or blob.endswith("os") or blob.startswith("os"):
            return "os"
        return ""

    def _build_eye_map(self, record: dict) -> dict[str, dict]:
        """Map: {'od': eye_detail, 'os': eye_detail}.

        Important: some patients are screened on a single eye, but upstream data may still
        contain an `eye_details` placeholder for the other side. We only consider a side
        "available" if the detail looks like a real screening payload (image/result/etc.).
        """
        eye_details = list((record or {}).get("eye_details") or [])
        out: dict[str, dict] = {}
        for d in eye_details:
            if not isinstance(d, dict):
                continue
            if not self._has_real_eye_payload(d):
                continue
            side = self._guess_side(d)
            if side and side not in out:
                out[side] = d
        return out

    @staticmethod
    def _has_real_eye_payload(detail: dict) -> bool:
        """Heuristic: returns True when the eye detail has actual screening media (images)."""
        if not isinstance(detail, dict) or not detail:
            return False

        # Media paths are the MANDATORY indicator for comparison.
        # Ongoing sessions or pre-filled forms without images should be ignored.
        for k in ("source_image_path", "heatmap_image_path", "image_path", "fundus_image_path"):
            if str(detail.get(k) or "").strip():
                return True

        return False

    @classmethod
    def filter_completed_screenings(cls, all_records: list[dict]) -> list[dict]:
        """Utility to extract only records that have actual screening data."""
        return [
            rec for rec in (all_records or [])
            if any(cls._has_real_eye_payload(d) for d in (rec.get("eye_details") or []))
        ]

    @staticmethod
    def _trend_label(prev_sev: str, latest_sev: str) -> tuple[str, str]:
        prev_rank = _severity_rank_for(prev_sev)
        latest_rank = _severity_rank_for(latest_sev)
        delta = latest_rank - prev_rank
        if delta > 1:
            return "Rapid deterioration", "#dc2626"
        if delta > 0:
            return "Worsening", "#ea580c"
        if delta < 0:
            return "Improving", "#16a34a"
        return "Stable", "#2563eb"

    def _set_mode(self, mode: str) -> None:
        mode = str(mode or "").strip().lower()
        if mode not in {"od", "os"}:
            mode = "od"
        # Don't allow switching to an eye that isn't available.
        if mode == "od" and hasattr(self, "_btn_od") and not self._btn_od.isEnabled():
            mode = "os"
        if mode == "os" and hasattr(self, "_btn_os") and not self._btn_os.isEnabled():
            mode = "od"
        # Ensure exclusive toggle behavior.
        self._btn_od.setChecked(mode == "od")
        self._btn_os.setChecked(mode == "os")
        self._render_mode(mode)

    def _clear_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)

    def _render_mode(self, mode: str) -> None:
        self._active_side = mode
        self._clear_content()
        side = mode
        prev_eye = self._eye_map_prev.get(side) or {}
        latest_eye = self._eye_map_latest.get(side) or {}
        # When a side is missing in one record, show a placeholder card for that side.
        prev_payload = self._merge_group_with_eye(
            self.previous_record,
            prev_eye,
            fallback_eye_label=("Right (OD)" if side == "od" else "Left (OS)"),
        )
        latest_payload = self._merge_group_with_eye(
            self.latest_record,
            latest_eye,
            fallback_eye_label=("Right (OD)" if side == "od" else "Left (OS)"),
        )
        self._render_single(
            prev_payload,
            latest_payload,
            eye_label_override=("Right (OD)" if side == "od" else "Left (OS)"),
        )

    @staticmethod
    def _merge_group_with_eye(group_record: dict, eye_detail: dict, *, fallback_eye_label: str) -> dict:
        """Build a display record for a single eye within a grouped visit."""
        merged = dict(group_record or {})
        if isinstance(eye_detail, dict) and eye_detail:
            merged.update(eye_detail)
        merged.setdefault("eye_label", fallback_eye_label)
        return merged

    # NOTE: intentionally no "Both eyes" view — OD/OS comparison is handled via the toggle.

    def _render_single(self, previous_payload: dict, latest_payload: dict, *, eye_label_override: str | None) -> None:
        has_prev = self._has_real_eye_payload(previous_payload)
        has_latest = self._has_real_eye_payload(latest_payload)
        eye_part = f" ({escape(eye_label_override)})" if eye_label_override else ""

        if not has_prev or not has_latest:
            missing_date = _format_screening_datetime_label(previous_payload.get('screened_at') if not has_prev else latest_payload.get('screened_at'))
            self._summary.setText(
                f"Comparison unavailable for {escape(eye_label_override or 'this eye')} | "
                f"<span style='color:#64748b;'>Not screened on {escape(missing_date)}</span>"
            )
        else:
            prev_sev = _display_severity(previous_payload)
            latest_sev = _display_severity(latest_payload)
            trend_text, trend_color = self._trend_label(prev_sev, latest_sev)
            self._summary.setText(
                f"Severity change{eye_part}: <b>{escape(prev_sev)}</b> -> <b>{escape(latest_sev)}</b> | "
                f"<span style='color:{trend_color};font-weight:700;'>{escape(trend_text)}</span>"
            )

        columns = QHBoxLayout()
        columns.setSpacing(20)
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        host.setLayout(columns)
        columns.addWidget(self._build_eye_card(previous_payload, heading="Previous Screening"), 1)
        columns.addWidget(self._build_eye_card(latest_payload, heading="Latest Screening"), 1)
        self._content_layout.addWidget(host, 1)

    def _build_eye_card(self, record: dict, *, heading: str) -> QGroupBox:
        has_data = self._has_real_eye_payload(record)
        card = QGroupBox(heading)
        card.setMinimumWidth(480)
        card.setStyleSheet(
            "QGroupBox{font-weight:700;padding-top:24px;border:1px solid #e2e8f0;border-radius:12px;background:#ffffff;color:#1e293b;}"
            "QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 8px;color:#64748b;}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)
        card_layout.setContentsMargins(16, 20, 16, 16)

        date_label = escape(_format_screening_datetime_label(record.get('screened_at')))
        eye_label = str(record.get("eye_label") or record.get("eyes") or "—")

        if not has_data:
            placeholder = QLabel(
                f"<div style='text-align:center; padding: 40px 10px;'>"
                f"<div style='font-size:48px; color:#cbd5e1;'>∅</div><br>"
                f"<b style='font-size:14px; color:#64748b;'>Not screened on this date</b><br>"
                f"<span style='font-size:12px; color:#94a3b8;'>{date_label}</span>"
                f"</div>"
            )
            placeholder.setWordWrap(True)
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px;")
            card_layout.addStretch(1)
            card_layout.addWidget(placeholder)
            card_layout.addStretch(1)
            return card

        result = _display_severity(record)
        meta = QLabel(
            "<br>".join(
                [
                    f"<b>Date:</b> {date_label}",
                    f"<b>Eye:</b> {escape(eye_label)}",
                    f"<b>Severity:</b> {escape(result)}",
                ]
            )
        )
        meta.setWordWrap(True)
        meta.setStyleSheet("color:#334155;")
        card_layout.addWidget(meta)

        for label_text, path_key in (("Fundus Image", "source_image_path"), ("Grad-CAM", "heatmap_image_path")):
            img_path = _resolve_media_path(record.get(path_key))
            img = ClickableImageLabel(label_text, viewer_title=f"{heading} - {label_text}")
            img.setAlignment(Qt.AlignCenter)
            img.setMinimumHeight(170)
            img.setMaximumHeight(220)
            img.setStyleSheet("background:#f8fafc;color:#94a3b8;border:1px solid #e2e8f0;border-radius:10px;")
            if img_path:
                pixmap = QPixmap(img_path)
                if not pixmap.isNull():
                    img.setPixmap(pixmap.scaled(360, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    img.full_pixmap = pixmap # Required for zoom dialog
            card_layout.addWidget(img)

        final_result = (
            str(record.get("final_diagnosis_icdr") or "").strip()
            or str(record.get("doctor_classification") or "").strip()
            or str(record.get("result") or "").strip()
            or str(result or "").strip()
            or "—"
        )
        override_comments = str(record.get("override_justification") or "").strip() or "—"
        additional_comments = str(record.get("doctor_findings") or "").strip() or "—"

        details = QLabel(
            "<br>".join(
                [
                    f"<b>Final Findings of the Doctor:</b> {escape(final_result)}",
                    f"<b>Override Comments:</b> {escape(override_comments)}",
                    f"<b>Additional Comments:</b> {escape(additional_comments)}",
                ]
            )
        )
        details.setWordWrap(True)
        details.setStyleSheet("background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;color:#334155;")
        card_layout.addWidget(details)
        return card


## PatientTimelineDialog is now in patient_timeline_dialog.py

    def _build_left_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(280)
        sidebar.setStyleSheet("background:transparent;")
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(12)
        sl.addWidget(self._build_patient_info_card(), 0)
        sl.addWidget(self._build_timeline_card(), 1)
        return sidebar

    # ══════════════════════════════════════════════════════════════════════
    # HEADER
    # ══════════════════════════════════════════════════════════════════════
    def _build_header(self, patient_name: str, initial_result: str, risk_text: str, risk_color: str) -> QFrame:
        header = QFrame()
        header.setStyleSheet(
            "QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;}"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(22, 16, 22, 16)
        hl.setSpacing(18)

        # Avatar
        initials = "".join(p[0].upper() for p in str(patient_name).split() if p)[:2] or "?"
        avatar = QLabel(initials)
        avatar.setFixedSize(56, 56)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {risk_color},stop:1 {risk_color}cc);"
            "color:#ffffff;font-size:20px;font-weight:800;border-radius:28px;border:none;"
        )
        hl.addWidget(avatar)

        # Name + meta
        name_col = QVBoxLayout()
        name_col.setSpacing(4)
        name_lbl = QLabel(str(patient_name))
        name_lbl.setStyleSheet("font-size:24px;font-weight:800;color:#0f172a;background:transparent;border:none;")

        age_val = self.patient_summary.get("age") or "—"
        sex_val = self.patient_summary.get("sex") or "—"
        pid_val = self.patient_summary.get("patient_id") or "—"
        condition = self.patient_summary.get("diabetes_type") or "No condition noted"

        meta_parts = [
            f"🆔 {pid_val}",
            f"👤 {age_val} yrs · {sex_val}",
            f"🩺 {condition}",
        ]
        pid_lbl = QLabel("     ".join(meta_parts))
        pid_lbl.setStyleSheet("font-size:12px;color:#64748b;background:transparent;border:none;")
        name_col.addWidget(name_lbl)
        name_col.addWidget(pid_lbl)
        hl.addLayout(name_col, 1)

        # Initial condition chip
        initial_chip = self._build_chip("INITIAL CONDITION", initial_result or "Pending", "#0ea5e9", filled=False)
        hl.addWidget(initial_chip)

        # Risk chip
        risk_chip = self._build_chip("RISK LEVEL", risk_text.upper(), risk_color, filled=True)
        hl.addWidget(risk_chip)

        # Count chip
        count_chip = self._build_chip(
            "TOTAL SCREENINGS",
            str(len(self.timeline_records)),
            "#3b82f6",
            filled=False,
        )
        hl.addWidget(count_chip)

        return header

    def _build_chip(self, title: str, value: str, color: str, filled: bool = False) -> QFrame:
        chip = QFrame()
        if filled:
            chip.setStyleSheet(f"QFrame{{background:{color};border:1px solid {color};border-radius:10px;}}")
            title_color = "#ffffff99"
            value_color = "#ffffff"
        else:
            chip.setStyleSheet(f"QFrame{{background:{color}18;border:1px solid {color}44;border-radius:10px;}}")
            title_color = color
            value_color = color
        cl = QVBoxLayout(chip)
        cl.setContentsMargins(16, 10, 16, 10)
        cl.setSpacing(3)
        t = QLabel(title)
        t.setStyleSheet(f"font-size:9px;font-weight:700;color:{title_color};letter-spacing:1.2px;background:transparent;border:none;")
        v = QLabel(value)
        v.setStyleSheet(f"font-size:15px;font-weight:800;color:{value_color};background:transparent;border:none;")
        cl.addWidget(t)
        cl.addWidget(v)
        return chip

    # ══════════════════════════════════════════════════════════════════════
    # PROGRESSION BAR
    # ══════════════════════════════════════════════════════════════════════
    def _build_progression_bar(self) -> QFrame:
        prog_lines = self._build_progression_summary_lines()
        bar = QFrame()
        bar.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #f8fafc,stop:1 #f1f5f9);"
            "border:1px solid #e2e8f0;border-radius:12px;}"
        )
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(20, 12, 20, 12)
        hl.setSpacing(24)

        icons = {"Initial": "◎", "Progression": "→", "Trend": "◈", "Risk": "⬤"}
        colors = {"Initial": "#3b82f6", "Progression": "#8b5cf6", "Trend": "#ec4899", "Risk": "#f59e0b"}

        for line in prog_lines:
            key = next((k for k in icons if line.lower().startswith(k.lower())), None)
            icon_char = icons.get(key, "•")
            icon_color = colors.get(key, "#64748b")

            item = QWidget()
            item.setStyleSheet("background:transparent;")
            il = QHBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(8)

            icon_lbl = QLabel(icon_char)
            icon_lbl.setStyleSheet(f"font-size:16px;color:{icon_color};background:transparent;border:none;")
            text_lbl = QLabel(line)
            text_lbl.setStyleSheet("font-size:12px;color:#334155;background:transparent;border:none;font-weight:500;")
            text_lbl.setWordWrap(True)
            il.addWidget(icon_lbl)
            il.addWidget(text_lbl, 1)
            hl.addWidget(item, 1)

        if len(self.timeline_records) >= 2:
            cmp_btn = QPushButton("⇄  Compare Trends")
            cmp_btn.setStyleSheet(
                "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #3b82f6,stop:1 #6366f1);"
                "color:#ffffff;border:none;border-radius:8px;padding:8px 18px;font-weight:700;}"
                "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #2563eb,stop:1 #4f46e5);}"
            )
            cmp_btn.clicked.connect(self._handle_compare)
            hl.addWidget(cmp_btn)
        return bar

    # ══════════════════════════════════════════════════════════════════════
    # PATIENT INFO SIDEBAR
    # ══════════════════════════════════════════════════════════════════════
    def _build_patient_info_card(self) -> QGroupBox:
        pt = self.patient_summary

        card = QGroupBox("Patient Information")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 18, 0, 10)
        cl.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(host)
        vl.setContentsMargins(14, 0, 14, 10)
        vl.setSpacing(14)

        # ── Demographics ──
        vl.addWidget(self._section_header("👤  DEMOGRAPHICS"))
        vl.addWidget(self._info_row("Patient ID", pt.get("patient_id")))
        vl.addWidget(self._info_row("Full Name", pt.get("name")))
        vl.addWidget(self._info_row("Date of Birth", pt.get("birthdate")))
        vl.addWidget(self._info_row("Age", f"{pt.get('age')} years" if pt.get("age") else None))
        vl.addWidget(self._info_row("Sex", pt.get("sex")))
        vl.addWidget(self._info_row("Contact", pt.get("contact")))
        vl.addWidget(self._info_row("Eye Screened", pt.get("eyes")))

        # ── Vital Signs ──
        bp_sys = pt.get("blood_pressure_systolic") or "—"
        bp_dia = pt.get("blood_pressure_diastolic") or "—"
        fbs = pt.get("fasting_blood_sugar")
        rbs = pt.get("random_blood_sugar")

        vl.addWidget(self._section_header("💗  VITAL SIGNS"))
        vl.addWidget(self._info_row("Height", f"{pt.get('height')} cm" if pt.get("height") else None))
        vl.addWidget(self._info_row("Weight", f"{pt.get('weight')} kg" if pt.get("weight") else None))
        vl.addWidget(self._info_row("BMI", pt.get("bmi")))
        vl.addWidget(self._info_row("Blood Pressure", f"{bp_sys}/{bp_dia} mmHg" if (bp_sys != "—" or bp_dia != "—") else None))
        vl.addWidget(self._info_row("Fasting Glucose", f"{fbs} mg/dL" if fbs else None))
        vl.addWidget(self._info_row("Random Glucose", f"{rbs} mg/dL" if rbs else None))
        vl.addWidget(self._info_row("VA (Left)", pt.get("visual_acuity_left")))
        vl.addWidget(self._info_row("VA (Right)", pt.get("visual_acuity_right")))

        # ── Diabetic History ──
        prev_tx = "Yes" if _is_truthy_flag(pt.get("prev_treatment")) else "No"
        vl.addWidget(self._section_header("📋  DIABETIC HISTORY"))
        vl.addWidget(self._info_row("Diabetes Type", pt.get("diabetes_type")))
        vl.addWidget(self._info_row("Diagnosed", pt.get("diabetes_diagnosis_date")))
        vl.addWidget(self._info_row("Duration", pt.get("duration")))
        vl.addWidget(self._info_row("HbA1c", f"{pt.get('hba1c')}%" if pt.get("hba1c") else None))
        vl.addWidget(self._info_row("Treatment", pt.get("treatment_regimen")))
        vl.addWidget(self._info_row("Family History of Diabetes", pt.get("prev_dr_stage")))
    

        # ── Symptoms ──
        syms = []
        if _is_truthy_flag(pt.get("symptom_blurred_vision")): syms.append("Blurred vision")
        if _is_truthy_flag(pt.get("symptom_floaters")):       syms.append("Floaters")
        if _is_truthy_flag(pt.get("symptom_flashes")):        syms.append("Flashes")
        if _is_truthy_flag(pt.get("symptom_vision_loss")):    syms.append("Vision loss")

        vl.addWidget(self._section_header("⚠️  REPORTED SYMPTOMS"))
        if syms:
            sym_frame = QFrame()
            sym_frame.setStyleSheet("background:transparent;")
            sym_layout = QVBoxLayout(sym_frame)
            sym_layout.setContentsMargins(0, 0, 0, 0)
            sym_layout.setSpacing(4)
            for sym in syms:
                chip = QLabel(f"●  {sym}")
                chip.setStyleSheet(
                    "background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;"
                    "border-radius:6px;padding:4px 10px;font-size:11px;font-weight:600;"
                )
                sym_layout.addWidget(chip)
            vl.addWidget(sym_frame)
        else:
            none_lbl = QLabel("No symptoms reported")
            none_lbl.setStyleSheet("font-size:11px;color:#64748b;font-style:italic;background:transparent;border:none;padding:4px 0;")
            vl.addWidget(none_lbl)

        # ── Screened By ──
        screener = str(pt.get("original_screener_name") or pt.get("original_screener_username") or "—")
        vl.addWidget(self._section_header("👨‍⚕️  SCREENED BY"))
        screener_lbl = QLabel(screener)
        screener_lbl.setStyleSheet(
            "font-size:12px;color:#1e293b;font-weight:600;background:#f0f9ff;"
            "border:1px solid #bae6fd;border-radius:6px;padding:8px 12px;"
        )
        screener_lbl.setWordWrap(True)
        vl.addWidget(screener_lbl)

        vl.addStretch(1)
        scroll.setWidget(host)
        cl.addWidget(scroll, 1)
        return card

    def _section_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "font-size:10px;font-weight:800;color:#475569;letter-spacing:1.2px;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #f1f5f9,stop:1 #ffffff);"
            "border-left:3px solid #3b82f6;border-radius:4px;padding:6px 8px;margin-top:6px;"
        )
        return lbl

    def _info_row(self, label: str, value) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(4, 3, 0, 3)
        hl.setSpacing(10)
        lbl = QLabel(label)
        lbl.setStyleSheet(
            "font-size:11px;font-weight:600;color:#94a3b8;min-width:92px;max-width:92px;"
            "background:transparent;border:none;"
        )
        lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        display_val = str(value) if value not in (None, "", "—") else "—"
        val = QLabel(display_val)
        val.setStyleSheet(
            f"font-size:12px;color:{'#1e293b' if display_val != '—' else '#cbd5e1'};"
            f"font-weight:{'600' if display_val != '—' else '400'};"
            "background:transparent;border:none;"
        )
        val.setWordWrap(True)
        hl.addWidget(lbl)
        hl.addWidget(val, 1)
        return w

    # ══════════════════════════════════════════════════════════════════════
    # TIMELINE LIST
    # ══════════════════════════════════════════════════════════════════════
    def _build_timeline_card(self) -> QGroupBox:
        card = QGroupBox("Screening Timeline")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 18, 12, 12)
        cl.setSpacing(8)

        count_lbl = QLabel(f"📅  {len(self.timeline_records)} screening{'s' if len(self.timeline_records) != 1 else ''} on record")
        count_lbl.setStyleSheet(
            "font-size:11px;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;"
            "border-radius:6px;padding:6px 10px;font-weight:600;"
        )
        cl.addWidget(count_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 4, 2, 0)
        layout.setSpacing(10)

        for idx, record in enumerate(self.timeline_records):
            button = QPushButton(self._node_text(record, idx + 1))
            button.setCursor(Qt.PointingHandCursor)
            button.setCheckable(True)
            button.setMinimumHeight(90)
            button.setStyleSheet(self._node_style(record, active=False))
            source_path = _resolve_media_path(record.get("source_image_path"))
            if source_path:
                pixmap = QPixmap(source_path)
                if not pixmap.isNull():
                    button.setIcon(QIcon(pixmap.scaled(58, 58, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
                    button.setIconSize(QSize(58, 58))
            rid = int(record.get("id") or 0)
            button.clicked.connect(lambda checked=False, record_id=rid: self._select_record(record_id))
            layout.addWidget(button)
            self._node_buttons[rid] = button

        layout.addStretch(1)
        scroll.setWidget(host)
        cl.addWidget(scroll, 1)
        return card

    def _node_text(self, record: dict, index: int) -> str:
        result = _display_severity(record)
        follow_up_flag = str(record.get("screening_type") or "").strip().lower() == "follow_up"
        badge = "  🔄 Follow-up" if follow_up_flag else "  🆕 Initial"
        return "\n".join(
            [
                f"#{index}  ·  {_format_screening_datetime_label(record.get('screened_at'))}",
                f"{record.get('eyes') or 'Eye not set'}  |  {result}{badge}",
            ]
        )

    def _node_style(self, record: dict, active: bool) -> str:
        _, color = _risk_status_for(_display_severity(record))
        bg = "#eff6ff" if active else "#ffffff"
        shadow_border = f"3px solid {color}" if active else "1px solid #e2e8f0"
        return (
            "QPushButton{"
            f"background:{bg};color:#0f172a;border:{shadow_border};border-left:6px solid {color};"
            "border-radius:10px;padding:12px 14px;text-align:left;font-weight:600;font-size:11px;"
            "}"
            "QPushButton:hover{background:#f8fafc;border-color:" + color + ";}"
        )

    # ══════════════════════════════════════════════════════════════════════
    # DETAILS + IMAGES COLUMN
    # ══════════════════════════════════════════════════════════════════════
    def _build_screening_analysis_panel(self) -> QGroupBox:
        panel = QGroupBox("Screening Analysis")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(12)

        self.analysis_heading = QLabel("")
        self.analysis_heading.setStyleSheet(
            "font-size:13px;font-weight:800;color:#0f172a;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #eff6ff,stop:1 #f8fafc);"
            "border:1px solid #dbeafe;border-radius:12px;padding:10px 14px;"
        )
        layout.addWidget(self.analysis_heading)

        diagnosis_card = QFrame()
        diagnosis_card.setStyleSheet(
            "QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;}"
        )
        dl = QHBoxLayout(diagnosis_card)
        dl.setContentsMargins(14, 12, 14, 12)
        dl.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(4)
        diag_k = QLabel("DIAGNOSIS")
        diag_k.setStyleSheet("font-size:10px;font-weight:900;color:#0ea5e9;letter-spacing:1.2px;background:transparent;border:none;")
        left.addWidget(diag_k)
        self._analysis_diag_lbl = QLabel("—")
        self._analysis_diag_lbl.setStyleSheet("font-size:28px;font-weight:900;color:#0f172a;background:transparent;border:none;")
        left.addWidget(self._analysis_diag_lbl)
        self._analysis_risk_badge = QLabel("")
        self._analysis_risk_badge.setAlignment(Qt.AlignCenter)
        self._analysis_risk_badge.setMaximumHeight(26)
        left.addWidget(self._analysis_risk_badge, 0, Qt.AlignLeft)
        dl.addLayout(left, 1)

        metrics = QFrame()
        metrics.setStyleSheet("QFrame{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;}")
        ml = QVBoxLayout(metrics)
        ml.setContentsMargins(12, 10, 12, 10)
        ml.setSpacing(10)
        mk = QLabel("AI METRICS")
        mk.setStyleSheet("font-size:10px;font-weight:900;color:#64748b;letter-spacing:1.2px;background:transparent;border:none;")
        ml.addWidget(mk)

        self._conf_row = self._build_metric_bar("AI Confidence", "#16a34a")
        self._unc_row = self._build_metric_bar("Uncertainty", "#f59e0b")
        ml.addWidget(self._conf_row)
        ml.addWidget(self._unc_row)
        dl.addWidget(metrics, 0)

        layout.addWidget(diagnosis_card)

        layout.addWidget(self._build_images_card(), 1)
        return panel

    def _build_metric_bar(self, title: str, accent: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        wl = QVBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        t = QLabel(title)
        t.setStyleSheet("font-size:11px;font-weight:700;color:#334155;background:transparent;border:none;")
        v = QLabel("—")
        v.setObjectName(f"val_{title.replace(' ', '_')}")
        v.setStyleSheet(f"font-size:12px;font-weight:900;color:{accent};background:transparent;border:none;font-family:'Consolas','Cascadia Mono',monospace;")
        v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(t)
        top.addWidget(v, 1)
        wl.addLayout(top)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        bar.setStyleSheet(
            "QProgressBar{background:#e2e8f0;border:none;border-radius:5px;}"
            f"QProgressBar::chunk{{background:{accent};border-radius:5px;}}"
        )
        bar.setObjectName(f"bar_{title.replace(' ', '_')}")
        wl.addWidget(bar)
        return w

    def _build_clinical_context_panel(self) -> QGroupBox:
        panel = QGroupBox("Clinical Context")
        panel.setFixedWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(12)

        optional_k = QLabel("OPTIONAL COMMENTS")
        optional_k.setStyleSheet("font-size:10px;font-weight:900;color:#64748b;letter-spacing:1.1px;background:transparent;border:none;")
        layout.addWidget(optional_k)

        self._ctx_optional = QTextEdit()
        self._ctx_optional.setReadOnly(True)
        self._ctx_optional.setPlaceholderText("No optional comments available.")
        self._ctx_optional.setMinimumHeight(160)
        self._ctx_optional.setStyleSheet(
            "QTextEdit{background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:10px;"
            "font-size:12px;color:#0f172a;}"
        )
        layout.addWidget(self._ctx_optional, 1)

        override_k = QLabel("OVERRIDE COMMENTS")
        override_k.setStyleSheet("font-size:10px;font-weight:900;color:#64748b;letter-spacing:1.1px;background:transparent;border:none;")
        layout.addWidget(override_k)

        self._ctx_override = QLabel("—")
        self._ctx_override.setWordWrap(True)
        self._ctx_override.setStyleSheet(
            "background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:10px;"
            "font-size:12px;color:#334155;"
        )
        layout.addWidget(self._ctx_override, 0)
        return panel

    def _build_diagnosis_card(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #f0f9ff,stop:1 #e0f2fe);"
            "border:1px solid #bae6fd;border-radius:12px;}"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 12, 16, 14)
        fl.setSpacing(6)

        ttl = QLabel("🎯  DIAGNOSIS")
        ttl.setStyleSheet("font-size:10px;font-weight:800;color:#0369a1;letter-spacing:1.2px;background:transparent;border:none;")
        fl.addWidget(ttl)

        self._det_severity_lbl = QLabel("—")
        self._det_severity_lbl.setStyleSheet("font-size:22px;font-weight:800;color:#0f172a;background:transparent;border:none;")
        fl.addWidget(self._det_severity_lbl)

        self._det_risk_badge = QLabel("")
        self._det_risk_badge.setMaximumHeight(24)
        self._det_risk_badge.setAlignment(Qt.AlignCenter)
        fl.addWidget(self._det_risk_badge, 0, Qt.AlignLeft)
        return frame

    def _build_metrics_card(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;}"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 12, 16, 14)
        fl.setSpacing(10)

        ttl = QLabel("📊  AI METRICS")
        ttl.setStyleSheet("font-size:10px;font-weight:800;color:#64748b;letter-spacing:1.2px;background:transparent;border:none;")
        fl.addWidget(ttl)

        for attr, label_text, color in (("_det_conf_lbl", "Confidence", "#16a34a"), ("_det_unc_lbl", "Uncertainty", "#dc2626")):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(f"{label_text}")
            lbl.setStyleSheet(f"font-size:11px;font-weight:600;color:{color};background:transparent;border:none;min-width:80px;")
            val = QLabel("—")
            val.setStyleSheet(f"font-size:16px;font-weight:800;color:{color};background:transparent;border:none;")
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            setattr(self, attr, val)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            fl.addLayout(row)
        return frame

    def _build_classification_row(self) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        tiles = (
            ("🤖 AI RESULT", "_det_ai_lbl", "#8b5cf6"),
            ("👨‍⚕️ DOCTOR", "_det_doctor_lbl", "#0ea5e9"),
            ("✅ FINAL (ICDR)", "_det_final_lbl", "#16a34a"),
        )
        for label_text, attr, color in tiles:
            tile = QFrame()
            tile.setStyleSheet(
                "QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;}"
                f"QFrame:hover{{border-color:{color};}}"
            )
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(12, 10, 12, 10)
            tl.setSpacing(4)
            kl = QLabel(label_text)
            kl.setStyleSheet(f"font-size:9px;font-weight:800;color:{color};letter-spacing:0.6px;background:transparent;border:none;")
            vl_lbl = QLabel("—")
            vl_lbl.setStyleSheet("font-size:12px;font-weight:700;color:#1e293b;background:transparent;border:none;")
            vl_lbl.setWordWrap(True)
            setattr(self, attr, vl_lbl)
            tl.addWidget(kl)
            tl.addWidget(vl_lbl)
            rl.addWidget(tile, 1)
        return row

    def _build_clinical_card(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;}")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 12, 16, 14)
        fl.setSpacing(8)

        ttl = QLabel("🩺  CLINICAL DETAILS")
        ttl.setStyleSheet("font-size:10px;font-weight:800;color:#64748b;letter-spacing:1.2px;background:transparent;border:none;")
        fl.addWidget(ttl)

        rows = (
            ("_det_decision_lbl", "Decision Mode"),
            ("_det_symptoms_lbl", "Symptoms"),
            ("_det_findings_lbl", "Findings"),
            ("_det_notes_lbl", "Clinical Notes"),
        )
        for attr, label_text in rows:
            w = QWidget()
            w.setStyleSheet("background:transparent;")
            wl = QHBoxLayout(w)
            wl.setContentsMargins(0, 2, 0, 2)
            wl.setSpacing(12)
            kl = QLabel(f"{label_text}")
            kl.setStyleSheet(
                "font-weight:700;color:#64748b;font-size:11px;min-width:108px;max-width:108px;"
                "background:transparent;border:none;"
            )
            kl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            vl_lbl = QLabel("—")
            vl_lbl.setStyleSheet("color:#1e293b;font-size:12px;background:transparent;border:none;font-weight:500;")
            vl_lbl.setWordWrap(True)
            setattr(self, attr, vl_lbl)
            wl.addWidget(kl, 0)
            wl.addWidget(vl_lbl, 1)
            fl.addWidget(w)
        return frame

    def _build_meta_card(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #fefce8,stop:1 #fef9c3);"
            "border:1px solid #fde68a;border-radius:12px;}"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 10, 16, 12)
        fl.setSpacing(4)

        ttl = QLabel("🔖  SCREENING METADATA")
        ttl.setStyleSheet("font-size:10px;font-weight:800;color:#92400e;letter-spacing:1.2px;background:transparent;border:none;")
        fl.addWidget(ttl)

        self._det_type_lbl = QLabel("")
        self._det_type_lbl.setStyleSheet("color:#78350f;font-size:12px;font-weight:700;background:transparent;border:none;")
        self._det_prev_ref_lbl = QLabel("")
        self._det_prev_ref_lbl.setStyleSheet("color:#78350f;font-size:12px;background:transparent;border:none;")
        fl.addWidget(self._det_type_lbl)
        fl.addWidget(self._det_prev_ref_lbl)
        self._det_meta_frame = frame
        return frame

    def _build_images_card(self) -> QGroupBox:
        card = QGroupBox("Fundus Images")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 18, 14, 14)
        cl.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(12)

        self.source_preview = QLabel("Loading fundus image...")
        self.heatmap_preview = QLabel("Loading Grad-CAM...")

        for preview, label_text, icon in (
            (self.source_preview, "FUNDUS IMAGE", "📷"),
            (self.heatmap_preview, "GRAD-CAM HEATMAP", "🔥"),
        ):
            wrap = QWidget()
            wrap.setStyleSheet("background:transparent;")
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(6)
            cap = QLabel(f"{icon}  {label_text}")
            cap.setStyleSheet(
                "font-size:10px;font-weight:800;color:#475569;letter-spacing:0.8px;"
                "background:transparent;border:none;padding:2px 0;"
            )
            preview.setAlignment(Qt.AlignCenter)
            preview.setMinimumHeight(220)
            preview.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #0f172a,stop:1 #1e293b);"
                "color:#94a3b8;border:1px solid #334155;border-radius:12px;font-size:12px;font-style:italic;"
            )
            wl.addWidget(cap)
            wl.addWidget(preview, 1)
            row.addWidget(wrap, 1)
        cl.addLayout(row)
        return card

    # ══════════════════════════════════════════════════════════════════════
    # ACTION BAR
    # ══════════════════════════════════════════════════════════════════════
    def _build_action_bar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;}")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(16, 12, 16, 12)
        hl.setSpacing(10)

        styles = {
            "primary": (
                "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #2563eb,stop:1 #1d4ed8);"
                "color:#fff;border:none;border-radius:8px;font-weight:700;padding:9px 20px;}"
                "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1d4ed8,stop:1 #1e3a8a);}"
            ),
            "success": (
                "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #16a34a,stop:1 #15803d);"
                "color:#fff;border:none;border-radius:8px;font-weight:700;padding:9px 20px;}"
                "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #15803d,stop:1 #166534);}"
            ),
            "warning": (
                "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #f59e0b,stop:1 #d97706);"
                "color:#fff;border:none;border-radius:8px;font-weight:700;padding:9px 20px;}"
                "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #d97706,stop:1 #b45309);}"
                "QPushButton:disabled{background:#fcd34d;color:#ffffff;}"
            ),
            "neutral": (
                "QPushButton{background:#f1f5f9;color:#334155;border:1px solid #cbd5e1;"
                "border-radius:8px;font-weight:600;padding:9px 20px;}"
                "QPushButton:hover{background:#e2e8f0;}"
            ),
            "close": (
                "QPushButton{background:#ffffff;color:#64748b;border:1px solid #e2e8f0;"
                "border-radius:8px;font-weight:600;padding:9px 20px;}"
                "QPushButton:hover{background:#fef2f2;color:#b91c1c;border-color:#fecaca;}"
            ),
        }

        follow_up_btn = QPushButton("➕  New Follow-Up")
        follow_up_btn.setStyleSheet(styles["success"])
        follow_up_btn.clicked.connect(self._handle_follow_up)

        report_btn = QPushButton("📄  View Full Report")
        report_btn.setStyleSheet(styles["primary"])
        report_btn.clicked.connect(self._handle_view_report)

        compare_btn = QPushButton("⇄  Compare Screenings")
        compare_btn.setEnabled(len(self.timeline_records) >= 2)
        compare_btn.setStyleSheet(styles["warning"])
        compare_btn.clicked.connect(self._handle_compare)

        export_btn = QPushButton("⬇  Export History")
        export_btn.setStyleSheet(styles["neutral"])
        export_btn.clicked.connect(self._handle_export)

        close_btn = QPushButton("✕  Close")
        close_btn.setStyleSheet(styles["close"])
        close_btn.clicked.connect(self.accept)

        for btn in (follow_up_btn, report_btn, compare_btn, export_btn):
            btn.setMinimumHeight(40)
            hl.addWidget(btn)
        hl.addStretch(1)
        close_btn.setMinimumHeight(40)
        hl.addWidget(close_btn)
        return bar

    # ══════════════════════════════════════════════════════════════════════
    # PROGRESSION LOGIC
    # ══════════════════════════════════════════════════════════════════════
    def _build_progression_summary_lines(self) -> list[str]:
        if not self.timeline_records:
            return ["No screening history available."]

        severities = [_display_severity(record) for record in self.timeline_records]
        condensed = []
        for severity in severities:
            if not condensed or condensed[-1] != severity:
                condensed.append(severity)

        initial = condensed[0] if condensed else "Pending"
        latest = condensed[-1] if condensed else "Pending"
        risk_text, _ = _risk_status_for(latest)

        if len(self.timeline_records) < 2:
            return [
                f"Initial condition: {initial}",
                f"Risk status: {risk_text}",
            ]

        latest_date = _parse_datetime_value(self.timeline_records[-1].get("screened_at"))
        first_date = _parse_datetime_value(self.timeline_records[0].get("screened_at"))
        day_span = (latest_date - first_date).days if latest_date and first_date else None

        start_rank = _severity_rank_for(initial)
        end_rank = _severity_rank_for(latest)

        if end_rank - start_rank > 1 and day_span is not None and day_span <= 180:
            trend = "Trend: Rapid deterioration"
        elif end_rank > start_rank:
            trend = "Trend: Progressive worsening"
        elif end_rank < start_rank:
            trend = "Trend: Improving"
        else:
            trend = "Trend: Stable disease pattern"

        return [
            f"Initial condition: {initial}",
            f"Progression: {' → '.join(condensed)}",
            trend,
            f"Risk status: {risk_text}",
        ]

    # ══════════════════════════════════════════════════════════════════════
    # RECORD SELECTION
    # ══════════════════════════════════════════════════════════════════════
    def _select_record(self, record_id: int):
        chosen = next((record for record in self.timeline_records if int(record.get("id") or 0) == int(record_id)), None)
        if not chosen:
            return

        self._selected_record = chosen
        for rid, button in self._node_buttons.items():
            is_active = rid == int(record_id)
            button.setChecked(is_active)
            target_record = chosen if is_active else next(
                record for record in self.timeline_records if int(record.get("id") or 0) == rid
            )
            button.setStyleSheet(self._node_style(target_record, is_active))

        result = _display_severity(chosen)
        _, _, conf_pct, unc_pct = _parse_confidence_metrics(chosen.get("confidence"))
        eye_label = str(chosen.get("eyes") or "Eye")
        when = _format_screening_datetime_label(chosen.get("screened_at"))
        self.analysis_heading.setText(f"{when}     |     {eye_label}")

        # Diagnosis + risk
        risk_text, risk_color = _risk_status_for(result)
        self._analysis_diag_lbl.setText((result or "Pending").upper() if result else "PENDING")
        self._analysis_diag_lbl.setStyleSheet(f"font-size:28px;font-weight:900;color:{risk_color};background:transparent;border:none;")
        self._analysis_risk_badge.setText(f"  {risk_text.upper()}  ")
        self._analysis_risk_badge.setStyleSheet(
            f"background:{risk_color};color:#ffffff;border:none;"
            "border-radius:6px;font-weight:800;font-size:10px;padding:3px 12px;letter-spacing:0.8px;"
        )

        # Metrics bars
        self._set_metric_bar(self._conf_row, "AI_Confidence", conf_pct, "#16a34a")
        self._set_metric_bar(self._unc_row, "Uncertainty", unc_pct, "#f59e0b")

        # Clinical context (right sidebar)
        doctor_notes = str(chosen.get("doctor_findings") or chosen.get("notes") or "").strip()
        override_reason = str(chosen.get("override_justification") or "").strip()
        accepted_raw = chosen.get("doctor_accepted_ai")
        accepted = str(accepted_raw).strip().lower() in {"1", "true", "yes"}

        self._ctx_optional.setPlainText(doctor_notes if doctor_notes else "No optional comments recorded.")
        if accepted:
            self._ctx_override.setText("Doctor Accepted AI Result")
        else:
            self._ctx_override.setText(override_reason if override_reason else "—")

        self._set_preview_image(self.source_preview, chosen.get("source_image_path"), "Fundus image unavailable")
        self._set_preview_image(self.heatmap_preview, chosen.get("heatmap_image_path"), "Grad-CAM unavailable")

    def _set_metric_bar(self, row_widget: QWidget, key: str, value: float | None, accent: str):
        val_label: QLabel | None = row_widget.findChild(QLabel, f"val_{key}")
        bar: QProgressBar | None = row_widget.findChild(QProgressBar, f"bar_{key}")
        if val_label is not None:
            val_label.setStyleSheet(
                f"font-size:12px;font-weight:900;color:{accent};background:transparent;border:none;"
                "font-family:'Consolas','Cascadia Mono',monospace;"
            )
            val_label.setText(f"{value:.1f}%" if value is not None else "—")
        if bar is not None:
            bar.setValue(int(max(0, min(100, value))) if value is not None else 0)

    def _build_next_steps_text(self, severity: str) -> str:
        sev = _normalize_severity(severity)
        if sev in ("", "Pending"):
            return "Await final interpretation. If symptoms are present, arrange clinician review."
        if sev == "No DR":
            return "Routine follow-up: repeat screening in 12 months. Reinforce glycemic/BP control and lifestyle counseling."
        if sev == "Mild DR":
            return "Schedule follow-up screening in 6–12 months. Consider ophthalmology referral based on risk factors and symptoms."
        if sev == "Moderate DR":
            return "Recommend ophthalmology referral. Follow-up in 3–6 months and optimize systemic risk control."
        if sev == "Severe DR":
            return "Urgent ophthalmology referral. Consider expedited evaluation and treatment planning."
        if sev == "Proliferative DR":
            return "Immediate ophthalmology referral (same-day if possible). High risk for vision-threatening disease."
        return "Clinical follow-up recommended based on the above findings."

    def _set_preview_image(self, label: QLabel, image_value: str, fallback_text: str):
        label.setPixmap(QPixmap())
        label.setText(fallback_text)
        path = _resolve_media_path(image_value)
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        label.setText("")
        label.setPixmap(pixmap.scaled(460, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ══════════════════════════════════════════════════════════════════════
    # EVENT HANDLERS
    # ══════════════════════════════════════════════════════════════════════
    def _handle_follow_up(self):
        if callable(self._on_follow_up):
            self._on_follow_up(self._selected_record)

    def _handle_view_report(self):
        if callable(self._on_view_report):
            self._on_view_report(self._selected_record)

    def _handle_compare(self):
        if callable(self._on_compare):
            self._on_compare(self.timeline_records)

    def _handle_export(self):
        if callable(self._on_export):
            self._on_export(self.timeline_records)


class PatientDetailsDialog(QDialog):
    """Read-only dialog displaying full patient screening details without fundus image."""

    def __init__(self, patient_record: dict, parent=None):
        super().__init__(parent)
        self.record = patient_record
        self.setWindowTitle(f"Patient Details - {patient_record.get('name', 'Unknown')}")
        self.resize(700, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel(f"{patient_record.get('name', 'N/A')}")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#1f4f77;")
        layout.addWidget(title)

        # Create scrollable content area
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(16)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        # Helper function to create field rows
        def add_field(label_text: str, value_text: str):
            row = QHBoxLayout()
            row.setSpacing(12)
            label = QLabel(f"{label_text}:")
            label.setStyleSheet("font-weight:600;color:#3f7ca7;min-width:150px;")
            value = QLabel(value_text)
            value.setStyleSheet("color:#475569;word-wrap:on;")
            value.setWordWrap(True)
            row.addWidget(label)
            row.addWidget(value, 1)
            content_layout.addLayout(row)
        
        def add_section(section_title: str):
            sep = QLabel(section_title.upper())
            sep.setStyleSheet("font-size:12px;font-weight:700;color:#3f7ca7;margin-top:8px;letter-spacing:1px;")
            content_layout.addWidget(sep)
        
        # Patient Information Section
        add_section("Patient Information")
        add_field("Patient ID", str(patient_record.get("patient_id") or "N/A"))
        add_field("Name", str(patient_record.get("name") or "N/A"))
        add_field("Age", str(patient_record.get("age") or "N/A"))
        add_field("Date of Birth", str(patient_record.get("birthdate") or "N/A"))
        add_field("Sex", str(patient_record.get("sex") or "N/A"))
        add_field("Contact", str(patient_record.get("contact") or "N/A"))
        add_field("Email", str(patient_record.get("email") or "N/A"))
        add_field("Address", str(patient_record.get("address") or "N/A"))
        add_field("Eye Screened", str(patient_record.get("eyes") or "N/A"))
        
        # Vital Signs Section
        add_section("Vital Signs & Measurements")
        add_field("Height (cm)", str(patient_record.get("height") or "N/A") + (" cm" if patient_record.get("height") else ""))
        add_field("Weight (kg)", str(patient_record.get("weight") or "N/A") + (" kg" if patient_record.get("weight") else ""))
        add_field("BMI", str(patient_record.get("bmi") or "N/A"))
        add_field("Visual Acuity - Left", str(patient_record.get("visual_acuity_left") or "N/A"))
        add_field("Visual Acuity - Right", str(patient_record.get("visual_acuity_right") or "N/A"))
        
        bp_sys = patient_record.get("blood_pressure_systolic") or "—"
        bp_dia = patient_record.get("blood_pressure_diastolic") or "—"
        add_field("Blood Pressure", f"{bp_sys}/{bp_dia} mmHg")
        
        fbs = patient_record.get("fasting_blood_sugar") or "N/A"
        rbs = patient_record.get("random_blood_sugar") or "N/A"
        add_field("Blood Glucose (FBS/RBS)", f"{fbs} / {rbs} mg/dL")
        
        # Symptoms Section
        add_section("Symptoms")
        symptoms = []
        if _is_truthy_flag(patient_record.get("symptom_blurred_vision")):
            symptoms.append("Blurred vision")
        if _is_truthy_flag(patient_record.get("symptom_floaters")):
            symptoms.append("Floaters")
        if _is_truthy_flag(patient_record.get("symptom_flashes")):
            symptoms.append("Flashes")
        if _is_truthy_flag(patient_record.get("symptom_vision_loss")):
            symptoms.append("Vision loss")
        symptom_text = ", ".join(symptoms) if symptoms else "None reported"
        add_field("Reported Symptoms", symptom_text)
        
        # Clinical History Section
        add_section("Diabetic History")
        add_field("Diabetes Type", str(patient_record.get("diabetes_type") or "N/A"))
        add_field("Diagnosed Date", str(patient_record.get("diabetes_diagnosis_date") or "N/A"))
        add_field("Duration", str(patient_record.get("duration") or "N/A"))
        add_field("Treatment Regimen", str(patient_record.get("treatment_regimen") or "N/A"))
        add_field("Family History of Diabetes", str(patient_record.get("prev_dr_stage") or "N/A"))
        
        # Screening Result Section
        add_section("Screening Result")
        add_field("AI Classification", str(patient_record.get("ai_classification") or patient_record.get("result") or "N/A"))
        add_field("Doctor Classification", str(patient_record.get("doctor_classification") or patient_record.get("result") or "N/A"))
        add_field("Final Diagnosis", "Based on ICDR Severity Scale")
        add_field("Decision Mode", str(patient_record.get("decision_mode") or "accepted").title())
        # Keep two separate comment channels (matches Screening Results UI):
        # - Override justification (required only when overriding)
        # - Additional doctor comments (free text)
        override_reason = str(patient_record.get("override_justification") or "").strip()
        if override_reason:
            add_field("Override Justification", override_reason)
        doctor_comments = str(patient_record.get("doctor_findings") or "").strip()
        if doctor_comments:
            add_field("Doctor Comments", doctor_comments)
        add_field("Confidence", str(patient_record.get("confidence") or "N/A"))
        add_field("Screened At", str(patient_record.get("screened_at") or "N/A"))
        add_field("Screened By", str(patient_record.get("original_screener_name") or patient_record.get("original_screener_username") or "N/A"))
        
        # Clinical Notes Section
        notes = patient_record.get("notes") or ""
        if notes and notes.strip():
            add_section("Clinical Notes")
            notes_label = QLabel(str(notes))
            notes_label.setWordWrap(True)
            notes_label.setStyleSheet("color:#475569;background:#f6f8fb;border:1px solid #d3dae3;border-radius:6px;padding:10px;")
            content_layout.addWidget(notes_label)
        
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(36)
        close_btn.setStyleSheet(
            "QPushButton{background:#1f6fe5;color:#ffffff;border:1px solid #1a5fc4;border-radius:6px;}"
            "QPushButton:hover{background:#1b63cf;}"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        
        self.setStyleSheet("QDialog{background:#ffffff;}")


class ReferralDetailDialog(QDialog):
    """Dialog displaying referred patient details WITH fundus image for review."""

    def __init__(self, patient_record: dict, parent=None):
        super().__init__(parent)
        self.record = patient_record
        self.setWindowTitle(f"Referral Review - {patient_record.get('name', 'Unknown')}")
        self.resize(1000, 700)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Left side: Patient Details (scrollable)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(left_scroll.Shape.NoFrame)
        left_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        left_scroll.setMaximumWidth(400)

        left_content = QWidget()
        left_content.setStyleSheet("background:transparent;")
        left_layout = QVBoxLayout(left_content)
        left_layout.setSpacing(14)
        left_layout.setContentsMargins(0, 0, 0, 0)

        def add_field(label_text: str, value_text: str):
            row = QHBoxLayout()
            row.setSpacing(12)
            label = QLabel(f"{label_text}:")
            label.setStyleSheet("font-weight:600;color:#3f7ca7;min-width:120px;")
            value = QLabel(value_text)
            value.setStyleSheet("color:#475569;word-wrap:on;")
            value.setWordWrap(True)
            row.addWidget(label)
            row.addWidget(value, 1)
            left_layout.addLayout(row)

        def add_section(section_title: str):
            sep = QLabel(section_title.upper())
            sep.setStyleSheet("font-size:11px;font-weight:700;color:#3f7ca7;margin-top:6px;letter-spacing:0.8px;")
            left_layout.addWidget(sep)

        add_section("Patient Information")
        add_field("Patient ID", str(patient_record.get("patient_id") or "N/A"))
        add_field("Name", str(patient_record.get("name") or "N/A"))
        add_field("Age", str(patient_record.get("age") or "N/A"))
        add_field("Sex", str(patient_record.get("sex") or "N/A"))
        add_field("Email", str(patient_record.get("email") or "N/A"))
        add_field("Address", str(patient_record.get("address") or "N/A"))

        add_section("Vital Signs")
        add_field("Height (cm)", str(patient_record.get("height") or "N/A") + (" cm" if patient_record.get("height") else ""))
        add_field("Weight (kg)", str(patient_record.get("weight") or "N/A") + (" kg" if patient_record.get("weight") else ""))
        add_field("BMI", str(patient_record.get("bmi") or "N/A"))

        bp_sys = patient_record.get("blood_pressure_systolic") or "-"
        bp_dia = patient_record.get("blood_pressure_diastolic") or "-"
        add_field("Blood Pressure", f"{bp_sys}/{bp_dia} mmHg")

        add_section("Diabetic History")
        add_field("Diabetes Type", str(patient_record.get("diabetes_type") or "N/A"))
        add_field("Duration", str(patient_record.get("duration") or "N/A"))
        add_field("Diagnosed Date", str(patient_record.get("diabetes_diagnosis_date") or "N/A"))
        add_field("Treatment Regimen", str(patient_record.get("treatment_regimen") or "N/A"))
        add_field("Family History of Diabetes", str(patient_record.get("prev_dr_stage") or "N/A"))

        add_section("Screening Result")
        add_field("AI Classification", str(patient_record.get("ai_classification") or patient_record.get("result") or "N/A"))
        add_field("Doctor Classification", str(patient_record.get("doctor_classification") or patient_record.get("result") or "N/A"))
        add_field("Final Diagnosis", "Based on ICDR Severity Scale")
        add_field("Decision Mode", str(patient_record.get("decision_mode") or "accepted").title())
        # Keep two separate comment channels (matches Screening Results UI):
        # - Override justification (required only when overriding)
        # - Additional doctor comments (free text)
        override_reason = str(patient_record.get("override_justification") or "").strip()
        if override_reason:
            add_field("Override Justification", override_reason)
        doctor_comments = str(patient_record.get("doctor_findings") or "").strip()
        if doctor_comments:
            add_field("Doctor Comments", doctor_comments)
        add_field("Confidence", str(patient_record.get("confidence") or "N/A"))
        add_field("Screened By", str(patient_record.get("original_screener_name") or patient_record.get("original_screener_username") or "N/A"))
        add_field("Screened At", str(patient_record.get("screened_at") or "N/A"))

        notes_raw = str(patient_record.get("notes") or "").strip()
        if notes_raw:
            notes_lines = [line.strip() for line in notes_raw.splitlines() if line.strip()]
            message_lines = [line for line in notes_lines if line.lower().startswith("message on this patient")]
            comment_lines = [
                line for line in notes_lines if line.lower().startswith("additional comments:")
            ]
            status_lines = [
                line for line in notes_lines if line not in message_lines and line not in comment_lines
            ]

            if comment_lines:
                add_section("Additional Comments")
                cleaned_comments = [
                    line.split(":", 1)[1].strip() if ":" in line else line for line in comment_lines
                ]
                comments_label = QLabel("\n".join(cleaned_comments))
                comments_label.setWordWrap(True)
                comments_label.setStyleSheet(
                    "color:#1f2937;background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:10px;"
                )
                left_layout.addWidget(comments_label)

            if message_lines:
                add_section("Message History")
                message_label = QLabel("\n".join(reversed(message_lines)))
                message_label.setWordWrap(True)
                message_label.setStyleSheet(
                    "color:#334155;background:#eef6ff;border:1px solid #bfdbfe;border-radius:6px;padding:10px;"
                )
                left_layout.addWidget(message_label)

            if status_lines:
                add_section("Status Notes")
                status_label = QLabel("\n".join(reversed(status_lines)))
                status_label.setWordWrap(True)
                status_label.setStyleSheet(
                    "color:#475569;background:#f6f8fb;border:1px solid #d3dae3;border-radius:6px;padding:10px;"
                )
                left_layout.addWidget(status_label)

        left_layout.addStretch()
        left_scroll.setWidget(left_content)
        layout.addWidget(left_scroll)

        # Right side: Fundus Image
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setSpacing(10)
        right_layout.setContentsMargins(0, 0, 0, 0)

        image_title = QLabel("FUNDUS IMAGE")
        image_title.setStyleSheet("font-size:11px;font-weight:700;color:#3f7ca7;letter-spacing:0.8px;")
        right_layout.addWidget(image_title)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setStyleSheet("border:1px solid #d3dae3;border-radius:6px;background:#f6f8fb;")
        image_label.setMinimumSize(500, 500)

        source_image = patient_record.get("source_image_path") or ""
        if source_image and os.path.isfile(source_image):
            try:
                pixmap = QPixmap(source_image)
                if not pixmap.isNull():
                    scaled = pixmap.scaledToHeight(500, Qt.SmoothTransformation)
                    image_label.setPixmap(scaled)
                else:
                    image_label.setText("Image could not be loaded")
            except Exception:
                image_label.setText("Error loading image")
        else:
            image_label.setText("No fundus image available")

        right_layout.addWidget(image_label, 1)

        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(36)
        close_btn.setStyleSheet(
            "QPushButton{background:#1f6fe5;color:#ffffff;border:1px solid #1a5fc4;border-radius:6px;}"
            "QPushButton:hover{background:#1b63cf;}"
        )
        close_btn.clicked.connect(self.accept)
        right_layout.addWidget(close_btn)

        layout.addWidget(right_container)
        self.setStyleSheet("QDialog{background:#ffffff;}")


class ArchivedRecordsDialog(QDialog):
    """Dialog for reviewing archived patient records."""

    def __init__(self, reports_page: "ReportsPage"):
        super().__init__(reports_page)
        self.reports_page = reports_page
        self._rows = []
        self._filtered_rows = []
        self._record_lookup = {}

        self.setWindowTitle("Archived Patient Records")
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Archived Patient Records")
        title.setStyleSheet("font-size:22px;font-weight:700;color:#007bff;")
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel("Review archived screenings and restore them back into the active dashboard and reports.")
        subtitle.setStyleSheet("font-size:13px;color:#6c757d;")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search archived records by patient ID, name, result, or archived by")
        self.search_input.textChanged.connect(self.apply_filters)
        controls.addWidget(self.search_input, 1)
        self.count_label = QLabel("0 archived")
        self.count_label.setStyleSheet("color:#6c757d;font-size:12px;")
        self.count_label.setAlignment(Qt.AlignCenter)
        controls.addWidget(self.count_label)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Patient ID", "Name", "Result", "Archived At", "Archived By"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_restore_button)
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet(
            "QPushButton{background:#dc3545;color:#fff;border:1px solid #bb2d3b;}"
            "QPushButton:hover{background:#c82333;}"
            "QPushButton:disabled{background:#f1aeb5;color:#fff;border:1px solid #ea868f;}"
        )
        self.delete_btn.clicked.connect(self.delete_selected_record)
        actions.addWidget(self.delete_btn)
        self.restore_btn = QPushButton("Restore Selected")
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self.restore_selected_record)
        actions.addWidget(self.restore_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        layout.addLayout(actions)
        self.reload_rows()

    def reload_rows(self):
        # Archived view is patient-level (one row per patient), aggregating all archived visits.
        archived_eye_rows = [r for r in self.reports_page._all_result_rows if r.get("archived_at")]
        visit_rows = group_patient_record_rows(archived_eye_rows) if archived_eye_rows else []

        patients: dict[str, dict] = {}
        for visit in visit_rows or []:
            pid = str(visit.get("patient_id") or "").strip()
            if not pid:
                continue

            existing = patients.get(pid)
            if existing is None:
                existing = {
                    "patient_id": pid,
                    "name": str(visit.get("name") or "").strip(),
                    "result": str(visit.get("result") or "").strip(),
                    "archived_at": str(visit.get("archived_at") or "").strip(),
                    "archived_by": str(visit.get("archived_by") or "").strip(),
                    "source_rows": [],
                    "visit_rows": [],
                }
                patients[pid] = existing

            # Keep aggregated rows so downstream actions can access screening history if needed.
            existing["visit_rows"].append(visit)
            existing["source_rows"].extend(list(visit.get("source_rows") or []))

            # Prefer latest non-empty name.
            if not existing.get("name") and str(visit.get("name") or "").strip():
                existing["name"] = str(visit.get("name") or "").strip()

            # Pick worst severity across visits.
            try:
                current_rank = _severity_rank_for(str(existing.get("result") or ""))
            except Exception:
                current_rank = -1
            try:
                visit_rank = _severity_rank_for(str(visit.get("result") or ""))
            except Exception:
                visit_rank = -1
            if visit_rank > current_rank:
                existing["result"] = str(visit.get("result") or "").strip()

            # Pick most recent archive stamp (and its 'archived_by').
            current_at = _parse_datetime_value(str(existing.get("archived_at") or ""))
            visit_at = _parse_datetime_value(str(visit.get("archived_at") or ""))
            if current_at is None or (visit_at is not None and visit_at > current_at):
                existing["archived_at"] = str(visit.get("archived_at") or "").strip()
                existing["archived_by"] = str(visit.get("archived_by") or "").strip()

        # Stable ordering: most recently archived first.
        self._rows = list(patients.values())
        self._rows.sort(
            key=lambda r: (_parse_datetime_value(str(r.get("archived_at") or "")) or datetime.min, str(r.get("patient_id") or "")),
            reverse=True,
        )
        # Key by patient id so restore/delete affects whole patient.
        self._record_lookup = {str(r.get("patient_id") or ""): r for r in self._rows}
        self.apply_filters()

    def apply_filters(self):
        query = self.search_input.text().strip().lower()
        filtered = []
        for row in self._rows:
            haystack = " ".join([str(row[k] or "") for k in ("patient_id","name","result","archived_at","archived_by")]).lower()
            if query and query not in haystack:
                continue
            filtered.append(row)
        self._filtered_rows = filtered
        self._render_table()

    def _render_table(self):
        self.table.setRowCount(0)
        for row in self._filtered_rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            item = QTableWidgetItem(str(row["patient_id"] or ""))
            item.setData(Qt.UserRole, str(row.get("patient_id") or ""))
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 0, item)
            name_item = QTableWidgetItem(str(row["name"] or ""))
            name_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 1, name_item)
            result_item = QTableWidgetItem(str(row["result"] or ""))
            result_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 2, result_item)
            archived_at_item = QTableWidgetItem(str(row["archived_at"] or ""))
            archived_at_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 3, archived_at_item)
            archived_by_item = QTableWidgetItem(str(row["archived_by"] or ""))
            archived_by_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 4, archived_by_item)
        self.count_label.setText(f"{len(self._filtered_rows)} archived")
        self._update_restore_button()

    def _get_selected_record(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        item = self.table.item(r, 0)
        return self._record_lookup.get(item.data(Qt.UserRole)) if item else None

    def _update_restore_button(self):
        record = self._get_selected_record()
        has = record is not None
        can_manage = bool(has and self.reports_page._can_archive_record(record))
        self.restore_btn.setEnabled(can_manage)
        self.delete_btn.setEnabled(can_manage)

    def restore_selected_record(self):
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "Restore Record", "Select an archived patient record to restore.")
            return
        if not self.reports_page._can_archive_record(record):
            owner = self.reports_page._record_owner_label(record)
            QMessageBox.warning(
                self,
                "Restore Restricted",
                f"Only the original screening doctor ({owner}) can restore this archived record.",
            )
            return
        if not self.reports_page.restore_record(record):
            return
        self.reports_page.refresh_report()
        self.reload_rows()

    def delete_selected_record(self):
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "Delete Record", "Select an archived patient record to delete.")
            return
        if not self.reports_page._can_archive_record(record):
            owner = self.reports_page._record_owner_label(record)
            QMessageBox.warning(
                self,
                "Delete Restricted",
                f"Only the original screening doctor ({owner}) can delete this archived record.",
            )
            return
        label = f"{record['name'] or 'Unknown Patient'} ({record['patient_id'] or 'No ID'})"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete Archived Record")
        box.setText(f"Permanently delete {label}?")
        box.setInformativeText("This action cannot be undone.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        if not self.reports_page.delete_archived_record(record):
            QMessageBox.warning(self, "Delete Record", "Unable to permanently delete the selected record.")
            return
        self.reports_page.refresh_report()
        self.reload_rows()


class ReportsPage(QWidget):
    """Reports page with local offline statistics."""

    def __init__(self, username: str = "", role: str = "clinician", display_name: str = "", specialization: str = ""):
        super().__init__()
        self.username = username or os.environ.get("EYESHIELD_CURRENT_USER", "")
        self.display_name = display_name or os.environ.get("EYESHIELD_CURRENT_NAME", "") or self.username
        self.role = role or os.environ.get("EYESHIELD_CURRENT_ROLE", "clinician")
        self.specialization = str(specialization or os.environ.get("EYESHIELD_CURRENT_SPECIALIZATION", "")).strip()
        _r = str(self.role or "").strip().lower()
        self.display_title = self.specialization if _r in ("clinician", "doctor") and self.specialization else self.role
        self.is_admin = self.role == "admin"
        self.is_frontdesk = _r == "frontdesk"
        # Archive controls (doctor POV).
        # Even in EMR-backed runtime, the Patient Records UI is powered by patient_records.db,
        # so clinicians/admins still need archive/restore controls for record management.
        self.can_manage_archives = (_r in {"clinician", "doctor", "admin"}) and not self.is_frontdesk
        self.records_changed_callback = None
        self.archived_records_dialog = None
        self._summary_cache = {}
        self._all_result_rows = []
        self._filtered_rows = []
        self._record_lookup = {}
        self._display_row_lookup = {}

        self.setStyleSheet("""
            QWidget {
                background: #f8fafc;
                color: #0f172a;
                font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
            QLineEdit, QComboBox {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 14px;
            }
            QLineEdit:hover, QComboBox:hover {
                border: 1px solid #3b82f6;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 2px solid #3b82f6;
                padding: 7px 13px;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                gridline-color: #f1f5f9;
                selection-background-color: #eff6ff;
                selection-color: #1e40af;
                outline: none;
            }
            QTableWidget::item {
                border-bottom: 1px solid #f1f5f9;
                padding: 12px;
            }
            QTableWidget::item:selected {
                background: #eff6ff;
                color: #1e40af;
            }
            QHeaderView::section {
                background: #f8fafc;
                color: #475569;
                font-weight: 700;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                border: none;
                border-bottom: 2px solid #e2e8f0;
                padding: 12px 16px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
                font-size: 13px;
                color: #334155;
            }
            QPushButton:hover {
                background: #f1f5f9;
                border: 1px solid #cbd5e1;
                color: #0f172a;
            }
            QPushButton:pressed {
                background: #e2e8f0;
            }
            QPushButton:disabled {
                background: #f8fafc;
                color: #94a3b8;
                border: 1px solid #f1f5f9;
            }
            QLabel#statusLabel {
                color: #64748b;
                font-size: 12px;
            }
        """)

        # ── stack: page 0 = reports table, page 1 = patient overview ────────
        self._main_stack = QStackedWidget()
        self._reports_page = QWidget()
        _outer = QVBoxLayout(self)
        _outer.setContentsMargins(0, 0, 0, 0)
        _outer.setSpacing(0)
        _outer.addWidget(self._main_stack)
        self._main_stack.addWidget(self._reports_page)

        # ── centeralized layout matching Medical Partners ────────
        centering_layout = QHBoxLayout(self._reports_page)
        centering_layout.setContentsMargins(0, 0, 0, 0)
        centering_layout.setSpacing(0)
        centering_layout.addStretch(1)

        page_container = QWidget()
        page_container.setMinimumWidth(1200)
        page_container.setMaximumWidth(1200)
        centering_layout.addWidget(page_container)
        centering_layout.addStretch(1)

        root = QVBoxLayout(page_container)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        # ── Hero Section (Title + Main Buttons) ────────
        hero = QFrame()
        hero.setObjectName("reportsHero")
        hero.setStyleSheet("""
            QFrame#reportsHero {
                background: #ffffff;
                border: 1px solid #dbeafe;
                border-radius: 12px;
            }
        """)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(16, 12, 16, 12)
        
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        
        title_lbl = QLabel("Patient Records")
        title_lbl.setStyleSheet("font-size:20px; font-weight:400; color:#1d4ed8; background:transparent;")
        header_row.addWidget(title_lbl)
        header_row.addStretch(1)

        self.export_btn = QPushButton("Export Results")
        self.export_btn.clicked.connect(self.export_summary)
        
        if self.can_manage_archives:
            self.archive_btn = QPushButton("Archive")
            self.archive_btn.clicked.connect(self.archive_selected_record)
            self.archive_btn.setEnabled(False)
            
            self.archived_records_btn = QPushButton("Archived Records")
            self.archived_records_btn.clicked.connect(self.open_archived_records_window)
            
            header_row.addWidget(self.archive_btn)
            header_row.addWidget(self.archived_records_btn)
        else:
            self.archive_btn = None
            self.archived_records_btn = None

        self.report_btn = QPushButton("Report")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self.generate_report)
        
        self.referral_btn = QPushButton("Referral")
        self.referral_btn.setEnabled(False)
        self.referral_btn.clicked.connect(self.start_referral_flow)
        
        if not getattr(self, "is_frontdesk", False):
            header_row.addWidget(self.report_btn)
            header_row.addWidget(self.referral_btn)
        else:
            header_row.addWidget(self.export_btn)
            
        hero_layout.addLayout(header_row)
        root.addWidget(hero)

        self.rescreen_btn = QPushButton("Add Follow-Up Screening")
        self.rescreen_btn.setEnabled(False)
        self.rescreen_btn.clicked.connect(self.start_frontdesk_followup if self.is_frontdesk else self.rescreen_patient)
        self.rescreen_btn.hide()

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color:#4f637a;background:#eaf1fb;border:1px solid #d4e2f3;border-radius:10px;padding:8px 12px;")
        self.status_label.hide()
        root.addWidget(self.status_label)

        # ── Controls Section (Search + Filter) ────────
        self._controls_group = QGroupBox("")
        self._controls_group.setStyleSheet("QGroupBox{border:1px solid #dbeafe; border-radius:12px; margin-top:0; padding:10px;}")
        cl = QHBoxLayout(self._controls_group)
        cl.setContentsMargins(12, 8, 12, 8)
        cl.setSpacing(14)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search patient name or ID...")
        self.search_input.setMinimumHeight(40)
        self.search_input.textChanged.connect(self.apply_filters)
        cl.addWidget(self.search_input, 1)
        
        if not getattr(self, "is_frontdesk", False):
            self.result_filter = QComboBox()
            self.result_filter.addItems(["All","No DR","Mild DR","Moderate DR","Severe DR","Proliferative DR"])
            self.result_filter.setMinimumHeight(40)
            self.result_filter.setMinimumWidth(160)
            self.result_filter.currentTextChanged.connect(self.apply_filters)
            cl.addWidget(self.result_filter)
        
        root.addWidget(self._controls_group)

        self._results_group = QGroupBox("")
        rl = QVBoxLayout(self._results_group)
        rl.setContentsMargins(18, 16, 18, 18)
        rl.setSpacing(14)

        class _PatientCellDelegate(QStyledItemDelegate):
            NAME_ROLE = int(Qt.UserRole) + 1
            PID_ROLE = int(Qt.UserRole) + 2

            def paint(self, painter: QPainter, option, index) -> None:
                painter.save()
                try:
                    opt = QStyleOptionViewItem(option)
                    self.initStyleOption(opt, index)
                    opt.text = ""

                    widget = opt.widget
                    style = widget.style() if widget is not None else QApplication.style()
                    style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

                    name = str(index.data(self.NAME_ROLE) or "—").strip() or "—"
                    
                    r = opt.rect.adjusted(12, 0, -12, 0)
                    name_rect = r

                    selected = bool(opt.state & QStyle.State_Selected)
                    name_color = QColor("#0f172a") if not selected else QColor("#0b1220")

                    name_font = QFont(opt.font)
                    name_font.setBold(False)
                    name_font.setPixelSize(14)
                    painter.setFont(name_font)
                    painter.setPen(name_color)
                    painter.drawText(name_rect, int(Qt.AlignLeft | Qt.AlignVCenter), name)
                finally:
                    painter.restore()

            def sizeHint(self, option, index):
                hint = super().sizeHint(option, index)
                hint.setHeight(max(hint.height(), 44))
                return hint

        self.results_table = QTableWidget(0, 4)
        self.results_table.setObjectName("patientRecordsTable")
        self.results_table.setHorizontalHeaderLabels(["Patient", "Risk level", "Screening Date", "Screened by"])
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setShowGrid(False)
        self.results_table.setSortingEnabled(True)
        self.results_table.setWordWrap(False)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.verticalHeader().setDefaultSectionSize(44)
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SingleSelection)
        self.results_table.itemSelectionChanged.connect(self._update_action_buttons)
        self.results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(self._open_results_context_menu)
        self.results_table.doubleClicked.connect(self._on_table_row_double_clicked)
        self.results_table.setMinimumHeight(420)
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setItemDelegateForColumn(0, _PatientCellDelegate(self.results_table))
        rl.addWidget(self.results_table)
        root.addWidget(self._results_group)

        if getattr(self, "is_frontdesk", False):
            self.setTabOrder(self.export_btn, self.report_btn)
            self.setTabOrder(self.report_btn, self.referral_btn)
            self.setTabOrder(self.referral_btn, self.search_input)
        else:
            self.setTabOrder(self.report_btn, self.referral_btn)
            self.setTabOrder(self.referral_btn, self.search_input)
        if hasattr(self, "result_filter"):
            self.setTabOrder(self.search_input, self.result_filter)
            self.setTabOrder(self.result_filter, self.results_table)
        else:
            self.setTabOrder(self.search_input, self.results_table)
        self._setup_action_buttons_ui()
        self.refresh_report()

    def _icon_path(self, filename: str) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", filename)

    def _set_button_icon(self, button: QPushButton, icon_name: str):
        icon_file = self._icon_path(icon_name)
        if os.path.exists(icon_file):
            base_icon = QIcon(icon_file)
            source = base_icon.pixmap(QSize(24, 24))
            if source.isNull():
                button.setIcon(base_icon)
                button.setIconSize(QSize(24, 24))
                return

            def _tint(color_hex: str) -> QPixmap:
                tinted = QPixmap(source.size())
                tinted.fill(Qt.GlobalColor.transparent)
                painter = QPainter(tinted)
                painter.drawPixmap(0, 0, source)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(tinted.rect(), QColor(color_hex))
                painter.end()
                return tinted

            icon = QIcon()
            icon.addPixmap(_tint("#60a5fa"), QIcon.Mode.Normal, QIcon.State.Off)
            icon.addPixmap(_tint("#3b82f6"), QIcon.Mode.Active, QIcon.State.Off)
            icon.addPixmap(_tint("#bfdbfe"), QIcon.Mode.Disabled, QIcon.State.Off)
            button.setIcon(icon)
            button.setIconSize(QSize(22, 22))

    def _setup_action_buttons_ui(self):
        self._set_button_icon(self.report_btn, "generate_report.svg")
        self._set_button_icon(self.referral_btn, "referral.svg")
        self.report_btn.setText("Report")
        self.referral_btn.setText("Referral")
        self.report_btn.setToolTip("Generate a detailed PDF report for the selected patient")
        self.referral_btn.setToolTip("Generate a referral letter PDF for the selected patient")
        if getattr(self, "is_frontdesk", False):
            self._set_button_icon(self.export_btn, "export.svg")
            self._set_button_icon(self.rescreen_btn, "rescreen.svg")
            self.export_btn.setText("Export")
            self.rescreen_btn.setText("Follow-up Screening")
            self.export_btn.setToolTip("Export currently visible report rows to CSV")
            self.rescreen_btn.setToolTip("Add a follow-up screening for the selected patient")
        if self.archived_records_btn is not None:
            self._set_button_icon(self.archived_records_btn, "archives.svg")
            self.archived_records_btn.setText("Archived Records")
            self.archived_records_btn.setToolTip("Open archived records and restore or delete entries")
        if self.archive_btn is not None:
            self._set_button_icon(self.archive_btn, "archive.svg")
            self.archive_btn.setText("Archive")
            self.archive_btn.setToolTip("Archive the selected active patient record")

        top_icon_buttons = [self.report_btn, self.referral_btn]
        if getattr(self, "is_frontdesk", False):
            top_icon_buttons.insert(0, self.export_btn)
            top_icon_buttons.append(self.rescreen_btn)
        if self.archive_btn is not None:
            top_icon_buttons.append(self.archive_btn)
        if self.archived_records_btn is not None:
            top_icon_buttons.append(self.archived_records_btn)

        for button in top_icon_buttons:
            button.setMinimumHeight(40)

    def refresh_report(self):
        try:
            rows: list[dict] = []
            for pid in emr.list_patient_ids_with_screenings():
                rows.extend(emr.list_emr_timeline_records(int(pid)))
            # Normalize legacy fields expected by the Reports UI.
            for row in rows:
                row.setdefault("archived_at", None)
                row.setdefault("archived_by", None)
                row.setdefault("archive_reason", None)
                row["result"] = row.get("final_diagnosis_icdr") or row.get("doctor_classification") or row.get("result") or ""
                # Single record unit: visit group (queue-<qid>), not legacy per-eye ids.
        except Exception as err:
            QMessageBox.warning(self, "Reports", f"Failed to load report data: {err}")
            return
        self._all_result_rows = rows
        self._record_lookup = {r["id"]: r for r in rows}
        self.apply_filters()
        if self.archived_records_dialog is not None:
            self.archived_records_dialog.reload_rows()
        self.status_label.setText("")

    # NOTE: Archive state now comes from EMR (visit-level fields on emr_queue_entries).

    @staticmethod
    def _eye_sort_key(eye_value: str) -> tuple[int, str]:
        eye = str(eye_value or "").strip().lower()
        if "right" in eye:
            return (0, eye)
        if "left" in eye:
            return (1, eye)
        return (2, eye)

    @staticmethod
    def _format_screening_datetime(value: str) -> str:
        parsed = _parse_datetime_value(value)
        if not parsed:
            return str(value or "—").strip() or "—"
        
        local_dt = _to_local_datetime(parsed)
        hour = local_dt.strftime("%I").lstrip("0") or "0"
        return f"{local_dt.strftime('%B')} {local_dt.day}, {local_dt.year} - {hour}:{local_dt.strftime('%M')} {local_dt.strftime('%p').lower()}"

    def _build_display_rows(self, rows: list[dict]) -> list[dict]:
        grouped_rows = group_patient_record_rows(rows)
        display_rows = []
        for grouped_row in grouped_rows:
            source_rows = list(grouped_row.get("source_rows") or [])
            if not source_rows:
                continue
            primary = dict(grouped_row.get("primary_record") or grouped_row)
            owner_name = str(primary.get("original_screener_name") or "").strip()
            owner_username = str(primary.get("original_screener_username") or "").strip()
            screened_by_display = self._format_doctor_label(owner_name or owner_username)
            # Archive/restore operates on patient_records.db row ids.
            # For EMR-derived rows, those are stored in `legacy_record_id` when available.
            record_ids = []
            for item in source_rows:
                lid = int(item.get("legacy_record_id") or 0)
                if lid > 0:
                    record_ids.append(lid)
            selection_key = str(grouped_row.get("screening_group_id") or f"record-{grouped_row.get('id')}")
            date_text = self._format_screening_datetime(grouped_row.get("screened_at"))
            eye_summary = str(grouped_row.get("eye_summary") or grouped_row.get("eyes") or "—")
            ai_result_text = "\n".join(
                str(item.get("ai_classification") or item.get("result") or "—")
                for item in source_rows
            )
            doctor_result_text = "\n".join(
                str(item.get("final_diagnosis_icdr") or item.get("doctor_classification") or item.get("result") or "—")
                for item in source_rows
            )
            confidence_text = str(grouped_row.get("confidence") or "—")
            combined_search = " ".join(
                [
                    str(grouped_row.get("patient_id") or ""),
                    str(grouped_row.get("name") or ""),
                    eye_summary,
                    date_text,
                    ai_result_text,
                    doctor_result_text,
                    confidence_text,
                    str(grouped_row.get("doctor_findings") or ""),
                    screened_by_display,
                ]
            ).lower()
            row = dict(grouped_row)
            row.update(
                {
                    "selection_key": selection_key,
                    "screened_at_raw": grouped_row.get("screened_at"),
                    "screened_at": date_text,
                    "screened_by": screened_by_display,
                    "confidence": confidence_text,
                    "ai_result": ai_result_text,
                    "doctor_result": doctor_result_text,
                    "result": str(grouped_row.get("result") or ""),
                    "_search_text": combined_search,
                }
            )
            display_rows.append(row)

        display_rows.sort(
            key=lambda item: (_parse_datetime_value(item.get("screened_at_raw")) or datetime.min, int(item.get("id") or 0)),
            reverse=True,
        )
        # Patient Records list should show each patient only once (latest screening).
        latest_by_patient: dict[str, dict] = {}
        for row in display_rows:
            pid = str(row.get("patient_id") or "").strip()
            if not pid:
                continue
            if pid not in latest_by_patient:
                latest_by_patient[pid] = row
        deduped = list(latest_by_patient.values())
        deduped.sort(
            key=lambda item: (_parse_datetime_value(item.get("screened_at_raw")) or datetime.min, int(item.get("id") or 0)),
            reverse=True,
        )
        self._display_row_lookup = {row["selection_key"]: row for row in deduped}
        return deduped

    def apply_filters(self):
        query = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        mode = self.result_filter.currentText() if hasattr(self, "result_filter") else "All"
        active_rows = [row for row in self._all_result_rows if not row["archived_at"]]
        display_rows = self._build_display_rows(active_rows)
        filtered = []
        for row in display_rows:
            source_rows = row.get("source_rows") or []
            if not source_rows:
                continue
            if query and query not in str(row.get("_search_text") or ""):
                continue
            result_blob = " ".join(str(item.get("result") or "") for item in source_rows).lower()
            if mode == "No DR" and "no dr" not in result_blob:
                continue
            if mode == "Mild DR" and "mild" not in result_blob:
                continue
            if mode == "Moderate DR" and "moderate" not in result_blob:
                continue
            if mode == "Severe DR" and "severe" not in result_blob:
                continue
            if mode == "Proliferative DR" and "proliferative" not in result_blob:
                continue
            filtered.append(row)
        self._filtered_rows = filtered
        self._update_summary_cards(filtered)
        self._render_results_table()

    def focus_patient_record(self, patient_id: str, *, open_overview: bool = True) -> None:
        """
        Focus a patient in the Patient Records list and optionally open the inline overview.

        Used by EMR diagnosis flow so clinicians can continue actions (referral/archive/report).
        """
        pid = str(patient_id or "").strip()
        if not pid:
            return
        if hasattr(self, "search_input"):
            self.search_input.setText(pid)
        else:
            self.refresh_report()
            self.apply_filters()

        # Select the first matching row (if any).
        row_index = 0 if hasattr(self, "_filtered_rows") and self._filtered_rows else -1
        if row_index >= 0 and hasattr(self, "results_table"):
            try:
                self.results_table.setCurrentCell(row_index, 0)
                self.results_table.selectRow(row_index)
            except Exception:
                pass
        if not open_overview:
            return
        record = self._get_selected_record()
        if not record:
            return
        timeline = self._fetch_patient_timeline_records(str(record.get("patient_id") or ""))
        if timeline:
            self._show_patient_overview(record, timeline)

    def _render_results_table(self):
        self.results_table.setSortingEnabled(False)
        self.results_table.setRowCount(0)
        for row in self._filtered_rows:
            i = self.results_table.rowCount()
            self.results_table.insertRow(i)
            patient_item = QTableWidgetItem(str(row.get("name") or row.get("patient_id") or ""))
            patient_item.setData(Qt.UserRole, row["selection_key"])
            patient_item.setData(int(Qt.UserRole) + 1, str(row.get("name") or "—"))
            patient_item.setData(int(Qt.UserRole) + 2, str(row.get("patient_id") or ""))
            patient_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.results_table.setItem(i, 0, patient_item)

            severity = _display_severity(row)
            risk_text, risk_color = _risk_status_for(severity)
            risk_item = QTableWidgetItem(risk_text)
            risk_item.setTextAlignment(Qt.AlignCenter)
            risk_item.setForeground(QColor(risk_color))
            # Aesthetic: make it bold
            f = risk_item.font()
            f.setWeight(QFont.Weight.Bold)
            risk_item.setFont(f)
            self.results_table.setItem(i, 1, risk_item)

            screened_at_item = QTableWidgetItem(str(row.get("screened_at") or ""))
            screened_at_item.setTextAlignment(Qt.AlignCenter)
            self.results_table.setItem(i, 2, screened_at_item)

            screened_by_item = QTableWidgetItem(str(row.get("screened_by") or "--"))
            screened_by_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.results_table.setItem(i, 3, screened_by_item)
        self.results_table.setSortingEnabled(True)
        for row_idx in range(self.results_table.rowCount()):
            self.results_table.setRowHeight(row_idx, 44)
        if hasattr(self, "filtered_count_label"):
            self.filtered_count_label.setText(f"Total: {len(self._filtered_rows)}")
        self._update_action_buttons()

    def _result_color_for_current_theme(self, level: str) -> QColor:
        window = self.palette().color(self.backgroundRole())
        is_dark = window.value() < 128
        if level == "high":
            return QColor("#fca5a5") if is_dark else QColor("#991b1b")
        return QColor("#86efac") if is_dark else QColor("#166534")

    def _update_summary_cards(self, rows):
        total = len(rows)
        self._summary_cache = {"total_screenings": total}

    @staticmethod
    def _format_doctor_label(name_value: str) -> str:
        name = str(name_value or "").strip()
        if not name:
            return "--"
        if name.lower().startswith("dr"):
            return name
        return f"Dr. {name}"

    @staticmethod
    def _normalize_owner_name(value: str) -> str:
        name = str(value or "").strip().lower()
        if name.startswith("dr. "):
            return name[4:].strip()
        if name.startswith("dr "):
            return name[3:].strip()
        return name

    def _is_record_owner(self, record: dict | None) -> bool:
        if not record:
            return False
        current_username = str(self.username or "").strip().lower()
        owner_username = str(record.get("original_screener_username") or "").strip().lower()
        if owner_username:
            return bool(current_username and owner_username == current_username)

        owner_name = self._normalize_owner_name(record.get("original_screener_name"))
        if not owner_name:
            return False

        current_display_name = self._normalize_owner_name(self.display_name)
        return bool(owner_name and current_display_name and owner_name == current_display_name)

    def _can_rescreen_record(self, record: dict) -> bool:
        return self._is_record_owner(record)

    def _can_archive_record(self, record: dict | None) -> bool:
        # Allow all doctors/clinicians/admins to archive any record.
        if str(self.role or "").strip().lower() in {"doctor", "clinician", "admin"}:
            return bool(record)
        return self._is_record_owner(record)

    def _can_internal_referral_record(self, record: dict) -> bool:
        return False

    def _record_owner_label(self, record: dict) -> str:
        owner_name = str(record.get("original_screener_name") or "").strip()
        owner_username = str(record.get("original_screener_username") or "").strip()
        return owner_name or owner_username or "original screener"

    def _open_results_context_menu(self, pos):
        item = self.results_table.itemAt(pos)
        if item is None:
            return
        self.results_table.selectRow(item.row())
        record = self._get_selected_record()
        if not record:
            return

        menu = QMenu(self)
        view_action = menu.addAction("View Details")
        menu.addSeparator()
        generate_action = None
        referral_action = None
        if not getattr(self, "is_frontdesk", False):
            generate_action = menu.addAction("Generate Report")
            referral_action = menu.addAction("Generate Referral")
        rescreen_action = None
        archive_action = None
        if self.archive_btn is not None:
            archive_action = menu.addAction("Archive Record")
            archive_action.setEnabled(not bool(record.get("archived_at")) and self._can_archive_record(record))

        chosen = menu.exec(self.results_table.viewport().mapToGlobal(pos))
        if chosen == view_action:
            self._show_patient_details()
        elif generate_action is not None and chosen == generate_action:
            self.generate_report()
        elif referral_action is not None and chosen == referral_action:
            self.start_referral_flow()
        elif rescreen_action is not None and chosen == rescreen_action:
            self.start_frontdesk_followup()
        elif archive_action is not None and chosen == archive_action:
            self.archive_selected_record()

    def _get_selected_record(self):
        row = self.results_table.currentRow()
        if row < 0:
            selection_model = self.results_table.selectionModel()
            if selection_model is not None:
                selected_rows = selection_model.selectedRows()
                if selected_rows:
                    row = selected_rows[0].row()
        if row < 0:
            return None
        item = self.results_table.item(row, 0)
        return self._display_row_lookup.get(item.data(Qt.UserRole)) if item else None

    def _choose_eye_record(self, record: dict, title: str) -> dict | None:
        eye_details = list(record.get("eye_details") or [])
        if len(eye_details) <= 1:
            return eye_details[0] if eye_details else record

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("This screening visit has results for both eyes. Which eye would you like to use?")
        buttons = {}
        for detail in eye_details:
            label = f"{detail.get('eye_label') or 'Eye'} ({detail.get('display_result') or detail.get('result') or 'Pending'})"
            buttons[box.addButton(label, QMessageBox.ButtonRole.AcceptRole)] = detail
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        chosen = box.clickedButton()
        if chosen == cancel_btn:
            return None
        return buttons.get(chosen)

    def _update_action_buttons(self):
        record = self._get_selected_record()
        self.report_btn.setEnabled(bool(record))
        self.referral_btn.setEnabled(bool(record))
        self.referral_btn.setToolTip("Generate referral letter")
        if getattr(self, "is_frontdesk", False):
            self.rescreen_btn.setEnabled(bool(record))
            self.rescreen_btn.setToolTip("Start a new follow-up screening for the selected patient")
        if self.archive_btn is not None:
            can_archive = bool(record and not record.get("archived_at") and self._can_archive_record(record))
            self.archive_btn.setEnabled(can_archive)
            if record and not self._can_archive_record(record):
                self.archive_btn.setToolTip(
                    f"Only the original screening doctor ({self._record_owner_label(record)}) can archive this record."
                )
            else:
                self.archive_btn.setToolTip("Archive the selected active patient record")

    def start_frontdesk_followup(self) -> None:
        """Front desk shortcut: start follow-up without per-eye prompt (use latest record in visit)."""
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "New Follow-up Screening", "Select a patient record first.")
            return

        label = f"{record.get('name') or 'Unknown Patient'} ({record.get('patient_id') or record.get('patient_code') or 'No ID'})"
        if (
            QMessageBox.question(
                self,
                "New Follow-up Screening",
                f"Start a new follow-up screening for {label}?\n\n"
                "This will open the follow-up form with the patient's information prefilled.\n"
                'After reviewing/editing, click "Save & Queue Patient" to queue them again.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        # Always create a legacy patient_records.db row from the overview record
        # because the record comes from the EMR timeline and its "id" is an EMR ID
        # that does not exist in patient_records.db.
        try:
            from .db import ensure_patient_records_db
        except Exception:  # pragma: no cover
            from db import ensure_patient_records_db
        ok_db, err = ensure_patient_records_db()
        if not ok_db:
            QMessageBox.warning(self, "New Follow-up Screening", f"Unable to open patient records DB: {err}")
            return
        import traceback as _tb
        record_id = 0
        try:
            import sqlite3
            from datetime import datetime
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Use patient_code if available, otherwise fall back to patient_id
            pid_code = str(
                record.get("patient_code")
                or record.get("patient_id")
                or ""
            ).strip()

            dob_iso = str(record.get("birthdate") or record.get("date_of_birth") or "")
            age_str = str(record.get("age") or "")
            if not age_str and dob_iso:
                try:
                    born = datetime.strptime(dob_iso[:10], "%Y-%m-%d").date()
                    today = datetime.now().date()
                    age_val = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
                    age_str = str(max(age_val, 0))
                except Exception:
                    pass

            cur.execute(
                '''
                INSERT INTO patient_records (
                    patient_id, name, birthdate, age, sex, contact, phone, email, address, eyes,
                    diabetes_type, duration, diabetes_diagnosis_date, treatment_regimen, prev_dr_stage,
                    notes, result, confidence,
                    screened_at, screening_type, follow_up, followup_date, followup_label,
                    original_screener_username, original_screener_name, decision_mode,
                    height, weight, bmi
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                ''',
                (
                    pid_code,
                    str(record.get("name") or ""),
                    dob_iso,
                    age_str,
                    str(record.get("sex") or ""),
                    str(record.get("contact") or record.get("contact_number") or record.get("phone") or ""),
                    str(record.get("phone") or record.get("contact") or record.get("contact_number") or ""),
                    str(record.get("email") or ""),
                    str(record.get("address") or ""),
                    str(record.get("eyes") or record.get("eye_summary") or ""),
                    str(record.get("diabetes_type") or ""),
                    str(record.get("duration") or record.get("dm_duration_years") or ""),
                    str(record.get("diabetes_diagnosis_date") or ""),
                    str(record.get("treatment_regimen") or record.get("treatment") or record.get("current_medications") or ""),
                    str(record.get("prev_dr_stage") or ""),
                    str(record.get("notes") or record.get("doctor_findings") or ""),
                    str(record.get("result") or record.get("final_diagnosis_icdr") or record.get("ai_classification") or "Pending"),
                    str(record.get("confidence") or ""),
                    now,
                    "follow_up",
                    "Yes",
                    now,
                    "Follow-up screening",
                    str(record.get("original_screener_username") or ""),
                    str(record.get("original_screener_name") or ""),
                    "emr",
                    str(record.get("height_cm") or record.get("height") or ""),
                    str(record.get("weight_kg") or record.get("weight") or ""),
                    str(record.get("bmi") or ""),
                ),
            )
            conn.commit()
            record_id = int(cur.lastrowid or 0)
        except Exception as exc:
            _tb.print_exc()
            QMessageBox.warning(
                self, "New Follow-up Screening",
                f"Unable to prepare the follow-up screening form.\n\nDetail: {type(exc).__name__}: {exc}",
            )
            return
        finally:
            try:
                if "conn" in locals() and conn is not None:
                    conn.close()
            except Exception:
                pass

        if record_id <= 0:
            QMessageBox.warning(self, "New Follow-up Screening", "Unable to determine the source screening record.")
            return

        main_window = self.window()
        if not hasattr(main_window, "screening_page") or not hasattr(main_window, "pages"):
            QMessageBox.warning(self, "New Follow-up Screening", "Unable to open the screening page.")
            return

        screening_page = main_window.screening_page
        if not hasattr(screening_page, "load_patient_for_followup"):
            QMessageBox.warning(self, "New Follow-up Screening", "Follow-up workflow is not available in this session.")
            return

        if not screening_page.load_patient_for_followup(record_id):
            _tb.print_exc()
            QMessageBox.warning(
                self, "New Follow-up Screening",
                f"Unable to load follow-up form for record #{record_id}.",
            )
            return

        main_window.pages.setCurrentIndex(1)
        try:
            from .auth import UserManager
        except Exception:
            from auth import UserManager
        UserManager.add_activity_log(
            self.username,
            f"FD_FOLLOW_UP_STARTED patient_id={record.get('patient_id')}; previous_record_id={record_id}",
        )

    def _frontdesk_followup_from_overview(self, record: dict) -> None:
        """Called from the frontdesk-mode patient overview's follow-up button.

        Directly prepares the follow-up screening form using the record data
        from the patient overview, bypassing table row selection.
        """
        if not record:
            return

        label = f"{record.get('name') or 'Unknown Patient'} ({record.get('patient_id') or record.get('patient_code') or 'No ID'})"
        if (
            QMessageBox.question(
                self,
                "New Follow-up Screening",
                f"Start a new follow-up screening for {label}?\n\n"
                "This will open the follow-up form with the patient's information prefilled.\n"
                'After reviewing/editing, click "Save & Queue Patient" to queue them again.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        # Close the overview first
        self._hide_patient_overview()

        # Always create a legacy patient_records.db row from the overview record
        # because the record comes from the EMR timeline and its "id" is an EMR ID
        # that does not exist in patient_records.db.
        try:
            from .db import ensure_patient_records_db
        except Exception:
            from db import ensure_patient_records_db
        ok_db, err = ensure_patient_records_db()
        if not ok_db:
            QMessageBox.warning(self, "New Follow-up Screening", f"Unable to open patient records DB: {err}")
            return

        import traceback as _tb
        record_id = 0
        try:
            import sqlite3
            from datetime import datetime
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Use patient_code if available, otherwise fall back to patient_id
            pid_code = str(
                record.get("patient_code")
                or record.get("patient_id")
                or ""
            ).strip()

            dob_iso = str(record.get("birthdate") or record.get("date_of_birth") or "")
            age_str = str(record.get("age") or "")
            if not age_str and dob_iso:
                try:
                    born = datetime.strptime(dob_iso[:10], "%Y-%m-%d").date()
                    today = datetime.now().date()
                    age_val = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
                    age_str = str(max(age_val, 0))
                except Exception:
                    pass

            cur.execute(
                """
                INSERT INTO patient_records (
                    patient_id, name, birthdate, age, sex, contact, phone, email, address, eyes,
                    diabetes_type, duration, diabetes_diagnosis_date, treatment_regimen, prev_dr_stage,
                    notes, result, confidence,
                    screened_at, screening_type, follow_up, followup_date, followup_label,
                    original_screener_username, original_screener_name, decision_mode,
                    height, weight, bmi
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    pid_code,
                    str(record.get("name") or ""),
                    dob_iso,
                    age_str,
                    str(record.get("sex") or ""),
                    str(record.get("contact") or record.get("contact_number") or record.get("phone") or ""),
                    str(record.get("phone") or record.get("contact") or record.get("contact_number") or ""),
                    str(record.get("email") or ""),
                    str(record.get("address") or ""),
                    str(record.get("eyes") or record.get("eye_summary") or ""),
                    str(record.get("diabetes_type") or ""),
                    str(record.get("duration") or record.get("dm_duration_years") or ""),
                    str(record.get("diabetes_diagnosis_date") or ""),
                    str(record.get("treatment_regimen") or record.get("treatment") or record.get("current_medications") or ""),
                    str(record.get("prev_dr_stage") or ""),
                    str(record.get("notes") or record.get("doctor_findings") or ""),
                    str(record.get("result") or record.get("final_diagnosis_icdr") or record.get("ai_classification") or "Pending"),
                    str(record.get("confidence") or ""),
                    now,
                    "follow_up",
                    "Yes",
                    now,
                    "Follow-up screening",
                    str(record.get("original_screener_username") or ""),
                    str(record.get("original_screener_name") or ""),
                    "emr",
                    str(record.get("height_cm") or record.get("height") or ""),
                    str(record.get("weight_kg") or record.get("weight") or ""),
                    str(record.get("bmi") or ""),
                ),
            )
            conn.commit()
            record_id = int(cur.lastrowid or 0)
        except Exception as exc:
            _tb.print_exc()
            QMessageBox.warning(
                self, "New Follow-up Screening",
                f"Unable to prepare the follow-up screening form.\n\nDetail: {type(exc).__name__}: {exc}",
            )
            return
        finally:
            try:
                if "conn" in locals() and conn is not None:
                    conn.close()
            except Exception:
                pass

        if record_id <= 0:
            QMessageBox.warning(self, "New Follow-up Screening", "Unable to determine the source screening record.")
            return

        main_window = self.window()
        if not hasattr(main_window, "screening_page") or not hasattr(main_window, "pages"):
            QMessageBox.warning(self, "New Follow-up Screening", "Unable to open the screening page.")
            return

        screening_page = main_window.screening_page
        if not hasattr(screening_page, "load_patient_for_followup"):
            QMessageBox.warning(self, "New Follow-up Screening", "Follow-up workflow is not available in this session.")
            return

        if not screening_page.load_patient_for_followup(record_id):
            _tb.print_exc()
            QMessageBox.warning(
                self, "New Follow-up Screening",
                f"Unable to load follow-up form for record #{record_id}.",
            )
            return

        main_window.pages.setCurrentIndex(1)
        try:
            from .auth import UserManager
        except Exception:
            from auth import UserManager
        UserManager.add_activity_log(
            self.username,
            f"FD_FOLLOW_UP_STARTED patient_id={record.get('patient_id')}; previous_record_id={record_id}; source=overview",
        )

    def start_referral_flow(self):
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "Referral", "Select a patient record first.")
            return

        self.generate_referral()

    # ── inline patient overview ───────────────────────────────────────────────

    def _show_patient_overview(self, record: dict, timeline_records: list):
        """Replace the reports table with the patient overview panel in-place."""
        from patient_timeline_dialog import PatientTimelineDialog

        # Remove previous overview page if one exists
        if self._main_stack.count() > 1:
            old = self._main_stack.widget(1)
            self._main_stack.removeWidget(old)
            old.deleteLater()

        is_fd = getattr(self, "is_frontdesk", False)
        overview = PatientTimelineDialog(
            record,
            timeline_records,
            on_follow_up=self._start_follow_up_from_timeline if not is_fd else self._frontdesk_followup_from_overview,
            on_view_report=self._generate_report_for_record,
            on_compare=self._compare_latest_two_screenings,
            on_export=self._export_patient_history,
            frontdesk_mode=is_fd,
        )
        overview.back_requested.connect(self._hide_patient_overview)
        self._main_stack.addWidget(overview)
        self._main_stack.setCurrentIndex(1)

    def _hide_patient_overview(self):
        """Return to the reports table."""
        self._main_stack.setCurrentIndex(0)
        if self._main_stack.count() > 1:
            old = self._main_stack.widget(1)
            self._main_stack.removeWidget(old)
            old.deleteLater()

    def _on_table_row_double_clicked(self, index):
        """Handle double-click on table row to show patient details."""
        if index is not None and hasattr(index, "isValid") and index.isValid():
            self.results_table.setCurrentCell(index.row(), 0)
            self.results_table.selectRow(index.row())

        handle_patient_info_double_click(
            self,
            self._get_selected_record,
            self._fetch_patient_timeline_records,
            self.username,
            self._start_follow_up_from_timeline,
            self._generate_report_for_record,
            self._compare_latest_two_screenings,
            self._export_patient_history,
        )

    def _fetch_patient_timeline_records(self, patient_id: str) -> list[dict]:
        patient_id = str(patient_id or "").strip()
        if not patient_id:
            return []

        try:
            p = emr.get_patient_by_code(patient_id) or {}
            pid_pk = int(p.get("patient_id") or 0)
            if not pid_pk:
                return []
            timeline = emr.list_emr_timeline_records(pid_pk)
            for row in timeline:
                row.setdefault("archived_at", None)
                row.setdefault("archived_by", None)
                row.setdefault("archive_reason", None)
            return group_patient_record_rows(timeline)
        except Exception as err:
            QMessageBox.warning(self, "Patient Timeline", f"Failed to load patient history: {err}")
            return []

    def _start_follow_up_from_timeline(self, record: dict):
        if not record:
            QMessageBox.information(self, "Follow-Up Screening", "Choose a timeline record first.")
            return

        action_record = self._choose_eye_record(record, "Choose Eye for Follow-Up")
        if not action_record:
            return

        main_window = self.window()
        if not hasattr(main_window, "screening_page") or not hasattr(main_window, "pages"):
            QMessageBox.warning(self, "Follow-Up Screening", "Unable to open the screening page.")
            return

        # Prefer legacy record id when present (required by screening_form follow-up loader).
        record_id = int(action_record.get("legacy_record_id") or action_record.get("id") or 0)
        if record_id <= 0:
            QMessageBox.warning(self, "Follow-Up Screening", "Unable to determine the source screening record.")
            return

        screening_page = main_window.screening_page
        if not hasattr(screening_page, "load_patient_for_followup"):
            QMessageBox.warning(self, "Follow-Up Screening", "Follow-up workflow is not available in this session.")
            return

        if not screening_page.load_patient_for_followup(record_id):
            QMessageBox.warning(self, "Follow-Up Screening", "Unable to prepare the follow-up screening form.")
            return

        main_window.pages.setCurrentIndex(1)
        UserManager.add_activity_log(
            self.username,
            f"FOLLOW_UP_STARTED patient_id={action_record.get('patient_id')}; previous_record_id={record_id}",
        )

    def _compare_latest_two_screenings(self, timeline_records: list[dict]):
        # Filter for completed screenings only
        ordered = ScreeningComparisonDialog.filter_completed_screenings(
            sorted(list(timeline_records or []), key=_timeline_sort_key)
        )
        if len(ordered) < 2:
            QMessageBox.information(self, "Compare Screenings", "At least two completed screenings are required for comparison.")
            return

        dialog = ScreeningComparisonDialog(ordered, self)
        dialog.exec()

    def _export_patient_history(self, timeline_records: list[dict]):
        ordered = sorted(list(timeline_records or []), key=_timeline_sort_key)
        if not ordered:
            QMessageBox.information(self, "Export Patient History", "No patient history is available to export.")
            return

        patient_name = str(ordered[-1].get("name") or "Patient").strip() or "Patient"
        patient_id = str(ordered[-1].get("patient_id") or "Unknown").strip() or "Unknown"
        default_name = f"EyeShield_History_{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export Patient History", default_name, "CSV Files (*.csv)")
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Name", "Screening Date", "Screened By"])

                for record in ordered:
                    writer.writerow(
                        [
                            record.get("name"),
                            record.get("screened_at"),
                            record.get("original_screener_name") or record.get("original_screener_username") or "—"
                        ]
                    )
        except OSError as err:
            QMessageBox.warning(self, "Export Patient History", f"Unable to export history: {err}")
            return

        self.status_label.setText(f"Exported patient history for {patient_name} to {path}")
        QMessageBox.information(
            self,
            "Export Successful",
            f"The CSV file has been successfully created and saved in:\n{path}"
        )

    def _fetch_full_patient_record(self, record_id: int) -> dict:
        """Fetch complete patient record from database."""
        record = self._record_lookup.get(int(record_id) or 0)
        return dict(record) if record else None

    def open_archived_records_window(self):
        self.refresh_report()
        if self.archived_records_dialog is None:
            self.archived_records_dialog = ArchivedRecordsDialog(self)
        self.archived_records_dialog.reload_rows()
        self.archived_records_dialog.show()
        self.archived_records_dialog.raise_()
        self.archived_records_dialog.activateWindow()

    def archive_selected_record(self):
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "Archive Record", "Select a patient record to archive.")
            return
        if not self._can_archive_record(record):
            owner_text = self._record_owner_label(record)
            QMessageBox.warning(
                self,
                "Archive Restricted",
                f"Only the original screening doctor ({owner_text}) can archive this record.",
            )
            return
        if record["archived_at"]:
            QMessageBox.information(self, "Archive Record", "The selected patient record is already archived.")
            return
        label = f"{record['name'] or 'Unknown Patient'} ({record['patient_id'] or 'No ID'})"
        if QMessageBox.question(
            self,
            "Archive Patient Record",
            (
                "Archiving this patient will archive the entire record, including the complete screening history.\n\n"
                "Are you sure you want to proceed?"
            ),
                                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        patient_code = str(record.get("patient_id") or record.get("patient_code") or "").strip()
        patient = emr.get_patient_by_code(patient_code) if patient_code else None
        if not patient:
            QMessageBox.warning(self, "Archive Record", "Unable to resolve patient in EMR.")
            return
        actor_user_id = emr.get_user_id(self.username or "")
        if not emr.archive_patient(int(patient.get("patient_id") or 0), True, actor_user_id, reason=None):
            QMessageBox.warning(self, "Archive Record", "Unable to archive the selected patient record.")
            return
        self.refresh_report()

    def rescreen_patient(self):
        """Open follow-up screening dialog and navigate to screening page."""
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "Add Follow-Up Screening", "Select a patient record first.")
            return

        if not self._can_rescreen_record(record):
            owner_text = self._record_owner_label(record)
            UserManager.add_activity_log(
                self.username,
                f"FOLLOW_UP_BLOCKED patient_id={record.get('patient_id')}; owner={owner_text}",
            )
            QMessageBox.warning(
                self,
                "Action Restricted",
                f"Only the original screening doctor ({owner_text}) can add a follow-up for this patient.",
            )
            return

        action_record = self._choose_eye_record(record, "Choose Eye for Follow-Up")
        if not action_record:
            return

        actual_record_id = int(action_record.get("id") or 0)
        if actual_record_id <= 0:
            QMessageBox.warning(self, "Add Follow-Up Screening", "Unable to identify patient record ID.")
            return

        label = f"{record['name'] or 'Unknown Patient'} ({record['patient_id'] or 'No ID'})"
        box = QMessageBox(self)
        box.setWindowTitle("Add Follow-Up Screening")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"How would you like to add a follow-up screening for <b>{label}</b>?"
        )
        new_btn = box.addButton("Create New Screening Session", QMessageBox.ButtonRole.AcceptRole)
        replace_btn = box.addButton("Replace Previous Record", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()

        chosen = box.clickedButton()
        if chosen == cancel_btn:
            return

        replace_mode = chosen == replace_btn

        # Get main window and screening page
        main_window = self.window()
        if not hasattr(main_window, 'screening_page') or not hasattr(main_window, 'pages'):
            QMessageBox.warning(self, "Navigation Error", "Unable to navigate to screening page.")
            return

        screening_page = main_window.screening_page
        if not screening_page.load_patient_for_rescreen(actual_record_id, replace_mode=replace_mode):
            QMessageBox.warning(self, "Load Patient", f"Failed to load patient data for rescreening.\n\nRecord ID: {actual_record_id}")
            return

        # Navigate to screening page
        main_window.pages.setCurrentIndex(1)
        UserManager.add_activity_log(
            self.username,
            f"RESCREEN_ALLOWED patient_id={record.get('patient_id')}; record_id={actual_record_id}; replace_mode={replace_mode}",
        )


    def restore_record(self, record):
        if not record or not record.get("archived_at"):
            QMessageBox.information(self, "Restore Record", "The selected patient record is already active.")
            return False
        if not self._can_archive_record(record):
            owner = self._record_owner_label(record)
            QMessageBox.warning(
                self,
                "Restore Restricted",
                f"Only the original screening doctor ({owner}) can restore this archived record.",
            )
            return False
        label = f"{record['name'] or 'Unknown Patient'} ({record['patient_id'] or 'No ID'})"
        if QMessageBox.question(self, "Restore Record", f"Restore {label}?",
                                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return False
        patient_code = str(record.get("patient_id") or record.get("patient_code") or "").strip()
        patient = emr.get_patient_by_code(patient_code) if patient_code else None
        if not patient:
            QMessageBox.warning(self, "Restore Record", "Unable to resolve patient in EMR.")
            return False
        actor_user_id = emr.get_user_id(self.username or "")
        if not emr.archive_patient(int(patient.get("patient_id") or 0), False, actor_user_id, reason=None):
            QMessageBox.warning(self, "Restore Record", "Unable to restore the selected patient record.")
            return False
        return True

    def delete_archived_record(self, record):
        if not record or not record.get("archived_at"):
            return False
        if not self._can_archive_record(record):
            return False
        actor_user_id = emr.get_user_id(self.username or "")
        patient_code = str(record.get("patient_id") or record.get("patient_code") or "").strip()
        patient = emr.get_patient_by_code(patient_code) if patient_code else None
        if not patient:
            return False
        pid_pk = int(patient.get("patient_id") or 0)
        if pid_pk <= 0:
            return False
        queue_ids = emr.list_visit_queue_ids_for_patient(pid_pk, archived=True)
        success = True
        for qid in queue_ids:
            if not emr.delete_visit(int(qid), actor_user_id):
                success = False
        if success and callable(self.records_changed_callback):
            self.records_changed_callback()
        return success

    # Legacy archive helpers removed: archive state is stored on EMR visits (emr_queue_entries).

    @staticmethod
    def _is_high_attention_result(result_text):
        return any(k in str(result_text or "").lower() for k in ("moderate","severe","proliferative","refer","urgent","dr detected"))

    def export_summary(self):
        if not self._summary_cache:
            self.status_label.setText("No report data to export")
            return
        if not self._filtered_rows:
            self.status_label.setText("No visible report data to export")
            return
        confirm = QMessageBox.question(
            self,
            "Export Reports",
            f"Export {len(self._filtered_rows)} visible report row(s) to CSV?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export DR Screening Results", "", "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Name", "Screening Date", "Screened By"])
                for row in self._filtered_rows:
                    w.writerow(
                        [
                            row.get("name"),
                            row.get("screened_at", ""),
                            row.get("original_screener_name") or row.get("original_screener_username") or "—"
                        ]
                    )
            self.status_label.setText(f"Exported {len(self._filtered_rows)} rows to {path}")
            UserManager.add_activity_log(
                self.username,
                f"REPORT_EXPORT_CSV rows={len(self._filtered_rows)}; path={os.path.basename(path)}",
            )
            QMessageBox.information(
                self,
                "Export Successful",
                f"The CSV file has been successfully created and saved in:\n{path}"
            )
        except OSError as err:
            QMessageBox.warning(self, "Export", f"Failed to export summary: {err}")

    def apply_language(self, language: str):
        from translations import get_pack
        pack = get_pack(language)
        self._controls_group.setTitle("")
        self._results_group.setTitle("")
        self._setup_action_buttons_ui()

    # ── Report generation ──────────────────────────────────────────────────────

    def _fetch_full_record(self, record_id: int) -> "dict | None":
        rec = self._record_lookup.get(int(record_id) or 0)
        if not rec:
            return None
        out = dict(rec)
        # Legacy report template expects these aliases.
        out["va_left"] = out.get("visual_acuity_left")
        out["va_right"] = out.get("visual_acuity_right")
        out["bp_systolic"] = out.get("blood_pressure_systolic")
        out["bp_diastolic"] = out.get("blood_pressure_diastolic")
        out["fbs"] = out.get("fasting_blood_sugar")
        out["rbs"] = out.get("random_blood_sugar")
        out["symptom_blurred"] = out.get("symptom_blurred_vision")
        out["diag_date"] = out.get("diabetes_diagnosis_date")
        
        # Ensure age is computed if missing
        if not out.get("age") or out.get("age") == "0":
            dob = out.get("birthdate") or out.get("date_of_birth")
            if dob:
                try:
                    # Handle YYYY-MM-DD or other formats
                    bdate = _parse_datetime_value(dob)
                    if bdate:
                        today = datetime.now()
                        age = today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
                        out["age"] = str(age)
                except Exception:
                    pass

        # Ensure phone and email have fallbacks
        if not out.get("phone"):
            out["phone"] = out.get("contact") or out.get("contact_number")
        if not out.get("email"):
            out["email"] = out.get("email_address")
        
        return out

    def _fetch_report_eye_records(self, patient_id: str, screened_at: str, fallback_record_id: int, screening_group_id: str = "") -> list[dict]:
        def eye_sort_key(record: dict) -> tuple[int, str]:
            eye = str(record.get("eyes") or "").strip().lower()
            if "right" in eye:
                return (0, eye)
            if "left" in eye:
                return (1, eye)
            return (2, eye)

        patient_id = str(patient_id or "").strip()
        screened_at = str(screened_at or "").strip()
        if not patient_id:
            single = self._fetch_full_record(fallback_record_id)
            return [single] if single else []
        records: list[dict] = []
        try:
            if screening_group_id:
                records = [
                    dict(r)
                    for r in self._all_result_rows
                    if str(r.get("screening_group_id") or "").strip() == str(screening_group_id).strip()
                ]
            elif screened_at:
                records = [
                    dict(r)
                    for r in self._all_result_rows
                    if str(r.get("patient_id") or "").strip() == patient_id and str(r.get("screened_at") or "").strip() == screened_at
                ]
            else:
                records = [dict(r) for r in self._all_result_rows if str(r.get("patient_id") or "").strip() == patient_id]
                records.sort(key=lambda r: _timeline_sort_key(r), reverse=True)
                records = records[:2]
        except Exception:
            records = []
        if not records:
            single = self._fetch_full_record(fallback_record_id)
            records = [single] if single else []

        unique_records = []
        seen_ids = set()
        for record in records:
            record_id = record.get("id")
            if record_id in seen_ids:
                continue
            seen_ids.add(record_id)
            unique_records.append(record)
        return sorted(unique_records, key=eye_sort_key)

    def _prompt_referral_destination(self) -> "dict | None":
        hospitals = UserManager.list_referral_hospitals(active_only=True)

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Medical Partner")
        dialog.setFixedSize(540, 180)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hospital_label = QLabel("Please select a trusted medical partner")
        hospital_label.setStyleSheet("font-size:12px;font-weight:700;color:#1e293b;")
        
        hospital_combo = QComboBox()
        hospital_combo.setMinimumHeight(36)
        for item in hospitals:
            doc = str(item.get("contact_person") or "").strip()
            hosp = str(item.get("hospital_name") or "").strip()
            label = f"{doc} ({hosp})" if doc and hosp else (doc or hosp or "Unnamed")
            
            if item.get("is_default"):
                label = f"{label}  [Default]"
            hospital_combo.addItem(label, item)
            
        hospital_combo.addItem("Manual Entry (Other)", None)
        layout.addWidget(hospital_label)
        layout.addWidget(hospital_combo)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        continue_btn = QPushButton("Continue")
        continue_btn.setObjectName("primaryAction")
        action_row.addWidget(cancel_btn)
        action_row.addWidget(continue_btn)
        layout.addLayout(action_row)

        cancel_btn.clicked.connect(dialog.reject)
        continue_btn.clicked.connect(dialog.accept)

        def _prompt_manual_destination() -> dict | None:
            manual_dialog = QDialog(dialog)
            manual_dialog.setWindowTitle("Manual Medical Partner Entry")
            manual_dialog.setFixedSize(520, 260)

            manual_layout = QVBoxLayout(manual_dialog)
            manual_layout.setContentsMargins(16, 16, 16, 16)
            manual_layout.setSpacing(10)

            doc_input = QLineEdit()
            doc_input.setPlaceholderText("Doctor Name")
            hosp_input = QLineEdit()
            hosp_input.setPlaceholderText("Hospital or Clinic")
            addr_input = QLineEdit()
            addr_input.setPlaceholderText("Address")
            
            manual_layout.addWidget(QLabel("Doctor Name"))
            manual_layout.addWidget(doc_input)
            manual_layout.addWidget(QLabel("Hospital / Clinic"))
            manual_layout.addWidget(hosp_input)
            manual_layout.addWidget(QLabel("Address"))
            manual_layout.addWidget(addr_input)

            manual_actions = QHBoxLayout()
            manual_actions.addStretch(1)
            manual_cancel_btn = QPushButton("Cancel")
            manual_save_btn = QPushButton("Use Destination")
            manual_save_btn.setObjectName("primaryAction")
            manual_actions.addWidget(manual_cancel_btn)
            manual_actions.addWidget(manual_save_btn)
            manual_layout.addLayout(manual_actions)

            manual_cancel_btn.clicked.connect(manual_dialog.reject)
            manual_save_btn.clicked.connect(manual_dialog.accept)

            while True:
                if manual_dialog.exec() != QDialog.DialogCode.Accepted:
                    return None
                doc_name = doc_input.text().strip()
                hosp_name = hosp_input.text().strip()
                addr = addr_input.text().strip()
                
                if not doc_name and not hosp_name:
                    QMessageBox.warning(manual_dialog, "Validation Error", "Please provide at least a Doctor or Hospital name.")
                    continue
                
                display = f"{doc_name} ({hosp_name})" if doc_name and hosp_name else (doc_name or hosp_name)
                return {
                    "contact_person": doc_name,
                    "hospital_name": hosp_name,
                    "address": addr,
                    "display": display,
                }

        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None

            selected = hospital_combo.currentData()
            if selected is None:
                manual_destination = _prompt_manual_destination()
                if manual_destination is not None:
                    return manual_destination
                continue

            hospital_name = str(selected.get("hospital_name") or "").strip()
            department = str(selected.get("department") or "").strip()
            contact = str(selected.get("contact_person") or selected.get("phone") or "").strip()
            address = str(selected.get("address") or "").strip()
            display = hospital_name
            if department:
                display = f"{display} ({department})"
            return {
                "hospital_name": hospital_name,
                "department": department,
                "contact_person": contact,
                "address": address,
                "display": display,
            }

    def generate_report(self):
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "Generate Report", "Select a patient record to generate a report for.")
            return

        self._generate_report_for_record(record)

    def _generate_report_for_record(self, record: dict):
        if not record:
            QMessageBox.information(self, "Generate Report", "No patient record is available for report generation.")
            return

        full = self._fetch_full_record(record["id"]) or record
        eye_records = self._fetch_report_eye_records(
            full.get("patient_id"),
            full.get("screened_at"),
            int(full.get("id") or record["id"]),
            str(full.get("screening_group_id") or record.get("screening_group_id") or ""),
        )
        if not eye_records:
            eye_records = [full]

        success = generate_unified_patient_report(self, full, eye_records, self.username)
        if success:
            UserManager.add_activity_log(
                self.username,
                f"REPORT_GENERATED patient_id={full.get('patient_id')}; record_id={full.get('id')}",
            )
            QMessageBox.information(self, "Report Saved", "Patient report has been generated successfully.")


    def generate_referral(self):
        record = self._get_selected_record()
        if not record:
            QMessageBox.information(self, "Generate Referral", "Select a patient record to generate a referral letter.")
            return

        full = self._fetch_full_record(record["id"]) or record
        eye_records = self._fetch_report_eye_records(
            full.get("patient_id"),
            full.get("screened_at"),
            int(full.get("id") or record["id"]),
        )
        if not eye_records:
            eye_records = [full]

        severity_rank = {
            "No DR": 0,
            "Mild DR": 1,
            "Moderate DR": 2,
            "Severe DR": 3,
            "Proliferative DR": 4,
        }

        def pick_worst_grade(values: list[str]) -> str:
            best = ""
            best_rank = -1
            for raw in values:
                value = str(raw or "").strip()
                rank = severity_rank.get(value, -1)
                if rank > best_rank:
                    best = value
                    best_rank = rank
            return best

        bilateral_grades = []
        for eye_record in eye_records:
            grade = (
                str(eye_record.get("final_diagnosis_icdr") or "").strip()
                or str(eye_record.get("doctor_classification") or "").strip()
                or str(eye_record.get("ai_classification") or "").strip()
                or str(eye_record.get("result") or "").strip()
            )
            if grade:
                bilateral_grades.append(grade)

        result_raw = pick_worst_grade(bilateral_grades) or str(full.get("result") or "").strip()

        if result_raw in ("No DR", "Mild DR"):
            confirm = QMessageBox.question(
                self,
                "Generate Referral",
                "This result is usually not urgent for specialist referral. Generate referral letter anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        selected_destination = self._prompt_referral_destination()
        if selected_destination is None:
            return

        patient_name_raw = str(full.get("name") or "Patient").strip()
        default_name = f"EyeShield_Referral_{patient_name_raw}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save Referral Letter", default_name, "PDF Files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path = f"{path}.pdf"

        try:
            from PySide6.QtGui import QPdfWriter, QPageSize, QPageLayout, QTextDocument
            from PySide6.QtCore import QMarginsF
        except ImportError:
            QMessageBox.warning(self, "Generate Referral", "PDF generation requires PySide6 PDF support.")
            return

        def esc(v) -> str:
            s = str(v or "").strip()
            return escape(s) if s and s not in ("0", "None", "Select", "-") else "&#8212;"

        def _to_long_date(value: str) -> str:
            raw = str(value or "").strip()
            if not raw:
                return ""
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%d/%m/%Y",
                "%B %d, %Y",
            ):
                try:
                    return datetime.strptime(raw, fmt).strftime("%B %d, %Y")
                except ValueError:
                    continue
            return raw

        referral_map = {
            "No DR": ("Routine", "Annual follow-up and routine retinal screening."),
            "Mild DR": ("Routine", "Repeat retinal assessment in 6-12 months is advised."),
            "Moderate DR": ("Priority", "Refer to ophthalmology within 3 months for specialist evaluation."),
            "Severe DR": ("Urgent", "Urgent ophthalmology review is advised due to high progression risk."),
            "Proliferative DR": ("Immediate", "Immediate specialist referral is required for potential sight-threatening disease."),
        }
        urgency, rationale = referral_map.get(result_raw, ("Clinical Review", "Please evaluate for diabetic retinopathy management."))

        report_date = datetime.now().strftime("%B %d, %Y")
        profile = UserManager.get_user_profile(self.username) or {}
        finalized_by_raw = str(profile.get("full_name") or self.display_name or os.environ.get("EYESHIELD_CURRENT_NAME", "") or self.username).strip()
        created_by_raw = str(full.get("original_screener_name") or full.get("original_screener_username") or "").strip()
        created_by_label = self._format_doctor_label(created_by_raw or finalized_by_raw)
        finalized_by_label = self._format_doctor_label(finalized_by_raw)

        doctor_full = str(selected_destination.get("contact_person") or "").strip()
        hosp_name = str(selected_destination.get("hospital_name") or "").strip()
        hosp_addr = str(selected_destination.get("address") or "").strip()
        
        parts = doctor_full.split()
        surname = parts[-1] if parts else ""
        
        destination_name = esc(hosp_name)
        doctor_label = esc(doctor_full)
        destination_addr = esc(hosp_addr)

        # Get doctor's contact info (email preferred, fallback to phone/contact)
        doctor_contact = str(profile.get("contact") or "").strip()

        screen_date_text = esc(_to_long_date(full.get("screened_at") or report_date))
        eye_findings = []
        for eye_record in eye_records:
            eye_label = str(eye_record.get("eyes") or "Eye").strip() or "Eye"
            eye_grade = (
                str(eye_record.get("final_diagnosis_icdr") or "").strip()
                or str(eye_record.get("doctor_classification") or "").strip()
                or str(eye_record.get("ai_classification") or "").strip()
                or str(eye_record.get("result") or "").strip()
                or "N/A"
            )
            eye_findings.append(f"{eye_label}: {eye_grade}")
        eye_findings_text = esc("; ".join(eye_findings)) if eye_findings else "&#8212;"

        ai_result_raw = (
            str(full.get("ai_classification") or "").strip()
            or str(full.get("result") or "").strip()
            or result_raw
        )
        patient_name = esc(full.get("name"))
        patient_dob = esc(full.get("birthdate"))
        patient_age = esc(full.get("age"))
        patient_sex = esc(full.get("sex"))
        patient_hba1c = esc(full.get("hba1c"))
        patient_diabetes_type = esc(full.get("diabetes_type"))
        patient_height = esc(full.get("height"))
        patient_weight = esc(full.get("weight"))
        patient_bmi = esc(full.get("bmi"))
        patient_visual_acuity_left = esc(full.get("visual_acuity_left"))
        patient_visual_acuity_right = esc(full.get("visual_acuity_right"))
        patient_notes_raw = str(full.get("notes") or "").strip()
        if len(patient_notes_raw) > 220:
            patient_notes_raw = f"{patient_notes_raw[:217].rstrip()}..."
        patient_notes = esc(patient_notes_raw)

        def _resolve_image_uri(path_value: str) -> str:
            raw = str(path_value or "").strip()
            if not raw:
                return ""
            candidate = raw if os.path.isabs(raw) else os.path.join(os.path.dirname(os.path.abspath(__file__)), raw)
            if not os.path.exists(candidate):
                return ""
            try:
                return Path(candidate).resolve().as_uri()
            except OSError:
                return ""

        def _scaled_referral_image(uri: str, file_path: str, missing_text: str) -> str:
            if not uri:
                return f'<div style="padding:26px 14px;color:#9ca3af;font-style:italic;">{missing_text}</div>'

            max_w, max_h = 380, 280
            width, height = max_w, max_h
            if file_path and os.path.exists(file_path):
                pixmap = QPixmap(file_path)
                if not pixmap.isNull() and pixmap.width() > 0 and pixmap.height() > 0:
                    ratio = min(max_w / pixmap.width(), max_h / pixmap.height())
                    ratio = min(ratio, 1.0)
                    width = max(1, int(pixmap.width() * ratio))
                    height = max(1, int(pixmap.height() * ratio))

            return (
                f'<img src="{uri}" width="{width}" height="{height}" '
                'style="display:block;margin:0 auto;border-radius:2px;" />'
            )

        def _normalize_eye_label(eye_label_value: str) -> str:
            eye_name = str(eye_label_value or "").strip().lower()
            if eye_name in ("left", "left eye", "os"):
                return "Left Eye"
            if eye_name in ("right", "right eye", "od"):
                return "Right Eye"
            return str(eye_label_value or "Eye").strip() or "Eye"

        def _referral_eye_block(eye_label_value: str, source_uri: str, source_path: str) -> str:
            source_html = _scaled_referral_image(source_uri, source_path, "Fundus image not available")
            return f"""
    <div class=\"image-box keep-together\">
        <div style=\"font-size:9.2pt;font-weight:700;color:#1f2937;margin-bottom:10px;\">{esc(_normalize_eye_label(eye_label_value))}</div>
        <div style=\"font-size:8pt;font-weight:700;color:#4b5563;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px;\">Fundus Image</div>
        <div style=\"text-align:center;background:#ffffff;padding:8px;border:1px solid #e5e7eb;min-height:230px;\">{source_html}</div>
    </div>
"""

        referral_eye_blocks = []
        seen_eyes = set()
        for eye_record in eye_records:
            eye_label = str(eye_record.get("eyes") or "").strip() or "Eye"
            source_path = str(eye_record.get("source_image_path") or "").strip()
            source_uri = _resolve_image_uri(source_path)
            dedupe_key = (eye_label.lower(), source_path)
            if dedupe_key in seen_eyes:
                continue
            seen_eyes.add(dedupe_key)
            referral_eye_blocks.append(
                _referral_eye_block(eye_label, source_uri, source_path)
            )

        if not referral_eye_blocks:
            fallback_source_path = str(full.get("source_image_path") or "").strip()
            referral_eye_blocks.append(
                _referral_eye_block(
                    str(full.get("eyes") or "Eye").strip() or "Eye",
                    _resolve_image_uri(fallback_source_path),
                    fallback_source_path,
                )
            )

        is_bilateral_referral = len(referral_eye_blocks) > 1
        if is_bilateral_referral:
            referral_images_html = (
                '<div class="subject">Bilateral Fundus Images Captured</div>'
                '<div class="paragraph">The following retinal fundus images from both screened eyes are attached for specialist reference.</div>'
                + "".join(referral_eye_blocks)
            )
        else:
            referral_images_html = (
                '<div class="subject">Fundus Image Captured</div>'
                '<div class="paragraph">The following retinal fundus image was captured during this screening encounter and is attached for specialist reference.</div>'
                + "".join(referral_eye_blocks)
            )

        # Build professional 2-page HTML
        style = """
        <style>
            @page { margin: 10mm; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #1e293b; line-height: 1.4; font-size: 11.5pt; margin: 0; padding: 0; }
            .page { width: 100%; }
            .header { text-align: center; margin-bottom: 20px; }
            .header h1 { font-size: 20pt; color: #0f172a; text-transform: uppercase; border-bottom: 2px solid #0f172a; padding-bottom: 5px; margin: 0; }
            .meta-row { margin-bottom: 3px; }
            .subject { font-weight: bold; margin-top: 15px; margin-bottom: 15px; text-decoration: underline; }
            .section-title { font-weight: bold; margin-top: 15px; margin-bottom: 5px; color: #334155; text-transform: uppercase; font-size: 11pt; }
            .findings-list { margin-left: 20px; margin-top: 5px; margin-bottom: 10px; }
            .findings-list li { margin-bottom: 2px; }
            .footer { margin-top: 30px; }
            .page-break { page-break-before: always; }
            .image-container { text-align: center; margin-top: 15px; margin-bottom: 30px; }
            .image-container img { border: 1px solid #e2e8f0; border-radius: 4px; max-width: 600px; max-height: 400px; object-fit: contain; }
            .eye-label { font-size: 16pt; font-weight: bold; color: #1e40af; margin-top: 5px; }
            p { margin: 0 0 10px 0; }
        </style>
        """

        # Page 1: Letter
        html = f"<html><head>{style}</head><body>"
        html += "<div class='page'>"
        html += "<div class='header'><h1>Medical Referral Letter</h1></div>"
        html += f"<div class='meta-row'><strong>Date:</strong> {report_date}</div>"
        html += f"<div class='meta-row'><strong>To:</strong> Dr. {doctor_label}</div>"
        html += f"<div class='meta-row'><strong>Hospital:</strong> {destination_name}</div>"
        html += f"<div class='meta-row'><strong>Address:</strong> {destination_addr}</div>"
        html += f"<div class='subject'>Subject: Clinical Referral for Patient: {patient_name}</div>"
        
        html += f"<p>Dear Dr. {esc(surname)},</p>"
        html += "<p>I am writing to formally refer the above-mentioned patient to your specialized care for further evaluation and management.</p>"
        
        html += "<div class='section-title'>Clinical Findings:</div>"
        html += f"<p>Based on the Diabetic Retinopathy (DR) screening conducted on {screen_date_text}, the following status has been identified:</p>"
        html += "<ul class='findings-list'>"
        
        # Gather images/diagnosis for both eyes if available
        referral_eyes_data = []
        for eye_record in eye_records:
            label = str(eye_record.get("eyes") or "Eye").upper()
            diag = (
                str(eye_record.get("final_diagnosis_icdr") or "").strip()
                or str(eye_record.get("doctor_classification") or "").strip()
                or str(eye_record.get("ai_classification") or "").strip()
                or str(eye_record.get("result") or "").strip()
                or "N/A"
            )
            img_path = str(eye_record.get("source_image_path") or "").strip()
            if img_path:
                referral_eyes_data.append({
                    "label": label,
                    "diagnosis": esc(diag),
                    "path": img_path
                })

        for eye in referral_eyes_data:
            html += f"<li><strong>{eye['label']}:</strong> {eye['diagnosis']}</li>"
        html += "</ul>"
        
        html += "<p>I would appreciate your expert consultation and any necessary intervention or specialized care that the patient may require. "
        html += "Screening reports and fundus images have been provided to the patient for your reference.</p>"
        
        html += "<p>Thank you for your collaboration in providing comprehensive care for this patient.</p>"
        
        html += "<div class='footer'>"
        html += "<p>Sincerely,</p><br>"
        html += f"<strong>{finalized_by_label}</strong><br>"
        html += "EyeShield DR Screening System"
        html += "</div>"
        html += "</div>" # End Page 1

        # Page 2: Images
        html += "<div class='page-break'>"
        html += "<div class='header'><h1>Screening Images</h1></div>"
        for eye in referral_eyes_data:
            img_url = Path(eye['path']).resolve().as_uri()
            html += "<div class='image-container'>"
            html += f"<div class='eye-label'>{eye['label']}</div>"
            html += f"<img src='{img_url}' width='600'>"
            html += "</div>"
            
        html += "</body></html>"

        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setHtml(html)

        writer = QPdfWriter(path)
        writer.setResolution(150)
        try:
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        except Exception:
            pass
        try:
            writer.setPageMargins(QMarginsF(14, 10, 14, 16), QPageLayout.Unit.Millimeter)
        except Exception:
            pass

        doc.print_(writer)
        del writer
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            QMessageBox.warning(
                self,
                "Generate Referral",
                "Referral PDF was not created. Please choose a writable folder and try again.",
            )
            return
        self.status_label.setText(f"Referral saved: {os.path.basename(path)}")
        UserManager.add_activity_log(
            self.username,
            (
                f"REFERRAL_GENERATED patient_id={full.get('patient_id')}; "
                f"record_id={full.get('id')}; "
                f"created_by={created_by_raw or finalized_by_raw}; "
                f"finalized_by={finalized_by_raw}; "
                f"file={os.path.basename(path)}"
            ),
        )
        QMessageBox.information(self, "Referral Saved", f"Referral letter saved to:\n{path}")