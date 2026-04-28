"""
Screening form page for EyeShield EMR application.
Extracted from screening.py for better modularity.
"""

from datetime import datetime
from html import escape
import contextlib
import json
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import traceback

from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QLineEdit, QVBoxLayout, QHBoxLayout,
    QFileDialog, QFormLayout, QGroupBox, QComboBox, QDateEdit, QMessageBox,
    QDoubleSpinBox, QSpinBox, QCheckBox, QTextEdit, QCalendarWidget, QStackedWidget,
    QGridLayout, QFrame, QSizePolicy, QScrollArea, QSplitter, QAbstractSpinBox,
    QApplication,
)
from PySide6.QtGui import (
    QPixmap,
    QFont,
    QRegularExpressionValidator,
    QIcon,
    QPainter,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
)
from PySide6.QtCore import Qt, QDate, QRegularExpression, QSize, Signal, QTimer

try:
    from .screening_styles import (
        SCREENING_PAGE_STYLE,
        LINEEDIT_STYLE,
        TEXTEDIT_STYLE,
        SPINBOX_STYLE,
        DOUBLESPINBOX_STYLE,
        READONLY_SPINBOX_STYLE,
        CHECKBOX_STYLE,
        CALENDAR_STYLE,
    )
    from .screening_worker import _InferenceWorker
    from .screening_widgets import ClickableImageLabel, ModernCalendarDateEdit
    from .screening_results import ResultsWindow
    from .logic_improvements import (
        ScreeningFlowGuard,
        DuplicateDetector,
        DuplicateDialog,
    )
    from .app_paths import PATIENT_RECORDS_DB_PATH
    from .auth import UserManager
    from . import emr_service as emr
    from .safety_runtime import get_autosave_draft_path, safe_remove_file, write_activity
except ImportError:
    from screening_styles import (
        SCREENING_PAGE_STYLE,
        LINEEDIT_STYLE,
        TEXTEDIT_STYLE,
        SPINBOX_STYLE,
        DOUBLESPINBOX_STYLE,
        READONLY_SPINBOX_STYLE,
        CHECKBOX_STYLE,
        CALENDAR_STYLE,
    )
    from screening_worker import _InferenceWorker
    from screening_widgets import ClickableImageLabel, ModernCalendarDateEdit
    from screening_results import ResultsWindow
    from logic_improvements import (
        ScreeningFlowGuard,
        DuplicateDetector,
        DuplicateDialog,
    )
    from app_paths import PATIENT_RECORDS_DB_PATH
    from auth import UserManager
    import emr_service as emr
    from safety_runtime import get_autosave_draft_path, safe_remove_file, write_activity

# Central records DB helpers
try:
    from db import get_records_conn, ensure_patient_records_db_schema
except Exception:
    from .db import get_records_conn, ensure_patient_records_db_schema

# Canonical patient-records database used by Screening History / reports.
DB_FILE = str(PATIENT_RECORDS_DB_PATH)


class SymptomTag(QPushButton):
    """Toggleable pill tag used by the redesigned symptoms section."""

    _OFF = (
        "QPushButton {"
        "  background:#ffffff; color:#1f4f77;"
        "  border:1.5px solid transparent; border-radius:999px;"
        "  padding:5px 14px; font-size:12px; font-weight:500;"
        "}"
        "QPushButton:hover { border-color:#3f7ca7; }"
    )
    _ON = (
        "QPushButton {"
        "  background:#3f7ca7; color:#ffffff;"
        "  border:1.5px solid #3f7ca7; border-radius:999px;"
        "  padding:5px 14px; font-size:12px; font-weight:500;"
        "}"
    )

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setStyleSheet(self._OFF)
        self.toggled.connect(lambda on: self.setStyleSheet(self._ON if on else self._OFF))




class DurationSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 1200)
        self.setSpecialValueText(" ")
        
    def textFromValue(self, value: int) -> str:
        if value == 0:
            return ""
        y = value // 12
        m = value % 12
        return f"{y} years and {m} months"
        
    def valueFromText(self, text: str) -> int:
        try:
            # Basic parsing support for manual typing if needed
            import re
            parts = re.findall(r'(\d+)', text)
            if len(parts) >= 2:
                return int(parts[0]) * 12 + int(parts[1])
            elif len(parts) == 1:
                return int(parts[0]) * 12
        except Exception:
            pass
        return super().valueFromText(text)


class DropZoneLabel(QLabel):
    """Dashed image drop zone that emits file path on valid image drop."""

    file_dropped = Signal(str)

    _IDLE = (
        "QLabel { border:2px dashed #c6d2df; border-radius:10px;"
        " background:#ffffff; color:#3f7ca7;"
        " font-size:13px; font-weight:500; }"
    )
    _HOVER = (
        "QLabel { border:2px dashed #3f7ca7; border-radius:10px;"
        " background:#f8fbff; color:#3f7ca7;"
        " font-size:13px; font-weight:500; }"
    )
    _LOADED = (
        "QLabel { border:2px solid #2563eb; border-radius:12px;"
        " background:#000000; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Default is tall for the standalone Screening page; embedding flows
        # can override via `set_compact(True)` below.
        self.setMinimumHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pixmap_full = QPixmap()
        self._reset_placeholder()

    def set_compact(self, enabled: bool = True) -> None:
        """Reduce height/typography for embedded layouts."""
        if bool(enabled):
            self.setMinimumHeight(180)
            self.setStyleSheet(self._IDLE.replace("font-size:13px", "font-size:12px"))
        else:
            self.setMinimumHeight(400)
            self.setStyleSheet(self._IDLE)

    def set_image(self, path: str):
        px = QPixmap(path)
        if px.isNull():
            return
        self._pixmap_full = px
        self.setStyleSheet(self._LOADED)
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()

    def clear_image(self):
        self._pixmap_full = QPixmap()
        self._reset_placeholder()

    def has_image(self) -> bool:
        return not self._pixmap_full.isNull()

    def _reset_placeholder(self):
        self.setStyleSheet(self._IDLE)
        self.setPixmap(QPixmap())
        
        # Pure text-based placeholder per redesign
        self.setText(
            "<div style='text-align:center;'>"
            "<div style='font-size:14px;color:#1e293b;font-weight:600;margin-top:20px;'>Drop fundus image here or click to browse</div>"
            "<div style='font-size:11px;color:#94a3b8;margin-top:8px;'>Supports JPG, PNG, JPEG</div>"
            "</div>"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _refresh(self):
        if self._pixmap_full.isNull():
            return
        scaled = self._pixmap_full.scaled(
            max(1, self.width() - 4),
            max(1, self.height() - 4),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._pixmap_full.isNull():
            self._refresh()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._HOVER)

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._LOADED if self.has_image() else self._IDLE)

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith((".jpg", ".jpeg", ".png")):
                self.file_dropped.emit(path)
        self.setStyleSheet(self._LOADED if self.has_image() else self._IDLE)





_REDESIGN_STYLESHEET = """
QWidget { background:#ffffff; color:#1f2937; font-family:"Segoe UI","Inter","Calibri",sans-serif; font-size:13px; }
QFrame#card { background:#ffffff; border:1px solid #dde3ea; border-radius:0px; }
QLineEdit, QDateEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
    background:#ffffff; border:1.5px solid #d3dae3; border-radius:0px; padding:6px 10px;
    color:#1f2937; min-height:28px; selection-background-color:#3f7ca7;
}
QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
    border:1.5px solid #3f7ca7; background:#ffffff;
}
QLineEdit:read-only { background:#f6f8fb; color:#475569; }
QDateEdit {
    padding:6px 34px 6px 10px;
}
QDateEdit::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width:24px;
    border-left:1px solid #d3dae3;
    background:#f6f8fb;
    border-top-right-radius:6px;
    border-bottom-right-radius:6px;
}
QDateEdit::down-arrow {
    width:10px;
    height:10px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width:24px;
    border-left:1px solid #d3dae3;
    background:#f6f8fb;
    border-top-right-radius:6px;
    border-bottom-right-radius:6px;
}
QComboBox::down-arrow { width:10px; height:10px; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width:18px; border:none; background:transparent; }
QScrollArea { border:none; background:transparent; }
QScrollBar:vertical { background:#f2f5f8; width:6px; border-radius:3px; }
QScrollBar::handle:vertical { background:#c2ccd8; border-radius:3px; min-height:20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QSplitter::handle { background:#e7ecf1; width:1px; }
QPushButton#btnPrimary, QPushButton#btnDanger, QPushButton#btnAnalyze {
    background:#ffffff;
    color:#1a1a1a;
    border:1px solid #bfdbfe;
    border-radius:0px;
    padding:8px 14px;
    font-weight:600;
    font-size:13px;
}
QPushButton#btnPrimary:hover, QPushButton#btnDanger:hover, QPushButton#btnAnalyze:hover {
    background:#eff6ff;
    border-color:#93c5fd;
}
QPushButton#btnPrimary:disabled, QPushButton#btnDanger:disabled, QPushButton#btnAnalyze:disabled {
    background:#f8fafc;
    border:1px solid #dbeafe;
    color:#9ca3af;
}
QCheckBox { color:#475569; spacing:8px; font-size:12px; }
QCheckBox::indicator { width:16px; height:16px; border:1.5px solid #94a3b8; border-radius:3px; background:#ffffff; }
QCheckBox::indicator:checked { background:#3f7ca7; border-color:#3f7ca7; }
"""


class ScreeningPage(QWidget):
    """Patient screening page for DR detection with two-step workflow"""

    def __init__(self):
        super().__init__()
        self._embedded_compact = False
        self._content_root = None
        self._ensure_patient_records_schema()
        self.current_image = None
        self.patient_counter = 0
        self.min_dob_date = QDate(1900, 1, 1)
        self.max_dob_date = QDate.currentDate()
        self.default_dob_date = QDate(2000, 1, 1)  # Default calendar view year
        self.min_diagnosis_date = QDate(1900, 1, 1)
        self.max_diagnosis_date = QDate.currentDate()
        self.default_diagnosis_date = QDate.currentDate() # Default to today for diagnosis
        self._dob_user_selected = False
        self._dob_programmatic_update = False
        self.last_result_class = "Pending"
        self.last_result_conf = "Pending"
        self.last_ai_classification = "Pending"
        self.last_doctor_classification = "Pending"
        self.last_decision_mode = "pending"
        self.last_override_justification = ""
        self.last_doctor_findings = ""
        self._custom_storage_root = ""
        self._last_saved_signature = ""
        self._last_saved_at = ""
        self._last_saved_source_path = ""
        self._current_eye_saved = False
        self._first_eye_result = None
        # Bilateral workflow state:
        # - `_first_eye_result` persists for the session once the first eye is saved
        # - `_second_eye_result` is populated after the other eye is screened/saved
        # - `_is_second_eye_flow` gates "finish vs prompt" decisions in Results UI
        self._second_eye_result = None
        self._is_second_eye_flow = False
        # Patient identity must stay stable for the entire screening session.
        # Store it separately from UI widgets so it can't drift.
        self._session_patient_code = ""
        self._last_eye_choice = ""
        self._suspend_eye_guard = False
        self._navigation_locked = False
        self._rescreen_replace_record_id = None
        self._current_screening_type = ""
        self._current_previous_screening_id = None
        self._current_follow_up_flag = ""
        self._current_followup_date = ""
        self._current_followup_label = ""
        self._current_screening_group_id = ""
        self._emr_patient_pk = None
        self._emr_screening_id = None
        self._emr_queue_entry_id = None
        self._followup_restricted_mode = False
        self._flow_guard = ScreeningFlowGuard(self)
        self._duplicate_detector = DuplicateDetector()
        self._doctor_results_dialog = None
        self._draft_path = get_autosave_draft_path()
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(120_000)
        self._autosave_timer.timeout.connect(self._autosave_draft)
        self.stacked_widget = QStackedWidget()
        self.init_ui()
        self._autosave_timer.start()

    def set_embedded_compact(self, enabled: bool = True, *, max_width: int = 560) -> None:
        """
        Compact layout for embedding (e.g., inside EMR doctor diagnosis page).
        This reduces screen-based minimum widths so the upload panel fits its parent.
        """
        self._embedded_compact = bool(enabled)
        root = getattr(self, "_content_root", None)
        if root is None:
            return
        if self._embedded_compact:
            root.setMinimumWidth(0)
            # Remove maximum width limit to allow 50/50 split to function correctly
            root.setMaximumWidth(16777215)
            root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            # Collapse any centering gutters (addStretch spacers) that were added
            # during non-embedded build so content_root fills its parent fully.
            center_row = getattr(self, "_center_row_ref", None)
            if center_row is not None:
                center_row.setStretchFactor(root, 1000)
            # Remove layout margins to align with external components
            if self.layout() is not None:
                self.layout().setContentsMargins(0, 0, 0, 0)
        else:
            root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            if self.layout() is not None:
                self.layout().setContentsMargins(16, 16, 16, 16)

    def _ensure_patient_records_schema(self) -> None:
        """Keep the local patient_records DB compatible with newer save payloads."""
        conn = get_records_conn()
        try:
            ensure_patient_records_db_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def is_navigation_locked(self) -> bool:
        return bool(self._navigation_locked)

    def _is_inference_active(self) -> bool:
        worker = getattr(self, "_worker", None)
        return bool(worker and hasattr(worker, "isRunning") and worker.isRunning())

    def _guard_busy_action(self, action_name: str = "this action") -> bool:
        if self.is_navigation_locked() or self._is_inference_active():
            QMessageBox.information(
                self,
                "Screening In Progress",
                f"Please wait for image analysis to finish before {action_name}.",
            )
            return True
        return False

    def _set_navigation_locked(self, locked: bool):
        self._navigation_locked = bool(locked)
        main_window = self.window()
        if main_window is not self and hasattr(main_window, "_refresh_navigation_lock"):
            main_window._refresh_navigation_lock()

    def _modal_parent_widget(self):
        return self

    def _reparent_results_into_stack(self, set_index_0: bool = True):
        self._doctor_results_dialog = None
        if self.stacked_widget.indexOf(self.results_page) < 0:
            self.stacked_widget.insertWidget(1, self.results_page)
        if set_index_0:
            self.stacked_widget.setCurrentIndex(0)

    def _show_doctor_results_full_window(self):
        """Doctor diagnosis now uses the same in-tab screening results page."""
        self._reparent_results_into_stack(set_index_0=False)
        self.stacked_widget.setCurrentIndex(1)

    def _generate_screening_group_id(self, patient_id: str) -> str:
        patient_token = re.sub(r"[^A-Za-z0-9]+", "-", str(patient_id or "").strip()).strip("-") or "patient"
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"{patient_token}-{stamp}"

    def _ensure_screening_group_id(self, patient_id: str) -> str:
        current_group_id = str(getattr(self, "_current_screening_group_id", "") or "").strip()
        if current_group_id:
            return current_group_id
        current_group_id = self._generate_screening_group_id(patient_id)
        self._current_screening_group_id = current_group_id
        return current_group_id

    def open_saved_patient_screening_history(self) -> None:
        handler = getattr(self, "_post_save_history_handler", None)
        if callable(handler):
            handler()

    def _set_eye_selection(self, eye_label: str):
        self._suspend_eye_guard = True
        try:
            self.p_eye.setCurrentText(str(eye_label or ""))
            self._last_eye_choice = str(self.p_eye.currentText() or "").strip()
        finally:
            self._suspend_eye_guard = False

    def _on_eye_selection_changed(self, eye_label: str):
        if self._suspend_eye_guard:
            return

        selected_eye = str(eye_label or "").strip()
        previous_eye = str(self._last_eye_choice or "").strip()
        self._last_eye_choice = selected_eye

        if not selected_eye:
            return

        # Follow-up screenings should always allow selecting an eye without triggering
        # "replace existing" prompts. Follow-ups are stored as new sessions tied to
        # the screening date, not as overwrites of prior sessions.
        current_type = str(getattr(self, "_current_screening_type", "") or "").strip().lower()
        if current_type == "follow_up" or getattr(self, "_current_previous_screening_id", None):
            return

        patient_id = str(self.p_id.text() or "").strip() if hasattr(self, "p_id") else ""
        if not patient_id:
            return

        existing = self._find_existing_eye_record(patient_id, selected_eye)
        if not existing:
            return

        existing_id = int(existing.get("id") or 0)
        active_replace_id = int(self._rescreen_replace_record_id or 0)
        if existing_id and active_replace_id and existing_id == active_replace_id:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Existing Eye Record Detected")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"A saved screening record already exists for <b>{selected_eye}</b> for this patient."
        )
        box.setInformativeText(
            "Would you like to open that eye in replace mode, or keep your current eye selection?"
        )
        replace_btn = box.addButton("Open and Replace Existing Eye", QMessageBox.ButtonRole.AcceptRole)
        keep_btn = box.addButton("Keep Current Selection", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_btn)
        box.exec()

        if box.clickedButton() == replace_btn:
            if existing_id and self.load_patient_for_rescreen(existing_id, replace_mode=True):
                self._set_eye_selection(selected_eye)
            return

        self._set_eye_selection(previous_eye)

    def init_ui(self):
        """Initialize the revised UI: patient info and image upload in one window, results in new window"""
        self._apply_ui_polish()
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)
        # Unified page: Patient Info + Image Upload
        unified_page = self.create_unified_page()
        self.results_page = ResultsWindow(self)
        self.stacked_widget.addWidget(unified_page)
        self.stacked_widget.addWidget(self.results_page)
        main_layout.addWidget(self.stacked_widget)
        self._setup_validators()

    def _resolve_icon_path(self, *filenames: str) -> str:
        icon_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        for name in filenames:
            path = os.path.join(icon_dir, name)
            if os.path.isfile(path):
                return path
        return ""

    def _tinted_icon(self, icon_path: str, color_hex: str, size: int = 20) -> QIcon:
        if not icon_path:
            return QIcon()

        source = QIcon(icon_path).pixmap(QSize(size, size))
        if source.isNull():
            return QIcon(icon_path)

        tinted = QPixmap(source.size())
        tinted.fill(Qt.GlobalColor.transparent)

        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(color_hex))
        painter.end()

        return QIcon(tinted)

    def _apply_ui_polish(self):
        self.setStyleSheet(_REDESIGN_STYLESHEET)

    def _apply_visible_dropdown_style(self, combo: QComboBox):
        arrow_icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "icons",
            "dropdown_arrow.svg",
        ).replace("\\", "/")
        combo.setStyleSheet(
            f"""
            QComboBox {{
                background:#ffffff;
                border:1.5px solid #c5d2e0;
                border-radius:6px;
                padding:6px 34px 6px 10px;
                color:#1f2937;
                min-height:28px;
            }}
            QComboBox:focus {{
                border:1.5px solid #3f7ca7;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width:28px;
                border-left:1px solid #9fb4cc;
                background:#e8f1ff;
                border-top-right-radius:6px;
                border-bottom-right-radius:6px;
            }}
            QComboBox::down-arrow {{
                image: url(\"{arrow_icon_path}\");
                width:12px;
                height:8px;
            }}
            """
        )

    def _is_dark_theme_active(self) -> bool:
        main_window = self.window()
        return bool(getattr(main_window, "_dark_mode", False)) if main_window is not self else False

    def _apply_calendar_themes(self):
        dark = self._is_dark_theme_active()
        
        # Apply theme to DOB
        if hasattr(self, "p_dob") and hasattr(self.p_dob, "apply_theme"):
            self.p_dob.apply_theme(dark)
            self._dob_default_style = self.p_dob.styleSheet()
            self._dob_invalid_style = self._dob_default_style + "QDateEdit{border:1.5px solid #ef4444;}"

        # Apply theme to Diagnosis Date
        if hasattr(self, "diabetes_diagnosis_date") and hasattr(self.diabetes_diagnosis_date, "apply_theme"):
            self.diabetes_diagnosis_date.apply_theme(dark)

        # Fallback for non-modern date widgets.
        self._dob_default_style = "QDateEdit{border:1.5px solid #d3dae3;border-radius:6px;}"
        self._dob_invalid_style = "QDateEdit{border:1.5px solid #ef4444;border-radius:6px;}"
        self.p_dob.setStyleSheet(self._dob_default_style)

    def create_unified_page(self):
        root = QWidget()
        root.setStyleSheet(_REDESIGN_STYLESHEET)
        root_layout = QVBoxLayout(root)
        embedded = bool(getattr(self, "_embedded_compact", False))
        # Wider side gutters improve readability (less edge-to-edge scanning).
        root_layout.setContentsMargins(0 if embedded else 24, 0 if embedded else 20, 0 if embedded else 24, 0 if embedded else 20)
        root_layout.setSpacing(10 if embedded else 12)

        # Follow-up Context Header
        self.followup_header = QFrame()
        self.followup_header.setObjectName("followupHeader")
        self.followup_header.setStyleSheet("""
            QFrame#followupHeader {
                background: #f0f7ff;
                border: 1.5px solid #3b82f6;
                border-radius: 12px;
            }
        """)
        self.followup_header.hide()
        fh_layout = QHBoxLayout(self.followup_header)
        fh_layout.setContentsMargins(16, 10, 16, 10)
        
        self.followup_label = QLabel("Follow-Up Screening for Patient: ")
        self.followup_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #1e40af;")
        fh_layout.addWidget(self.followup_label)
        fh_layout.addStretch()
        
        exit_followup_btn = QPushButton("Exit Follow-Up")
        exit_followup_btn.setObjectName("btnDanger")
        exit_followup_btn.setFixedWidth(120)
        exit_followup_btn.clicked.connect(self.reset_screening)
        fh_layout.addWidget(exit_followup_btn)
        
        root_layout.addWidget(self.followup_header)

        # Center the main content area so it doesn't feel edge-to-edge on wide screens.
        # Keep it wide enough for the intake + upload split, but not "ultra wide".
        center_wrap = QWidget()
        center_wrap.setStyleSheet("background: transparent;")
        center_row = QHBoxLayout(center_wrap)
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(0)
        if not embedded:
            center_row.addStretch(1)

        content_root = QWidget()
        content_root.setStyleSheet("background: transparent;")
        # Keep a handle so embedding pages can compact the width later.
        self._content_root = content_root
        # Keep the assessment form in a "comfortable" middle width.
        # Wide enough for the intake + upload split, but not edge-to-edge on large screens.
        screen = QGuiApplication.primaryScreen()
        screen_w = int(screen.availableGeometry().width()) if screen else 1366
        assess_max_w = max(1100, min(1440, int(screen_w * 0.92)))
        assess_min_w = max(880, min(1120, int(screen_w * 0.70)))
        if bool(getattr(self, "_embedded_compact", False)):
            assess_max_w = 16777215 # No artificial cap when embedded
            assess_min_w = 0
        content_root.setMinimumWidth(assess_min_w)
        content_root.setMaximumWidth(assess_max_w)
        content_root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        content_layout = QHBoxLayout(content_root)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        center_row.addWidget(content_root, 1)
        self._center_row_ref = center_row
        if not embedded:
            center_row.addStretch(1)
            root_layout.addWidget(center_wrap, 1)
        else:
            # In embedded mode, avoid the centering "gutters" — the parent layout
            # already controls margins/centering.
            root_layout.addWidget(content_root, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(0)
        content_layout.addWidget(splitter)

        def make_card():
            frame = QFrame()
            frame.setObjectName("card")
            layout = QVBoxLayout(frame)
            if embedded:
                layout.setContentsMargins(16, 14, 16, 14)
                layout.setSpacing(10)
            else:
                layout.setContentsMargins(24, 20, 24, 22)
                layout.setSpacing(14)
            return frame, layout

        self._scr_unified_labels = {}

        def section_title(layout, text, key=None):
            row = QHBoxLayout()
            row.setSpacing(8)
            title = QLabel(text.upper())
            title.setStyleSheet(
                "font-size:13px;font-weight:700;letter-spacing:1.2px;"
                "color:#3f7ca7;background:transparent;"
            )
            if key:
                self._scr_unified_labels[key] = title
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("background:#dde3ea;max-height:1px;")
            row.addWidget(title)
            row.addWidget(line, 1)
            layout.addLayout(row)

        def lbl(text, key=None):
            w = QLabel(text)
            w.setStyleSheet("font-size:11px;font-weight:500;color:#475569;background:transparent;")
            if key:
                self._scr_unified_labels[key] = w
            return w

        def field(label_text, widget, key=None):
            v = QVBoxLayout()
            v.setSpacing(5)
            v.addWidget(lbl(label_text, key))
            v.addWidget(widget)
            return v

        def row2(*fields):
            h = QHBoxLayout()
            h.setSpacing(14)
            for f in fields:
                h.addLayout(f, 1)
            return h

        def row3(*fields):
            h = QHBoxLayout()
            h.setSpacing(10)
            for f in fields:
                h.addLayout(f, 1)
            return h

        left_panel = QWidget()
        left_panel.setStyleSheet("background:transparent;")
        left_col = QVBoxLayout(left_panel)
        left_col.setContentsMargins(0, 0, 8, 0)
        left_col.setSpacing(10)

        card1, c1 = make_card()
        section_title(c1, "Patient Information", "scr_patient_info")

        self.p_id = QLineEdit()
        self.p_id.setReadOnly(True)
        self.p_id.setStyleSheet(
            "QLineEdit{background:#f6f8fb;color:#3f7ca7;border:1px solid #d3dae3;"
            "border-radius:999px;padding:4px 14px;font-family:monospace;font-size:12px;font-weight:600;}"
        )
        self.generate_patient_id()
        pid_row = QHBoxLayout()
        pid_row.setSpacing(10)
        pid_row.addWidget(lbl("Patient ID", "scr_label_pid"))
        pid_row.addWidget(self.p_id, 1)
        c1.addLayout(pid_row)

        self.p_first_name = QLineEdit()
        self.p_first_name.setPlaceholderText("")
        self.p_middle_name = QLineEdit()
        self.p_middle_name.setPlaceholderText("")
        self.p_last_name = QLineEdit()
        self.p_last_name.setPlaceholderText("")

        # Legacy compatibility: keep a single full-name field used by existing logic paths.
        self.p_name = QLineEdit()
        self.p_name.setPlaceholderText("Full name")
        self.p_name.hide()

        self.p_first_name.textChanged.connect(lambda *_: self._sync_full_name_from_parts())
        self.p_middle_name.textChanged.connect(lambda *_: self._sync_full_name_from_parts())
        self.p_last_name.textChanged.connect(lambda *_: self._sync_full_name_from_parts())
        dob_arrow_icon = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "icons",
            "dropdown_arrow.svg",
        )
        self.p_dob = ModernCalendarDateEdit(self.min_dob_date, self.max_dob_date, dob_arrow_icon, self.default_dob_date)
        self._dob_default_style = ""
        self._dob_invalid_style = ""
        self.p_dob.dateChanged.connect(self._on_dob_date_changed)
        cal = self.p_dob.calendarWidget()
        if cal is not None:
            cal.clicked.connect(self._on_dob_calendar_selected)
            cal.activated.connect(self._on_dob_calendar_selected)
        self._apply_calendar_themes()
        c1.addLayout(
            row3(
                field("First Name", self.p_first_name, "scr_label_name"),
                field("Middle Name", self.p_middle_name),
                field("Last Name", self.p_last_name),
            )
        )

        self.p_age = QSpinBox()
        self.p_age.setRange(0, 120)
        self.p_age.setSuffix(" yrs")
        self.p_age.setReadOnly(True)
        self.p_age.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.p_age.setSpecialValueText(" ")

        self.p_sex = QComboBox()
        self.p_sex.setObjectName("sexDropdown")
        self.p_sex.addItems(["", "Male", "Female", "Prefer not to say"])
        self._apply_visible_dropdown_style(self.p_sex)
        self.p_eye = QComboBox()
        self.p_eye.setObjectName("eyeDropdown")
        self.p_eye.addItems(["", "Right Eye", "Left Eye"])
        self._apply_visible_dropdown_style(self.p_eye)
        self.p_eye.currentTextChanged.connect(self._on_eye_selection_changed)
        # Eye selection is handled at diagnosis time (doctor flow), not during assessment intake.
        c1.addLayout(
            row3(
                field("Date of Birth", self.p_dob, "scr_label_dob"),
                field("Age", self.p_age, "scr_label_age"),
                field("Sex", self.p_sex, "scr_label_sex"),
            )
        )
        self.p_eye.hide()

        # Contact fields (split per request)
        self.p_phone = QLineEdit()
        self.p_phone.setPlaceholderText("")
        self.p_email = QLineEdit()
        self.p_email.setPlaceholderText("")
        self.p_address = QLineEdit()
        self.p_address.setPlaceholderText("")
        self.p_address = QLineEdit()
        self.p_address.setPlaceholderText("")

        # Constraints
        # - Phone: digits only (optional)
        # - Email: must look like an email (optional)
        self.p_phone.setValidator(QRegularExpressionValidator(QRegularExpression(r"^[0-9]{0,15}$"), self.p_phone))
        self.p_phone.setMaxLength(15)
        self.p_email.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"),
                self.p_email,
            )
        )

        # Backward-compat field used throughout legacy code paths.
        # Keep it in sync so we don't have to refactor the entire module at once.
        self.p_contact = QLineEdit()
        self.p_contact.setPlaceholderText("Phone or Email")
        self.p_contact.hide()

        def _sync_contact_summary() -> None:
            phone = self.p_phone.text().strip() if hasattr(self, "p_phone") else ""
            email = self.p_email.text().strip() if hasattr(self, "p_email") else ""
            self.p_contact.setText(phone or email)

        self.p_phone.textChanged.connect(lambda *_: _sync_contact_summary())
        self.p_email.textChanged.connect(lambda *_: _sync_contact_summary())

        # Height, Weight, and BMI
        self.height = QDoubleSpinBox()
        self.height.setRange(0, 300)
        self.height.setDecimals(1)
        self.height.setSuffix(" cm")
        self.height.setSpecialValueText(" ")
        self.height.valueChanged.connect(self._calculate_bmi)

        self.weight = QDoubleSpinBox()
        self.weight.setRange(0, 500)
        self.weight.setDecimals(1)
        self.weight.setSuffix(" kg")
        self.weight.setSpecialValueText(" ")
        self.weight.valueChanged.connect(self._calculate_bmi)

        self.bmi = QDoubleSpinBox()
        self.bmi.setRange(0, 100)
        self.bmi.setDecimals(1)
        self.bmi.setReadOnly(True)
        self.bmi.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.bmi.setSpecialValueText(" ")
        self.bmi.setStyleSheet(
            "QDoubleSpinBox{background:#f6f8fb;color:#475569;border:1.5px solid #d3dae3;border-radius:6px;padding:6px 10px;}"
        )
        c1.addLayout(row3(field("Height (cm)", self.height), field("Weight (kg)", self.weight), field("BMI", self.bmi)))

        # BMI Classification Label
        self.bmi_classification_label = QLabel(" ")
        self.bmi_classification_label.setStyleSheet(
            "QLabel{font-size:10px;font-weight:600;color:#6b7280;margin-top:-4px;margin-left:10px;}"
        )
        c1.addWidget(self.bmi_classification_label)

        # Keep Phone/Email narrower so Address can take more space.
        self.p_phone.setMaximumWidth(190)
        self.p_email.setMaximumWidth(240)
        contact_row = QHBoxLayout()
        contact_row.setSpacing(14)
        contact_row.addLayout(field("Phone", self.p_phone, "scr_label_phone"), 1)
        contact_row.addLayout(field("Email", self.p_email, "scr_label_email"), 1)
        contact_row.addLayout(field("Address", self.p_address, "scr_label_address"), 2)
        c1.addLayout(contact_row)

        # Vitals widgets (rendered in a separate card below top row).
        self.va_left = QLineEdit()
        self.va_left.setPlaceholderText("e.g. 20/20")
        self.va_right = QLineEdit()
        self.va_right.setPlaceholderText("e.g. 20/20")

        self.bp_systolic = QSpinBox()
        self.bp_systolic.setRange(0, 300)
        self.bp_systolic.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.bp_systolic.setSpecialValueText(" ")
        self.bp_diastolic = QSpinBox()
        self.bp_diastolic.setRange(0, 200)
        self.bp_diastolic.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.bp_diastolic.setSpecialValueText(" ")
        bp_w = QWidget()
        bp_w.setStyleSheet("background:transparent;")
        bp_h = QHBoxLayout(bp_w)
        bp_h.setContentsMargins(0, 0, 0, 0)
        bp_h.setSpacing(6)
        _sl = QLabel("/")
        _sl.setStyleSheet("color:#94a3b8;background:transparent;font-size:14px;")
        _ul = QLabel("mmHg")
        _ul.setStyleSheet("color:#94a3b8;font-size:11px;background:transparent;")
        bp_h.addWidget(self.bp_systolic, 1)
        bp_h.addWidget(_sl)
        bp_h.addWidget(self.bp_diastolic, 1)
        bp_h.addWidget(_ul)

        self.fbs = QSpinBox()
        self.fbs.setRange(0, 600)
        self.fbs.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.fbs.setSpecialValueText(" ")
        self.rbs = QSpinBox()
        self.rbs.setRange(0, 800)
        self.rbs.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.rbs.setSpecialValueText(" ")
        bg_w = QWidget()
        bg_w.setStyleSheet("background:transparent;")
        bg_h = QHBoxLayout(bg_w)
        bg_h.setContentsMargins(0, 0, 0, 0)
        bg_h.setSpacing(6)
        _fl = QLabel("FBS")
        _fl.setStyleSheet("font-size:11px;color:#475569;background:transparent;")
        _rl = QLabel("RBS")
        _rl.setStyleSheet("font-size:11px;color:#475569;background:transparent;")
        bg_h.addWidget(_fl)
        bg_h.addWidget(self.fbs, 1)
        bg_h.addWidget(_rl)
        bg_h.addWidget(self.rbs, 1)

        self.symptom_blurred = SymptomTag("Blurred Vision")
        self.symptom_floaters = SymptomTag("Floaters")
        self.symptom_flashes = SymptomTag("Flashes")
        # Keep legacy flag for compatibility with stored records, but hide from UI.
        self.symptom_vision_loss = SymptomTag("Vision loss")
        self.symptom_vision_loss.hide()
        self.symptom_other = QLineEdit()
        self.symptom_other.setPlaceholderText("Other symptom")
        self.symptom_other.setStyleSheet(
            "QLineEdit{background:#ffffff;border:1.5px solid #d3dae3;border-radius:999px;padding:4px 12px;"
            "font-size:12px;min-height:0;}QLineEdit:focus{border-color:#3f7ca7;background:#fff;}"
        )
        self.symptom_other.setMaximumWidth(220)

        card2, c2 = make_card()
        section_title(c2, "DIABETIC HISTORY", "scr_clinical_history")
        self.diabetes_type = QComboBox()
        self.diabetes_type.setObjectName("diabetesTypeDropdown")
        # Initialize with normal options; will be updated via signal if sex is Female
        self.diabetes_type.addItems(["", "Type 1", "Type 2", "Type 1 + Type 2"])
        self._apply_visible_dropdown_style(self.diabetes_type)
        
        # Connect Sex signal to update Diabetes options
        self.p_sex.currentTextChanged.connect(self._update_diabetes_options)
        # Trigger initial update in case sex is already selected (e.g. from EMR)
        self._update_diabetes_options(self.p_sex.currentText())
        self.diabetes_diagnosis_date = ModernCalendarDateEdit(
            self.min_diagnosis_date,
            self.max_diagnosis_date,
            dob_arrow_icon,
            self.default_diagnosis_date
        )
        self.diabetes_diagnosis_date.dateChanged.connect(self._on_diagnosis_date_changed)
        c2.addLayout(row2(field("Diabetes Type", self.diabetes_type, "scr_label_diabetes"), field("Diagnosis Date", self.diabetes_diagnosis_date)))

        self.diabetes_duration = DurationSpinBox()

        self.diabetes_duration.setReadOnly(True)
        self.diabetes_duration.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.diabetes_duration.setStyleSheet(
            "QSpinBox{background:#f6f8fb;color:#475569;border:1.5px solid #d3dae3;border-radius:6px;padding:6px 10px;}"
        )

        # Previous DR stage dropdown
        self.prev_dr_stage = QComboBox()
        self.prev_dr_stage.setObjectName("prevDRStageDropdown")
        self.prev_dr_stage.addItems(["", "No previous DR", "Mild NPDR", "Moderate NPDR", "Severe NPDR", "PDR (Proliferative)", "Unknown"])
        self._apply_visible_dropdown_style(self.prev_dr_stage)
        c2.addLayout(row2(field("Duration", self.diabetes_duration, "scr_label_duration"), field("Previous DR Stage", self.prev_dr_stage, "scr_label_prev_dr")))

        # Treatment regimen dropdown
        self.treatment_regimen = QComboBox()
        self.treatment_regimen.setObjectName("treatmentRegimenDropdown")
        self.treatment_regimen.addItems(["", "Insulin only", "Oral medications only", "Insulin + Oral medications", "Diet control only", "None/Unknown"])
        self._apply_visible_dropdown_style(self.treatment_regimen)
        c2.addLayout(field("Treatment Regimen", self.treatment_regimen, "scr_label_treatment"))
        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Enter clinical notes…")
        self.notes.setMinimumHeight(72)
        self.notes.setMaximumHeight(90)
        self.notes.hide()
        # Single-column Assessment layout:
        # Patient Information (card1) → Clinical History (card2)
        left_col.addWidget(card1)
        left_col.addWidget(card2)

        # Vital Signs & Symptoms section removed from Assessment UI (kept in data model for compatibility).
        # Widgets are still instantiated above so existing save/load logic remains stable.
        card_vitals, cv = make_card()
        section_title(cv, "VITAL SIGNS & SYMPTOMS", "scr_vitals")
        cv.addLayout(row2(field("Visual Acuity - Left", self.va_left), field("Visual Acuity - Right", self.va_right)))
        cv.addLayout(row2(field("Blood Pressure", bp_w), field("Blood Glucose", bg_w)))
        cv.addWidget(lbl("Symptoms"))
        symptoms_grid = QGridLayout()
        symptoms_grid.setHorizontalSpacing(8)
        symptoms_grid.setVerticalSpacing(6)
        symptoms_grid.addWidget(self.symptom_blurred, 0, 0)
        symptoms_grid.addWidget(self.symptom_flashes, 0, 1)
        symptoms_grid.addWidget(self.symptom_floaters, 0, 2)
        symptoms_grid.addWidget(self.symptom_other, 0, 3)
        symptoms_grid.setColumnStretch(4, 1)
        cv.addLayout(symptoms_grid)
        card_vitals.hide()
        self._card_vitals = card_vitals

        # Front desk quick actions (shown only when upload is restricted).
        self._fd_action_row = QFrame()
        self._fd_action_row.setObjectName("card")
        # Keep this card compact; don't stretch full width.
        self._fd_action_row.setMaximumWidth(360)
        self._fd_action_row.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        fd_actions = QVBoxLayout(self._fd_action_row)
        fd_actions.setContentsMargins(12, 10, 12, 10)
        fd_actions.setSpacing(10)

        purpose_row = QHBoxLayout()
        purpose_row.setSpacing(8)
        purpose_lbl = QLabel("Purpose:")
        purpose_lbl.setFixedWidth(72)
        purpose_row.addWidget(purpose_lbl, 0, Qt.AlignVCenter)
        self.fd_purpose_combo = QComboBox()
        self.fd_purpose_combo.addItems(["New patient", "Follow-up patient"])
        self.fd_purpose_combo.setFixedHeight(34)
        self.fd_purpose_combo.setMinimumWidth(200)
        self.fd_purpose_combo.setCursor(Qt.PointingHandCursor)
        # Ensure readable (white) dropdown on Windows.
        self.fd_purpose_combo.setStyleSheet(
            "QComboBox{background:#ffffff;color:#0f172a;border:1px solid #dbe4ee;border-radius:8px;padding:6px 10px;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#ffffff;color:#0f172a;selection-background-color:#dbeafe;selection-color:#0f172a;}"
        )
        purpose_row.addWidget(self.fd_purpose_combo, 0, Qt.AlignVCenter)
        purpose_row.addStretch(1)
        fd_actions.addLayout(purpose_row)

        btn_row = QHBoxLayout()
        # Align button with dropdown left edge (after the "Purpose:" label).
        btn_row.addSpacing(72 + 8)
        self.btn_fd_save_queue = QPushButton("Save and Queue Patient")
        self.btn_fd_save_queue.setObjectName("btnPrimary")
        self.btn_fd_save_queue.setMinimumHeight(40)
        self.btn_fd_save_queue.setMinimumWidth(220)
        self.btn_fd_save_queue.clicked.connect(self._save_and_queue_patient)
        btn_row.addWidget(self.btn_fd_save_queue, 0, Qt.AlignLeft)
        btn_row.addStretch(1)
        fd_actions.addLayout(btn_row)
        self._fd_action_row.hide()
        left_col.addWidget(self._fd_action_row, 0, Qt.AlignRight)

        left_col.addStretch()
        splitter.addWidget(left_panel)

        card3 = QFrame()
        card3.setObjectName("card")
        card3.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._upload_card = card3
        c3 = QVBoxLayout(card3)
        if embedded:
            c3.setContentsMargins(16, 12, 16, 12)
            c3.setSpacing(8)
            # card3.setMaximumWidth(500) # Removed per user request for wider area
        else:
            c3.setContentsMargins(24, 20, 24, 22)
            c3.setSpacing(12)
        section_title(c3, "Fundus Image Upload", "scr_image_upload")

        self.image_label = DropZoneLabel()
        if embedded and hasattr(self.image_label, "set_compact"):
            try:
                self.image_label.set_compact(True)
            except Exception:
                pass
        self.image_label.file_dropped.connect(self._on_image_dropped)

        def _dz_click(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.upload_image()

        self.image_label.mousePressEvent = _dz_click
        c3.addWidget(self.image_label, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_upload = QPushButton("Upload Image")
        self.btn_upload.setObjectName("btnPrimary")
        self.btn_upload.setMinimumHeight(38)
        self.btn_upload.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_upload.clicked.connect(self.upload_image)

        self.btn_take_picture = None

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("btnDanger")
        self.btn_clear.setMinimumHeight(38)
        self.btn_clear.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_clear.clicked.connect(self.clear_image)
        btn_row.addWidget(self.btn_upload)
        btn_row.addWidget(self.btn_clear)
        c3.addLayout(btn_row)

        self.btn_analyze = QPushButton("Analyze Image")
        self.btn_analyze.setObjectName("btnAnalyze")
        self.btn_analyze.setMinimumHeight(44)
        self.btn_analyze.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#94a3b8;border:1.1px solid #e2e8f0;border-radius:6px;font-size:14px;font-weight:700;}"
            "QPushButton:enabled{background:#2563eb;color:#ffffff;border-color:#2563eb;}"
            "QPushButton:hover:enabled{background:#1d4ed8;}"
        )
        self.btn_analyze.setEnabled(False) # Disabled until image uploaded
        self.btn_analyze.clicked.connect(self.open_results_window)
        c3.addWidget(self.btn_analyze)

        # Added tip at the bottom per redesign
        tip = QFrame()
        tip.setStyleSheet("background:#eff6ff;border-radius:8px;")
        tl = QHBoxLayout(tip)
        tl.setContentsMargins(10, 8, 10, 8)
        tl.setSpacing(8)
        info_icon = QLabel("ⓘ")
        info_icon.setStyleSheet("color:#3b82f6;font-size:14px;font-weight:bold;")
        tip_text = QLabel("Ensure the fundus image is clear and properly focused for accurate analysis.")
        tip_text.setWordWrap(True)
        tip_text.setStyleSheet("color:#1e40af;font-size:11px;font-weight:400;")
        tl.addWidget(info_icon)
        tl.addWidget(tip_text, 1)
        c3.addWidget(tip)

        self.upload_error_label = QLabel("")
        self.upload_error_label.setStyleSheet("color:#dc2626;background:transparent;font-size:12px;font-weight:600;")
        self.upload_error_label.setWordWrap(True)
        self.upload_error_label.hide()
        c3.addWidget(self.upload_error_label)

        splitter.addWidget(card3)
        self._intake_splitter = splitter
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 5)
        # Avoid hard-coded pixel sizes so the Assessment tab scales with window width.
        handle = splitter.handle(1)
        handle.setDisabled(True)
        handle.setCursor(Qt.CursorShape.ArrowCursor)
        self._apply_role_permissions()
        self._set_tab_order_unified()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollBar:vertical { width: 10px; } QScrollBar:horizontal { height: 10px; }")
        
        # Give the root a minimum width to prevent overlapping/scrambling at lower resolutions
        root.setMinimumWidth(850)
        
        scroll_area.setWidget(root)
        return scroll_area

    def _current_role(self) -> str:
        return str(getattr(self, "role", "") or "").strip().lower()

    def _is_upload_restricted(self) -> bool:
        return self._current_role() == "frontdesk"

    def _apply_role_permissions(self) -> None:
        restricted = self._is_upload_restricted()
        if hasattr(self, "_upload_card"):
            self._upload_card.setVisible(not restricted)
        if hasattr(self, "_fd_action_row"):
            self._fd_action_row.setVisible(restricted)
        if hasattr(self, "_intake_splitter"):
            if restricted:
                # When upload is restricted (frontdesk), give the intake form the
                # full available width instead of leaving unused whitespace.
                self._intake_splitter.setStretchFactor(0, 1)
                self._intake_splitter.setStretchFactor(1, 0)
                self._intake_splitter.setSizes([10_000, 0])
            else:
                # Keep proportional sizing so the Assessment layout scales with the
                # centered max-width container instead of snapping to fixed pixels.
                self._intake_splitter.setStretchFactor(0, 6)
                self._intake_splitter.setStretchFactor(1, 5)
        if hasattr(self, "btn_upload"):
            self.btn_upload.setEnabled(not restricted)
        if hasattr(self, "btn_clear"):
            self.btn_clear.setEnabled(not restricted)
        if hasattr(self, "btn_analyze"):
            self.btn_analyze.setEnabled(not restricted)
        if hasattr(self, "image_label"):
            self.image_label.setEnabled(not restricted)
            if restricted:
                self.image_label.setText("Fundus image upload is available for clinician only.")
                self.image_label.setPixmap(QPixmap())
        if hasattr(self, "upload_error_label") and restricted:
            self.upload_error_label.hide()

    def configure_role_permissions(self, role: str | None = None) -> None:
        if role is not None:
            self.role = role
        self._apply_role_permissions()

    def set_frontdesk_purpose(self, purpose: str, *, locked: bool = False) -> None:
        """
        Frontdesk helper: set purpose dropdown and optionally lock it.

        purpose: "new" | "follow_up"
        """
        if not hasattr(self, "fd_purpose_combo"):
            return
        p = str(purpose or "").strip().lower()
        target = "Follow-up patient" if ("follow" in p) else "New patient"
        idx = self.fd_purpose_combo.findText(target)
        if idx >= 0:
            self.fd_purpose_combo.setCurrentIndex(idx)
        self.fd_purpose_combo.setEnabled(not locked)

    def sync_frontdesk_purpose_lock(self) -> None:
        """Keep Purpose pinned to current workflow on Assessment entry."""
        if not hasattr(self, "fd_purpose_combo"):
            return
        purpose = "follow_up" if self._is_followup_mode() else "new"
        self.set_frontdesk_purpose(purpose, locked=True)

    def _guard_upload_permission(self) -> bool:
        if self._is_upload_restricted():
            QMessageBox.information(
                self,
                "Assessment Restricted",
                "Fundus image upload is available only on clinician assessment.",
            )
            return True
        return False


    def _validate_all_fields_for_queue(self) -> bool:
        missing = []
        
        if hasattr(self, "p_first_name") and not self.p_first_name.text().strip():
            missing.append("First Name")
        if hasattr(self, "p_last_name") and not self.p_last_name.text().strip():
            missing.append("Last Name")
            
        if not self._get_dob_date().isValid():
            missing.append("Date of Birth")
            
        if not self.p_sex.currentText().strip():
            missing.append("Sex")
            
        phone = self.p_phone.text().strip() if hasattr(self, "p_phone") else ""
        contact = self.p_contact.text().strip()
        if not (phone or contact):
            missing.append("Phone/Contact Number")
            
        if hasattr(self, "p_address") and not self.p_address.text().strip():
            missing.append("Address")
            
        # Clinical variables are no longer strictly mandatory for patients without diabetes.
        if self.diabetes_diagnosis_date.isEnabled():
            d = self._get_diagnosis_date()
            if d.isValid() and d > QDate.currentDate():
                missing.append("Valid Diabetes Diagnosis Date")
            
        if missing:
            QMessageBox.warning(
                self,
                "Missing Required Fields",
                "Please fill in all required fields before saving.\n\nMissing:\n- " + "\n- ".join(missing)
            )
            return False
            
        return True
    def _save_and_queue_patient(self) -> None:
        """Frontdesk action: save intake demographics and assign queue entry."""
        if self._guard_busy_action("saving and queueing this patient"):
            return
        if not self._is_upload_restricted():
            # Clinician flow should continue using the full screening save path.
            self.save_screening()
            return
        if not self._validate_all_fields_for_queue():
            return

        confirm_save = QMessageBox.question(
            self,
            "Confirm Save",
            "Are you sure all information is correct before saving?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm_save != QMessageBox.StandardButton.Yes:
            return

        full_name = self.p_name.text().strip()
        parts = [p for p in full_name.split() if p.strip()]
        first_name = parts[0]
        last_name = " ".join(parts[1:])

        dob_date = self._get_dob_date()
        if not dob_date.isValid():
            QMessageBox.warning(self, "Missing Information", "Please choose a valid date of birth.")
            return
        dob_str = dob_date.toString("yyyy-MM-dd")

        username = str(getattr(self, "username", "") or os.environ.get("EYESHIELD_CURRENT_USER", "")).strip()
        uid = emr.get_user_id(username)
        if not uid:
            QMessageBox.warning(self, "Account Error", "Could not resolve your user account. Please sign in again.")
            return

        sex = self.p_sex.currentText().strip()
        phone = self.p_phone.text().strip() if hasattr(self, "p_phone") else ""
        email = self.p_email.text().strip() if hasattr(self, "p_email") else ""
        address = self.p_address.text().strip() if hasattr(self, "p_address") else ""
        contact = self.p_contact.text().strip()
        if not contact:
            QMessageBox.warning(self, "Missing Information", "Please enter a contact number.")
            return

        # Visit-scoped details (do NOT write into emr_patients; these vary per date/visit).
        height_cm = float(self.height.value()) if self.height.value() > 0 else None
        weight_kg = float(self.weight.value()) if self.weight.value() > 0 else None
        dm_type = self.diabetes_type.currentText().strip()
        if dm_type == "":
            dm_type = ""
        dm_duration = float(self.diabetes_duration.value()) if self.diabetes_duration.value() > 0 else None
        hba1c_val = None  # Removed for frontdesk

        purpose_ui = str(getattr(self, "fd_purpose_combo", None).currentText() if hasattr(self, "fd_purpose_combo") else "")
        purpose = "follow_up" if "follow" in purpose_ui.lower() else "new"
        # Visit-scoped vitals/history for this queue entry.
        visit_details = {
            "visual_acuity_left": self.va_left.text().strip() if hasattr(self, "va_left") else "",
            "visual_acuity_right": self.va_right.text().strip() if hasattr(self, "va_right") else "",
            "blood_pressure_systolic": int(self.bp_systolic.value()) if hasattr(self, "bp_systolic") and self.bp_systolic.value() > 0 else None,
            "blood_pressure_diastolic": int(self.bp_diastolic.value()) if hasattr(self, "bp_diastolic") and self.bp_diastolic.value() > 0 else None,
            "fasting_blood_sugar": float(self.fbs.value()) if hasattr(self, "fbs") and self.fbs.value() > 0 else None,
            "random_blood_sugar": float(self.rbs.value()) if hasattr(self, "rbs") and self.rbs.value() > 0 else None,
            "diabetes_type": dm_type or None,
            "dm_duration_years": dm_duration,
            "hba1c": hba1c_val,
            "diabetes_diagnosis_date": self._get_diagnosis_date().toString("dd/MM/yyyy") if self._get_diagnosis_date().isValid() else None,
            "treatment_regimen": (self.treatment_regimen.currentText().strip() if hasattr(self, "treatment_regimen") else "") or None,
            "prev_dr_stage": (self.prev_dr_stage.currentText().strip() if hasattr(self, "prev_dr_stage") else "") or None,
            "prev_treatment": "Yes" if bool(getattr(getattr(self, "prev_treatment", None), "isChecked", lambda: False)()) else "No",
            "symptom_blurred_vision": 1 if getattr(self, "symptom_blurred", None) and self.symptom_blurred.isChecked() else 0,
            "symptom_floaters": 1 if getattr(self, "symptom_floaters", None) and self.symptom_floaters.isChecked() else 0,
            "symptom_flashes": 1 if getattr(self, "symptom_flashes", None) and self.symptom_flashes.isChecked() else 0,
            "symptom_vision_loss": 1 if getattr(self, "symptom_vision_loss", None) and self.symptom_vision_loss.isChecked() else 0,
            "symptom_other": (self.symptom_other.text().strip() if hasattr(self, "symptom_other") else "") or None,
            "height_cm": height_cm,
            "weight_kg": weight_kg,
            "notes": self.notes.toPlainText().strip() if hasattr(self, "notes") else "",
        }

        # Fast-path: single transaction for patient + queue + visit details.
        try:
            saved = emr.frontdesk_save_and_queue(
                acting_user_id=int(uid),
                last_name=last_name,
                first_name=first_name,
                date_of_birth=dob_str,
                sex=sex,
                contact_number=phone or contact,
                email=email,
                address=address,
                screening_purpose=purpose,
                visit_details=visit_details,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save & queue patient.\n\n{exc}")
            return

        patient_id = int(saved.get("patient_id") or 0)
        queue_id = int(saved.get("queue_id") or 0)
        queue_number = str(saved.get("queue_number") or "")
        patient_code = str(saved.get("patient_code") or "").strip()
        if patient_id:
            self._emr_patient_pk = patient_id
        if queue_id:
            self._emr_queue_entry_id = queue_id
        if patient_code:
            self.p_id.setText(patient_code)

        # Patient Records placeholder is created by EMR via
        # `ensure_legacy_patient_record_stub(screening_group_id='queue-{queue_id}')`.
        # Do NOT insert a second placeholder row here; it causes duplicate timeline cards.

        main = self.window()
        if main is not self:
            emr_page = getattr(main, "emr_page", None)
            if emr_page and hasattr(emr_page, "refresh"):
                emr_page.refresh()

        write_activity("INFO", "PATIENT_QUEUED", f"patient_id={patient_id}; queue={queue_number}")
        QMessageBox.information(
            self,
            "Saved",
            "Patient is saved.",
        )
        self.reset_screening(confirm_unsaved=False)

    def _apply_upload_placeholder_style(self):
        if hasattr(self, "image_label") and hasattr(self.image_label, "clear_image"):
            self.image_label.clear_image()
            return
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Upload a fundus image\nJPG, PNG, JPEG")
        self.image_label.setStyleSheet(
            f"""
            QLabel {{
                border: 2px dashed #9ec5fe;
                border-radius: 12px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f8fbff, stop:1 #eef5ff);
                color: #0b5ed7;
                padding: 12px;
                font-size: 14px;
                font-weight: 600;
            }}
            """
        )

    def _apply_upload_loaded_style(self):
        if hasattr(self, "image_label") and hasattr(self.image_label, "set_image"):
            return
        self.image_label.setStyleSheet(
            f"""
            QLabel {{
                border: 1px solid #cfe2ff;
                border-radius: 12px;
                background: #ffffff;
                padding: 8px;
            }}
            """
        )

    def _form_label_stylesheet(self):
        return (
            "color: #212529;"
            "background: transparent; border: none;"
            "font-size: 13px; font-weight: 600;"
        )

    def _apply_flat_form_label_style(self, form_layout: QFormLayout):
        for row in range(form_layout.rowCount()):
            item = form_layout.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item and item.widget():
                item.widget().setStyleSheet(self._form_label_stylesheet())

    def _compose_full_name(self) -> str:
        first = self.p_first_name.text().strip() if hasattr(self, "p_first_name") else ""
        middle = self.p_middle_name.text().strip() if hasattr(self, "p_middle_name") else ""
        last = self.p_last_name.text().strip() if hasattr(self, "p_last_name") else ""
        return " ".join(part for part in (first, middle, last) if part)

    def _sync_full_name_from_parts(self) -> None:
        if hasattr(self, "p_name"):
            self.p_name.setText(self._compose_full_name())

    def _set_name_parts_from_full_name(self, full_name: str) -> None:
        text = str(full_name or "").strip()
        if not all(hasattr(self, f) for f in ("p_first_name", "p_middle_name", "p_last_name")):
            if hasattr(self, "p_name"):
                self.p_name.setText(text)
            return

        tokens = [tok for tok in text.split() if tok]
        first = tokens[0] if tokens else ""
        last = tokens[-1] if len(tokens) > 1 else ""
        middle = " ".join(tokens[1:-1]) if len(tokens) > 2 else ""

        for widget, value in (
            (self.p_first_name, first),
            (self.p_middle_name, middle),
            (self.p_last_name, last),
        ):
            prev = widget.blockSignals(True)
            widget.setText(value)
            widget.blockSignals(prev)

        self._sync_full_name_from_parts()

    def _set_tab_order_unified(self):
        self.setTabOrder(self.p_first_name, self.p_middle_name)
        self.setTabOrder(self.p_middle_name, self.p_last_name)
        self.setTabOrder(self.p_last_name, self.p_dob)
        self.setTabOrder(self.p_dob, self.p_sex)
        # Tab order: sex -> phone -> email -> eye (hidden)
        if hasattr(self, "p_phone") and hasattr(self, "p_email"):
            self.setTabOrder(self.p_sex, self.p_phone)
            self.setTabOrder(self.p_phone, self.p_email)
            self.setTabOrder(self.p_email, self.p_eye)
        else:
            self.setTabOrder(self.p_sex, self.p_contact)
            self.setTabOrder(self.p_contact, self.p_eye)
        self.setTabOrder(self.p_eye, self.diabetes_type)
        self.setTabOrder(self.diabetes_type, self.diabetes_diagnosis_date)
        self.setTabOrder(self.diabetes_diagnosis_date, self.diabetes_duration)
        self.setTabOrder(self.diabetes_duration, self.prev_dr_stage)
        self.setTabOrder(self.prev_dr_stage, self.treatment_regimen)
        self.setTabOrder(self.treatment_regimen, self.va_left)
        self.setTabOrder(self.va_left, self.va_right)
        self.setTabOrder(self.va_right, self.bp_systolic)
        self.setTabOrder(self.bp_systolic, self.bp_diastolic)
        self.setTabOrder(self.bp_diastolic, self.fbs)
        self.setTabOrder(self.fbs, self.rbs)
        self.setTabOrder(self.rbs, self.symptom_blurred)
        self.setTabOrder(self.symptom_blurred, self.symptom_floaters)
        self.setTabOrder(self.symptom_floaters, self.symptom_flashes)
        self.setTabOrder(self.symptom_flashes, self.symptom_other)
        self.setTabOrder(self.symptom_other, self.btn_upload)
        self.setTabOrder(self.btn_upload, self.btn_clear)
        self.setTabOrder(self.btn_clear, self.btn_analyze)

    def _setup_validators(self):
        self.name_regex = QRegularExpression(r"^[A-Za-z][A-Za-z\s\-']*$")
        name_part_regex = QRegularExpression(r"^[A-Za-z][A-Za-z\s\-']*$")
        if hasattr(self, "p_first_name"):
            self.p_first_name.setValidator(QRegularExpressionValidator(name_part_regex, self))
        if hasattr(self, "p_middle_name"):
            self.p_middle_name.setValidator(QRegularExpressionValidator(name_part_regex, self))
        if hasattr(self, "p_last_name"):
            self.p_last_name.setValidator(QRegularExpressionValidator(name_part_regex, self))

        # Keep visual acuity fields free-text for clinician entry.

        # Connect blood pressure and glucose validation
        self.bp_systolic.editingFinished.connect(self._validate_blood_pressure)
        self.bp_diastolic.editingFinished.connect(self._validate_blood_pressure)
        self.fbs.editingFinished.connect(self._validate_blood_glucose)
        self.rbs.editingFinished.connect(self._validate_blood_glucose)

    def _normalize_visual_acuity(self, value: str) -> tuple[str, bool]:
        text = str(value or "").strip().upper()
        if not text:
            return "", True

        # Normalize low-vision shorthand and Snellen spacing variations.
        aliases = {
            "COUNTING FINGERS": "CF",
            "HAND MOTION": "HM",
            "HAND MOVEMENT": "HM",
            "LIGHT PERCEPTION": "LP",
            "NO LIGHT PERCEPTION": "NLP",
        }
        if text in aliases:
            text = aliases[text]

        if "/" in text:
            parts = [part.strip() for part in text.split("/", 1)]
            text = f"{parts[0]}/{parts[1]}"

        return text, True

    def _validate_patient_basics(self):
        follow_up_mode = self._is_followup_mode()
        self._sync_full_name_from_parts()
        name = self.p_name.text().strip()
        first_name = self.p_first_name.text().strip() if hasattr(self, "p_first_name") else ""
        last_name = self.p_last_name.text().strip() if hasattr(self, "p_last_name") else ""
        dob_date = self._get_dob_date()
        sex = self.p_sex.currentText().strip()
        contact = self.p_contact.text().strip()
        age_val = self.p_age.value()
        height_val = self.height.value() if hasattr(self, "height") else 0.0
        weight_val = self.weight.value() if hasattr(self, "weight") else 0.0

        missing_fields = []
        if not first_name:
            missing_fields.append("First Name")
        if not last_name:
            missing_fields.append("Last Name")
        if not name:
            missing_fields.append("Name")
        if not follow_up_mode and not dob_date.isValid():
            missing_fields.append("Date of Birth")
        if not follow_up_mode and not sex:
            missing_fields.append("Sex")
        if not follow_up_mode and not contact:
            missing_fields.append("Contact")
        if height_val <= 0:
            missing_fields.append("Height (cm)")
        if weight_val <= 0:
            missing_fields.append("Weight (kg)")

        if missing_fields:
            QMessageBox.warning(
                self,
                "Missing Information",
                "Please fill up every patient information field.\n\nMissing: " + ", ".join(missing_fields),
            )
            return False

        if (not follow_up_mode) and isinstance(self.p_dob, QDateEdit) and not self._dob_user_selected:
            QMessageBox.warning(self, "Missing Information", "Please select the patient's actual date of birth.")
            return False

        if not self.name_regex.match(name).hasMatch():
            QMessageBox.warning(self, "Error", "Name can only include letters, spaces, hyphens, and apostrophes")
            return False

        if (not follow_up_mode) and (age_val < 1 or age_val > 120):
            QMessageBox.warning(self, "Invalid Age", "Age must be between 1 and 120.")
            return False

        # Diabetic history is now optional per user request.

        va_left_text, _ = self._normalize_visual_acuity(self.va_left.text()) if hasattr(self, "va_left") else ("", True)
        va_right_text, _ = self._normalize_visual_acuity(self.va_right.text()) if hasattr(self, "va_right") else ("", True)

        if hasattr(self, "va_left"):
            self.va_left.setText(va_left_text)
        if hasattr(self, "va_right"):
            self.va_right.setText(va_right_text)

        return True

    # HbA1c has been removed from the UI; keep handler as a no-op for compatibility.
    def _on_hba1c_changed(self, value: float):
        return

    def _calculate_bmi(self):
        """Auto-calculate BMI when height or weight changes."""
        height_cm = self.height.value()
        weight_kg = self.weight.value()
        
        if height_cm > 0 and weight_kg > 0:
            height_m = height_cm / 100.0
            bmi_value = weight_kg / (height_m * height_m)
            self.bmi.setValue(round(bmi_value, 1))
        else:
            self.bmi.setValue(0)

        # Update BMI classification
        bmi_value = self.bmi.value()
        if bmi_value == 0:
            classification = " "
            color = "#6b7280"
        elif bmi_value < 18.5:
            classification = "Underweight"
            color = "#3b82f6"
        elif bmi_value < 25:
            classification = "Normal Weight"
            color = "#10b981"
        elif bmi_value < 30:
            classification = "Overweight"
            color = "#f59e0b"
        else:
            classification = "Obese"
            color = "#ef4444"
        
        self.bmi_classification_label.setText(classification)
        self.bmi_classification_label.setStyleSheet(
            f"QLabel{{font-size:10px;font-weight:600;color:{color};margin-top:-4px;}}"
        )

    def _on_dob_text_changed(self, text):
        digits = "".join(ch for ch in text if ch.isdigit())[:8]
        if len(digits) <= 2:
            formatted = digits
        elif len(digits) <= 4:
            formatted = f"{digits[:2]}/{digits[2:]}"
        else:
            formatted = f"{digits[:2]}/{digits[2:4]}/{digits[4:]}"

        if formatted != text:
            self.p_dob.blockSignals(True)
            self.p_dob.setText(formatted)
            self.p_dob.blockSignals(False)
            self.p_dob.setCursorPosition(len(formatted))

        self._update_dob_input_style(digits)

        self.update_age_from_dob(self._get_dob_date())

    def _update_dob_input_style(self, digits):
        has_invalid_value = False

        if len(digits) >= 1 and int(digits[0]) > 3:
            has_invalid_value = True

        if len(digits) >= 2:
            day = int(digits[:2])
            if day < 1 or day > 31:
                has_invalid_value = True

        if len(digits) >= 3 and int(digits[2]) > 1:
            has_invalid_value = True

        if len(digits) >= 4:
            month = int(digits[2:4])
            if month < 1 or month > 12:
                has_invalid_value = True

        if len(digits) == 8 and not self._get_dob_date().isValid():
            has_invalid_value = True

        self.p_dob.setStyleSheet(self._dob_invalid_style if has_invalid_value else self._dob_default_style)

    def _get_dob_date(self):
        if isinstance(self.p_dob, QDateEdit):
            date = self.p_dob.date()
            if date == self.min_dob_date:
                return QDate()
        else:
            date = QDate.fromString(self.p_dob.text().strip(), "dd/MM/yyyy")

        if not date.isValid():
            return QDate()
        if date < self.min_dob_date or date > QDate.currentDate():
            return QDate()
        return date

    def _set_dob_date(self, date: QDate, user_selected: bool = False):
        if not isinstance(self.p_dob, QDateEdit):
            return
        self._dob_programmatic_update = True
        try:
            self.p_dob.setDate(date)
        finally:
            self._dob_programmatic_update = False
        self._dob_user_selected = bool(user_selected and date.isValid() and date != self.min_dob_date)

    def _on_dob_date_changed(self, date: QDate):
        if not self._dob_programmatic_update:
            self._dob_user_selected = bool(date.isValid() and date != self.min_dob_date)
        self.update_age_from_dob(date)

    def _on_dob_calendar_selected(self, date: QDate):
        if not isinstance(self.p_dob, QDateEdit):
            return
        self._dob_user_selected = bool(date.isValid() and date != self.min_dob_date)
        self.update_age_from_dob(self.p_dob.date())

    def _on_diagnosis_date_changed(self, date: QDate):
        """Auto-calculate duration from diagnosis date."""
        self._update_duration_from_diagnosis_date()

    def _update_diagnosis_date_style(self, digits):
        """No longer needed for QDateEdit."""
        pass

        # Auto-calculate duration
        self._update_duration_from_diagnosis_date()

    def _update_diagnosis_date_style(self, digits):
        """Apply red border if invalid diagnosis date."""
        has_invalid_value = False

        # Check day first digit
        if len(digits) >= 1 and int(digits[0]) > 3:
            has_invalid_value = True

        # Check day range (1-31)
        if len(digits) >= 2:
            day = int(digits[:2])
            if day < 1 or day > 31:
                has_invalid_value = True

        # Check month first digit
        if len(digits) >= 3 and int(digits[2]) > 1:
            has_invalid_value = True

        # Check month range (1-12)
        if len(digits) >= 4:
            month = int(digits[2:4])
            if month < 1 or month > 12:
                has_invalid_value = True

        # Full validation
        if len(digits) == 8:
            diag_date = self._get_diagnosis_date()
            dob_date = self._get_dob_date()
            if not diag_date.isValid():
                has_invalid_value = True
            elif diag_date > QDate.currentDate():
                has_invalid_value = True
            elif dob_date.isValid() and diag_date < dob_date:
                has_invalid_value = True

        # Apply styling
        invalid_style = """
            QLineEdit {
                border: 1.5px solid #dc3545;
                border-radius: 6px;
                padding: 6px 8px;
            }
        """
        self.diabetes_diagnosis_date.setStyleSheet(invalid_style if has_invalid_value else LINEEDIT_STYLE)

    def _get_diagnosis_date(self):
        """Parse and validate diagnosis date from text field."""
        d = self.diabetes_diagnosis_date.date()
        if d == self.min_diagnosis_date:
            return QDate()
        return d

    def _update_duration_from_diagnosis_date(self):
        """Auto-calculate diabetes duration from diagnosis date."""
        diag_date = self._get_diagnosis_date()
        if not diag_date.isValid():
            self.diabetes_duration.setValue(0)
            return

        today = QDate.currentDate()
        months = (today.year() - diag_date.year()) * 12 + today.month() - diag_date.month()
        if today.day() < diag_date.day():
            months -= 1
        self.diabetes_duration.setValue(max(0, months))

    def _update_diabetes_options(self, sex_text: str):
        """Update diabetes type options based on patient sex."""
        if not hasattr(self, "diabetes_type"):
            return

        current_selection = self.diabetes_type.currentText()
        
        normal_options = ["", "None", "Type 1", "Type 2", "Type 1 + Type 2"]
        gestational_options = ["Gestational", "Type 1 + Gestational", "Type 2 + Gestational"]
        
        self.diabetes_type.blockSignals(True)
        self.diabetes_type.clear()
        
        if sex_text == "Female":
            self.diabetes_type.addItems(normal_options + gestational_options)
        else:
            self.diabetes_type.addItems(normal_options)
            # If a gestational option was selected and we're no longer female, reset to blank
            if current_selection in gestational_options:
                current_selection = ""
                
        # Restore selection if it still exists in the new list
        idx = self.diabetes_type.findText(current_selection)
        if idx >= 0:
            self.diabetes_type.setCurrentIndex(idx)
        else:
            self.diabetes_type.setCurrentIndex(0)
            
        self.diabetes_type.blockSignals(False)

    def _validate_blood_pressure(self):
        """Validate blood pressure ranges - no warnings, just validation."""
        sys = self.bp_systolic.value()
        dia = self.bp_diastolic.value()

        # Both must be zero or both must be filled
        if (sys == 0) != (dia == 0):
            return False

        # If filled, check that diastolic is lower than systolic
        if sys > 0 and dia >= sys:
            return False

        return True

    def _validate_blood_glucose(self):
        """Validate blood glucose ranges."""
        fbs = self.fbs.value()
        rbs = self.rbs.value()

        if fbs > 0 and (fbs < 70 or fbs > 400):
            QMessageBox.warning(
                self, "Blood Glucose",
                "Fasting blood sugar should be between 70-400 mg/dL.\nIf this reading is correct, document it in the results decision notes."
            )
            return False

        if rbs > 0 and (rbs < 70 or rbs > 600):
            QMessageBox.warning(
                self, "Blood Glucose",
                "Random blood sugar should be between 70-600 mg/dL.\nIf this reading is correct, document it in the results decision notes."
            )
            return False

        return True

    def create_patient_info_page(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(15)

        title = QLabel("Step 1: Patient Information")
        title_font = QFont("Calibri", 16, QFont.Weight.Bold)
        title.setFont(title_font)
        title.setObjectName("pageHeader")
        layout.addWidget(title)

        patient_group = QGroupBox("Patient Information")
        patient_form = QFormLayout()

        self.p_id = QLineEdit()
        self.p_id.setReadOnly(True)
        self.generate_patient_id()
        patient_form.addRow("Patient ID:", self.p_id)

        self.p_name = QLineEdit()
        self.p_name.setPlaceholderText("Full name")
        patient_form.addRow("Name:", self.p_name)

        self.p_dob = QDateEdit()
        self.p_dob.setCalendarPopup(True)
        self.p_dob.setDisplayFormat("yyyy-MM-dd")
        self.p_dob.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        custom_calendar = QCalendarWidget()
        custom_calendar.setGridVisible(True)
        custom_calendar.setStyleSheet(CALENDAR_STYLE)
        self.p_dob.setCalendarWidget(custom_calendar)
        self.p_dob.setMinimumDate(QDate(1900, 1, 1))
        self.p_dob.setSpecialValueText(" ")
        self.p_dob.setDate(self.p_dob.minimumDate())
        self.p_dob.dateChanged.connect(self.update_age_from_dob)
        patient_form.addRow("Date of Birth:", self.p_dob)

        self.p_age = QSpinBox()
        self.p_age.setRange(0, 120)
        self.p_age.setSuffix(" years")
        self.p_age.setReadOnly(True)
        self.p_age.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.p_age.setSpecialValueText(" ")
        self.p_age.setValue(0)
        patient_form.addRow("Age:", self.p_age)

        self.p_sex = QComboBox()
        self.p_sex.addItems(["", "Male", "Female", "Other"])
        patient_form.addRow("Sex:", self.p_sex)

        self.p_phone = QLineEdit()
        self.p_phone.setPlaceholderText("")
        self.p_email = QLineEdit()
        self.p_email.setPlaceholderText("")
        # Keep legacy summary for older code paths.
        self.p_contact = QLineEdit()
        self.p_contact.hide()
        self.p_phone.textChanged.connect(lambda *_: self.p_contact.setText(self.p_phone.text().strip() or self.p_email.text().strip()))
        self.p_email.textChanged.connect(lambda *_: self.p_contact.setText(self.p_phone.text().strip() or self.p_email.text().strip()))
        patient_form.addRow("Phone Number:", self.p_phone)
        patient_form.addRow("Email:", self.p_email)
        patient_form.addRow("Address:", self.p_address)

        self.p_eye = QComboBox()
        self.p_eye.addItems(["", "Both Eyes", "Left Eye", "Right Eye"])
        patient_form.addRow("Eye(s):", self.p_eye)

        patient_group.setLayout(patient_form)
        layout.addWidget(patient_group)

        clinical_group = QGroupBox("Diabetic History")
        clinical_form = QFormLayout()

        self.diabetes_type = QComboBox()
        self.diabetes_type.addItems(["", "Type 1", "Type 2", "Gestational", "Type 1 + Type 2", "Type 1 + Gestational", "Type 2 + Gestational"])
        clinical_form.addRow("Diabetes Type:", self.diabetes_type)

        self.diabetes_duration = DurationSpinBox()
        clinical_form.addRow("Duration:", self.diabetes_duration)

        self.clinical_prev_dr_stage = QComboBox()
        self.clinical_prev_dr_stage.addItems(["Select", "No previous DR", "Mild NPDR", "Moderate NPDR", "Severe NPDR", "PDR (Proliferative)", "Unknown"])
        self._apply_visible_dropdown_style(self.clinical_prev_dr_stage)
        clinical_form.addRow("Previous DR Stage:", self.clinical_prev_dr_stage)

        self.notes = QTextEdit()
        self.notes.setMaximumHeight(80)
        self.notes.setMinimumHeight(80)
        self.notes.setPlaceholderText("Enter clinical notes")
        self.notes.setStyleSheet(TEXTEDIT_STYLE)
        self.notes.hide()

        clinical_group.setLayout(clinical_form)
        layout.addWidget(clinical_group)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.cancel_screening)
        button_layout.addWidget(self.btn_cancel)
        self.btn_proceed = QPushButton("Proceed to Image")
        self.btn_proceed.clicked.connect(self.validate_and_proceed)
        button_layout.addWidget(self.btn_proceed)
        layout.addLayout(button_layout)
        layout.addStretch()
        return container

    def create_image_analysis_page(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(15)

        title = QLabel("Step 2: Image Analysis")
        title_font = QFont("Calibri", 16, QFont.Weight.Bold)
        title.setFont(title_font)
        title.setObjectName("pageHeader")
        layout.addWidget(title)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("font-size: 11pt;")
        layout.addWidget(self.summary_label)

        image_group = QGroupBox("Fundus Image")
        image_layout = QVBoxLayout()

        self.image_label = QLabel("No image loaded")
        self.image_label.setMinimumSize(450, 400)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("border: 2px dashed currentColor;")
        image_layout.addWidget(self.image_label)

        btn_layout = QHBoxLayout()
        self.btn_upload = QPushButton("Upload Image")
        self.btn_upload.clicked.connect(self.upload_image)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear_image)
        btn_layout.addWidget(self.btn_upload)
        btn_layout.addWidget(self.btn_clear)
        image_layout.addLayout(btn_layout)

        image_group.setLayout(image_layout)
        layout.addWidget(image_group, 1)

        results_group = QGroupBox("Results")
        results_layout = QFormLayout()
        self.r_class = QLabel("—")
        self.r_class.setFont(QFont("Calibri", 16, QFont.Weight.Bold))
        results_layout.addRow("Classification:", self.r_class)
        self.r_conf = QLabel("—")
        results_layout.addRow("Confidence:", self.r_conf)
        results_group.setLayout(results_layout)
        layout.addWidget(results_group)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.btn_analyze = QPushButton("Analyze Image")
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.clicked.connect(self.analyze_image)
        button_layout.addWidget(self.btn_analyze)
        self.btn_save = QPushButton("Save Screening")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_screening)
        button_layout.addWidget(self.btn_save)
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self.go_back_to_patient_info)
        button_layout.addWidget(self.btn_back)
        self.btn_new = QPushButton("New Patient")
        self.btn_new.clicked.connect(self.reset_screening)
        button_layout.addWidget(self.btn_new)
        layout.addLayout(button_layout)
        return container

    # ==================== LOGIC FUNCTIONS ====================

    def generate_patient_id(self):
        # Display ID should be the EMR patient_code (never the internal integer patient_id).
        # Prefer the EMR allocator when available; fall back to legacy generator only if EMR
        # code allocation is unavailable for some reason.
        pid = ""
        with contextlib.suppress(Exception):
            if hasattr(emr, "next_patient_code"):
                pid = str(emr.next_patient_code() or "").strip()
        if not pid:
            pid = self._next_unique_patient_id()
        self._session_patient_code = pid
        self.p_id.setText(pid)
        return pid

    def apply_emr_context(
        self,
        emr_patient: dict,
        *,
        screening_id: int | None = None,
        queue_entry_id: int | None = None,
        fundus_path: str = "",
        eye_screened: str = "",
    ) -> None:
        """Apply demographics and optional fundus image from EMR (emr_patients / new screening session)."""
        pid_pk = emr_patient.get("patient_id")
        self._emr_patient_pk = int(pid_pk) if pid_pk is not None else None
        self._emr_screening_id = int(screening_id) if screening_id else None
        self._emr_queue_entry_id = int(queue_entry_id) if queue_entry_id else None

        code = str(emr_patient.get("patient_code") or "").strip()
        # Fallback: some callers may provide only the integer patient_id.
        if not code:
            with contextlib.suppress(Exception):
                pid_pk = int(emr_patient.get("patient_id") or 0)
                if pid_pk:
                    full = emr.get_patient(pid_pk) or {}
                    code = str(full.get("patient_code") or "").strip()
        if code:
            self._session_patient_code = code
            self.p_id.setText(code)
        fn = str(emr_patient.get("first_name") or "").strip()
        ln = str(emr_patient.get("last_name") or "").strip()
        self._set_name_parts_from_full_name(f"{fn} {ln}".strip() or (ln or fn))

        dob_s = str(emr_patient.get("date_of_birth") or "")[:10]
        if dob_s and hasattr(self, "p_dob"):
            d = QDate.fromString(dob_s, "yyyy-MM-dd")
            if not d.isValid():
                d = QDate.fromString(dob_s, Qt.DateFormat.ISODate)
            if d.isValid():
                if isinstance(self.p_dob, QDateEdit):
                    self._set_dob_date(d, user_selected=True)
                self.update_age_from_dob(d)

        sex = str(emr_patient.get("sex") or "").strip()
        if sex:
            if sex == "Other":
                sex = "Prefer not to say"
            idx = self.p_sex.findText(sex)
            if idx >= 0:
                self.p_sex.setCurrentIndex(idx)
        contact_number = str(emr_patient.get("contact_number") or "")
        self.p_contact.setText(contact_number)
        if hasattr(self, "p_phone"):
            self.p_phone.setText(contact_number)
        if hasattr(self, "p_address"):
            self.p_address.setText(str(emr_patient.get("address") or ""))

        h = emr_patient.get("height_cm")
        w = emr_patient.get("weight_kg")
        if hasattr(self, "height") and h is not None:
            try:
                hv = float(h)
                if hv > 0:
                    self.height.setValue(hv)
            except (TypeError, ValueError):
                pass
        if hasattr(self, "weight") and w is not None:
            try:
                wv = float(w)
                if wv > 0:
                    self.weight.setValue(wv)
            except (TypeError, ValueError):
                pass
        if hasattr(self, "_calculate_bmi"):
            self._calculate_bmi()

        dt = str(emr_patient.get("diabetes_type") or "").strip()
        if dt and hasattr(self, "diabetes_type"):
            idx = self.diabetes_type.findText(dt)
            if idx >= 0:
                self.diabetes_type.setCurrentIndex(idx)
            else:
                self.diabetes_type.setCurrentText(dt)

        dm = emr_patient.get("dm_duration_years")
        if dm is not None and hasattr(self, "diabetes_duration"):
            try:
                self.diabetes_duration.setValue(int(float(dm)))
            except (TypeError, ValueError):
                pass

        diag_date = str(emr_patient.get("diabetes_diagnosis_date") or "").strip()
        if diag_date and hasattr(self, "diabetes_diagnosis_date"):
            qd = QDate.fromString(str(diag_date or ""), "dd/MM/yyyy")
            if qd.isValid():
                self.diabetes_diagnosis_date.setDate(qd)
            else:
                self.diabetes_diagnosis_date.setDate(self.min_diagnosis_date)

        regimen = str(emr_patient.get("treatment_regimen") or "").strip()
        if regimen and hasattr(self, "treatment_regimen"):
            idx = self.treatment_regimen.findText(regimen)
            if idx >= 0:
                self.treatment_regimen.setCurrentIndex(idx)
            else:
                self.treatment_regimen.setCurrentText(regimen)

        prev_dr = str(emr_patient.get("prev_dr_stage") or "").strip()
        if prev_dr and hasattr(self, "prev_dr_stage"):
            idx = self.prev_dr_stage.findText(prev_dr)
            if idx >= 0:
                self.prev_dr_stage.setCurrentIndex(idx)
            else:
                self.prev_dr_stage.setCurrentText(prev_dr)
        # HbA1c removed from UI; ignore EMR value if present.

        es = (eye_screened or "").strip()
        if es == "Left":
            self.p_eye.setCurrentText("Left Eye")
        elif es == "Right":
            self.p_eye.setCurrentText("Right Eye")
        elif es == "Both":
            self.p_eye.setCurrentText("Right Eye")

        fp = str(fundus_path or "").strip()
        if fp and os.path.isfile(fp):
            self.current_image = fp
            self._set_preview_image(fp)
            if hasattr(self, "btn_analyze"):
                self.btn_analyze.setEnabled(True)

    def _selected_emr_eye_screened(self) -> str:
        eye_text = str(self.p_eye.currentText() or "").strip().lower()
        if "left" in eye_text:
            return "Left"
        if "right" in eye_text:
            return "Right"
        if "both" in eye_text:
            return "Both"
        return ""

    def _ensure_emr_screening_session(self, image_path: str) -> bool:
        if not self._emr_patient_pk or not self._emr_queue_entry_id or self._emr_screening_id:
            return True

        username = str(getattr(self, "username", "") or os.environ.get("EYESHIELD_CURRENT_USER", "")).strip()
        uid = emr.get_user_id(username)
        if not uid:
            QMessageBox.warning(self, "Diagnosis", "Could not resolve your clinician account. Please sign in again.")
            return False

        patient_id = int(self._emr_patient_pk)
        queue_id = int(self._emr_queue_entry_id)
        ok, reason = emr.can_start_screening(patient_id, queue_id)
        if not ok:
            QMessageBox.warning(self, "Cannot start diagnosis", reason)
            return False

        eye = self._selected_emr_eye_screened()
        if not eye:
            QMessageBox.warning(self, "Eye Required", "Please select which eye is being diagnosed before proceeding.")
            return False
        if eye == "Both":
            QMessageBox.warning(self, "Select One Eye", "Please diagnose one eye at a time from the patient queue.")
            return False

        existing = emr.latest_visit_screening(queue_id)
        if existing and emr.should_prompt_before_new_visit_screening(existing):
            if not QMessageBox.question(
                self._modal_parent_widget(),
                "Visit already has a screening",
                (
                    f"This visit already has screening #{existing['screening_id']} "
                    f"(status: {existing.get('session_status')}).\n\n"
                    "Create another screening under the same visit?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes:
                return False

        path = str(image_path or "").strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Diagnosis", "Fundus image file is missing or invalid.")
            return False
        path = os.path.normpath(os.path.abspath(path))

        screening_count = emr.count_screenings_for_patient(patient_id)
        screening_type = "initial" if screening_count == 0 else "follow_up"
        try:
            screening_id = emr.create_screening_session(
                patient_id,
                queue_id,
                uid,
                screening_type,
                eye,
                {eye: path},
            )
        except Exception as err:
            QMessageBox.warning(self, "Diagnosis", f"Could not start the screening session.\n\n{err}")
            return False

        self._emr_screening_id = int(screening_id)
        return True

    def _next_unique_patient_id(self):
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for _ in range(25):
            # Short, readable ID: ES-YYMMDD-XXXXX (e.g., ES-260316-A9K2M)
            stamp = datetime.now().strftime("%y%m%d")
            suffix = "".join(secrets.choice(alphabet) for _ in range(5))
            candidate = f"ES-{stamp}-{suffix}"
            if not self._patient_id_exists(candidate):
                return candidate

        # Fallback uses a longer, high-entropy suffix if repeated collisions happen.
        fallback = datetime.now().strftime("%y%m%d")
        return f"ES-{fallback}-{secrets.token_hex(4).upper()}"

    def _patient_id_exists(self, patient_id):
        patient_id = str(patient_id or "").strip()
        if not patient_id:
            return False

        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM patient_records WHERE patient_id = ? LIMIT 1", (patient_id,))
            exists = cur.fetchone() is not None
            conn.close()
            return exists
        except Exception:
            return False

    def update_age_from_dob(self, date):
        if not date.isValid() or date == getattr(self, "min_dob_date", QDate(1900, 1, 1)):
            self.p_age.setValue(0)
            return
        today = QDate.currentDate()
        age = today.year() - date.year()
        if (today.month(), today.day()) < (date.month(), date.day()):
            age -= 1
        self.p_age.setValue(max(0, age))

    def validate_and_proceed(self):
        if not self._validate_patient_basics():
            return
        dob_date = self._get_dob_date()
        dob_str = dob_date.toString("yyyy-MM-dd") if dob_date.isValid() else ""
        summary = f"<b>{self.p_name.text()}</b> | ID: {self.p_id.text()} | DOB: {dob_str} | Age: {self.p_age.value()}"
        self.summary_label.setText(summary)
        self.stacked_widget.setCurrentIndex(1)

    def cancel_screening(self):
        if self._guard_busy_action("canceling screening"):
            return

        reply = QMessageBox.question(
            self, "Cancel", "Are you sure you want to cancel? All data will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.reset_screening()

    def go_back_to_patient_info(self):
        if self._guard_busy_action("going back"):
            return

        reply = QMessageBox.question(
            self, "Go Back", "Going back will clear the image. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_image()
            self.stacked_widget.setCurrentIndex(0)

    def _is_followup_mode(self) -> bool:
        screening_type = str(getattr(self, "_current_screening_type", "") or "").strip().lower()
        return screening_type == "follow_up" or bool(getattr(self, "_current_previous_screening_id", None))

    def _set_patient_context_locked(self, locked: bool):
        """Two modes: full edit for new patient, strict edit limits for follow-up."""

        def _set_ro(widget_name: str, read_only: bool):
            if hasattr(self, widget_name):
                getattr(self, widget_name).setReadOnly(read_only)

        def _set_enabled(widget_name: str, enabled: bool):
            if hasattr(self, widget_name):
                getattr(self, widget_name).setEnabled(enabled)

        is_follow_up_locked = bool(locked)
        self._followup_restricted_mode = is_follow_up_locked

        # Editable in follow-up mode: height and weight only.
        _set_ro("height", False)
        _set_ro("weight", False)
        _set_enabled("prev_dr_stage", False if is_follow_up_locked else True)

        # Identity and demographics
        _set_ro("p_first_name", is_follow_up_locked)
        _set_ro("p_middle_name", is_follow_up_locked)
        _set_ro("p_last_name", is_follow_up_locked)
        _set_ro("p_name", is_follow_up_locked)
        _set_ro("p_contact", is_follow_up_locked)
        _set_ro("p_phone", is_follow_up_locked)
        _set_ro("p_email", is_follow_up_locked)
        _set_ro("p_address", is_follow_up_locked)
        _set_enabled("p_sex", not is_follow_up_locked)
        _set_enabled("p_eye", not is_follow_up_locked)

        if hasattr(self, "p_dob"):
            if isinstance(self.p_dob, QDateEdit):
                self.p_dob.setReadOnly(is_follow_up_locked)
                self.p_dob.setEnabled(not is_follow_up_locked)
            else:
                self.p_dob.setReadOnly(is_follow_up_locked)

        # Clinical history (Keep editable even in follow-up mode per user request)
        _set_enabled("diabetes_type", True)
        _set_ro("diabetes_diagnosis_date", False)
        # HbA1c removed from UI.
        _set_enabled("treatment_regimen", True)
        if hasattr(self, "prev_treatment"):
            self.prev_treatment.setEnabled(True)

        # Vitals, symptoms, notes
        _set_ro("va_left", is_follow_up_locked)
        _set_ro("va_right", is_follow_up_locked)
        _set_ro("symptom_other", is_follow_up_locked)
        if hasattr(self, "notes"):
            self.notes.setReadOnly(is_follow_up_locked)

        for spin_name in ("bp_systolic", "bp_diastolic", "fbs", "rbs"):
            if hasattr(self, spin_name):
                spin = getattr(self, spin_name)
                spin.setReadOnly(is_follow_up_locked)
                spin.setEnabled(True)

        for tag_name in ("symptom_blurred", "symptom_floaters", "symptom_flashes", "symptom_vision_loss"):
            _set_enabled(tag_name, not is_follow_up_locked)

        # Hard-lock non-allowed fields during follow-up.
        if is_follow_up_locked:
            _set_enabled("diabetes_duration", False)
        else:
            _set_enabled("diabetes_duration", True)

    def reset_screening(self, confirm_unsaved: bool = True):
        if self._guard_busy_action("starting a new screening"):
            return

        has_unsaved_progress = (not getattr(self, "_current_eye_saved", False)) and self._has_any_draft_content()
        if confirm_unsaved and has_unsaved_progress:
            confirm = QMessageBox.question(
                self,
                "Start New Screening",
                "You have unsaved screening data. Start a new screening and discard current progress?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self._reparent_results_into_stack(set_index_0=False)
        self._set_patient_context_locked(False)
        if hasattr(self, "followup_header"):
            self.followup_header.hide()
        
        self._current_screening_type = ""
        self._current_previous_screening_id = None
        self._current_follow_up_flag = ""
        self._current_followup_date = ""
        self._current_followup_label = ""
        self._current_screening_group_id = ""
        self._followup_restricted_mode = False
        self._emr_patient_pk = None
        self._emr_screening_id = None
        self._emr_queue_entry_id = None
        self.sync_frontdesk_purpose_lock()

        self.generate_patient_id()
        self.p_name.clear()
        if hasattr(self, "p_first_name"):
            self.p_first_name.clear()
        if hasattr(self, "p_middle_name"):
            self.p_middle_name.clear()
        if hasattr(self, "p_last_name"):
            self.p_last_name.clear()
        self.p_contact.clear()
        if hasattr(self, "p_phone"):
            self.p_phone.clear()
        if hasattr(self, "p_address"):
            self.p_address.clear()
        if hasattr(self, "p_email"):
            self.p_email.clear()
        if isinstance(self.p_dob, QDateEdit):
            self._set_dob_date(self.min_dob_date, user_selected=False)
        else:
            self.p_dob.clear()
        self.p_age.setValue(0)
        self.p_sex.setCurrentIndex(0)
        self.p_eye.setCurrentIndex(0)
        self.diabetes_type.setCurrentIndex(0)
        if hasattr(self, "diabetes_diagnosis_date"):
            self.diabetes_diagnosis_date.clear()
        self.diabetes_duration.setValue(0)
        # HbA1c removed from UI.
        if hasattr(self, "prev_treatment"):
            self.prev_treatment.setChecked(False)
        if hasattr(self, "va_left"):
            self.va_left.clear()
        if hasattr(self, "va_right"):
            self.va_right.clear()
        if hasattr(self, "bp_systolic"):
            self.bp_systolic.setValue(0)
        if hasattr(self, "bp_diastolic"):
            self.bp_diastolic.setValue(0)
        if hasattr(self, "fbs"):
            self.fbs.setValue(0)
        if hasattr(self, "rbs"):
            self.rbs.setValue(0)
        if hasattr(self, "height"):
            self.height.setValue(0)
        if hasattr(self, "weight"):
            self.weight.setValue(0)
        if hasattr(self, "bmi"):
            self.bmi.setValue(0)
        if hasattr(self, "treatment_regimen"):
            self.treatment_regimen.setCurrentIndex(0)
        if hasattr(self, "prev_dr_stage"):
            self.prev_dr_stage.setCurrentIndex(0)
        self.symptom_blurred.setChecked(False)
        self.symptom_floaters.setChecked(False)
        self.symptom_flashes.setChecked(False)
        self.symptom_vision_loss.setChecked(False)
        self.symptom_other.clear()
        self.notes.clear()
        self.current_image = None
        self._apply_upload_placeholder_style()
        self.last_result_class = "Pending"
        self.last_result_conf = "Pending"
        self._last_saved_signature = ""
        self._last_saved_at = ""
        self._last_saved_source_path = ""
        self._current_eye_saved = False
        self._first_eye_result = None
        self._second_eye_result = None
        self._is_second_eye_flow = False
        self._session_patient_code = ""
        self._rescreen_replace_record_id = None
        self._current_screening_type = ""
        self._current_previous_screening_id = None
        self._current_follow_up_flag = ""
        self._current_followup_date = ""
        self._current_followup_label = ""
        self._current_screening_group_id = ""
        self._flow_guard.reset()
        self._set_navigation_locked(False)
        self.btn_analyze.setEnabled(False)
        self._set_upload_error("")
        self.discard_draft_session()
        self.stacked_widget.setCurrentIndex(0)
        self.last_ai_classification = "Pending"
        self.last_doctor_classification = "Pending"
        self.last_decision_mode = "pending"
        self.last_override_justification = ""
        self.last_doctor_findings = ""
        if hasattr(self, "results_page"):
            self.results_page._doctor_classification = "Pending"
            self.results_page._decision_mode = "pending"
            self.results_page._override_justification = ""
            self.results_page._doctor_findings = ""
            self.results_page.override_reason_input.clear()
            self.results_page.findings_input.clear()
            self.results_page._refresh_decision_ui_state()
        if hasattr(self, "p_eye"):
            self.p_eye.setEnabled(True)

    def _handle_flow_blocked(self, message: str) -> bool:
        """Offer quick recovery when both eyes are already completed in this session."""
        text = str(message or "")
        if "already been analyzed" not in text.lower():
            return False

        box = QMessageBox(self)
        box.setWindowTitle("Screening Complete")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(text)
        box.setInformativeText("You can start a new screening session or return to the current results.")
        start_btn = box.addButton("Start New Screening", QMessageBox.ButtonRole.AcceptRole)
        back_btn = box.addButton("Back to Screening Results", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Stay", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        chosen = box.clickedButton()
        if chosen == start_btn:
            self.reset_screening()
        elif chosen == back_btn:
            self._set_navigation_locked(False)
            self.stacked_widget.setCurrentIndex(1)
        return True

    def load_patient_for_rescreen(self, record_id: int, replace_mode: bool = False):
        """Load a patient from database for rescreening.

        Args:
            record_id: Database record ID to load
            replace_mode: If True, will replace this record when saving. If False, creates new record.
        """
        try:
            record_id = int(record_id)  # Ensure it's an integer
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()

            # Use exact same query as generate_report's _fetch_full_record
            cur.execute("""
                SELECT id, patient_id, name, birthdate, age, sex, contact, phone, address, eyes,
                       diabetes_type, duration, hba1c, prev_treatment, notes,
                       result, confidence, screened_at,
                       ai_classification, doctor_classification, decision_mode, override_justification, final_diagnosis_icdr, doctor_findings,
                       visual_acuity_left, visual_acuity_right,
                       blood_pressure_systolic, blood_pressure_diastolic,
                       fasting_blood_sugar, random_blood_sugar,
                       symptom_blurred_vision, symptom_floaters,
                       symptom_flashes, symptom_vision_loss,
                       source_image_path, heatmap_image_path,
                       image_sha256, image_saved_at,
                       height, weight, bmi, treatment_regimen, prev_dr_stage, diabetes_diagnosis_date,
                       follow_up, followup_date, followup_label, screening_type, previous_screening_id, screening_group_id
                FROM patient_records WHERE id = ?
            """, (record_id,))
            row = cur.fetchone()
            conn.close()

            if not row:
                write_activity("ERROR", "LOAD_RESCREEN_FAILED", f"No record found in DB for id={record_id}")
                return False

            # Map row tuple to values by position (matching query order)
            (id_val, patient_id, name, birthdate, age, sex, contact, phone, address, eyes,
             diabetes_type, duration, hba1c, prev_treatment, notes,
             result, confidence, screened_at,
             ai_classification, doctor_classification, decision_mode, override_justification, final_diagnosis_icdr, doctor_findings,
             va_left, va_right,
             bp_sys, bp_dia,
             fbs, rbs,
             symptom_blurred, symptom_floaters,
             symptom_flashes, symptom_vision_loss,
             source_image_path, heatmap_image_path,
             image_sha256, image_saved_at,
             height_val, weight_val, bmi_val, treat_reg, prev_dr, diag_date,
             follow_up_flag, followup_date, followup_label, screening_type, previous_screening_id, screening_group_id) = row

            # Load data from record with safe type conversion
            self.p_id.setText(str(patient_id or ""))
            self._set_name_parts_from_full_name(str(name or ""))

            # DOB widget can be a QDateEdit (current UI) or text field (legacy UI)
            dob_text = str(birthdate or "").strip()
            if isinstance(self.p_dob, QDateEdit):
                dob_qdate = QDate()
                for fmt in ("yyyy-MM-dd", "MM/dd/yyyy", "dd/MM/yyyy"):
                    parsed = QDate.fromString(dob_text, fmt)
                    if parsed.isValid():
                        dob_qdate = parsed
                        break

                if dob_qdate.isValid():
                    min_date = self.p_dob.minimumDate()
                    max_date = self.p_dob.maximumDate()
                    if min_date.isValid() and dob_qdate < min_date:
                        dob_qdate = min_date
                    if max_date.isValid() and dob_qdate > max_date:
                        dob_qdate = max_date
                    self._set_dob_date(dob_qdate, user_selected=True)
                elif hasattr(self, "default_dob_date") and isinstance(self.default_dob_date, QDate):
                    self._set_dob_date(self.default_dob_date, user_selected=False)
            else:
                self.p_dob.setText(dob_text)

            # Safe int conversion for age
            try:
                age_val = int(float(str(age or 0)))
                self.p_age.setValue(age_val)
            except (ValueError, TypeError):
                self.p_age.setValue(0)

            # Safe sex setting
            sex_str = str(sex or "").strip()
            if sex_str and self.p_sex.findText(sex_str) >= 0:
                self.p_sex.setCurrentText(sex_str)
            else:
                self.p_sex.setCurrentIndex(0)

            self.p_contact.setText(str(contact or ""))
            if hasattr(self, "p_phone") and hasattr(self, "p_email"):
                contact_str = str(phone or contact or "").strip()
                if "@" in contact_str and "." in contact_str:
                    self.p_email.setText(contact_str)
                    self.p_phone.setText("")
                else:
                    self.p_phone.setText(contact_str)
                    self.p_email.setText("")
            if hasattr(self, "p_address"):
                self.p_address.setText(str(address or ""))

            # Safe diabetes type setting
            diabetes_str = str(diabetes_type or "").strip()
            if diabetes_str and diabetes_str != "":
                if self.diabetes_type.findText(diabetes_str) >= 0:
                    self.diabetes_type.setCurrentText(diabetes_str)
                else:
                    self.diabetes_type.setCurrentIndex(0)
            else:
                self.diabetes_type.setCurrentIndex(0)

            # Safe int conversion for duration
            try:
                duration_val = int(float(str(duration or 0)))
                self.diabetes_duration.setValue(duration_val)
            except (ValueError, TypeError):
                self.diabetes_duration.setValue(0)

            # HbA1c removed from UI.

            # Safe prev_treatment boolean
            try:
                if hasattr(self, "prev_treatment"):
                    self.prev_treatment.setChecked(bool(prev_treatment and str(prev_treatment).lower() in ("yes", "true", "1")))
            except Exception:
                if hasattr(self, "prev_treatment"):
                    self.prev_treatment.setChecked(False)

            # Mapping for newly added fields
            try:
                self.va_left.setText(str(va_left or ""))
                self.va_right.setText(str(va_right or ""))
                
                # Safe int conversion for blood pressure
                try:
                    self.bp_systolic.setValue(int(float(str(bp_sys or 0))))
                except (ValueError, TypeError):
                    self.bp_systolic.setValue(0)
                try:
                    self.bp_diastolic.setValue(int(float(str(bp_dia or 0))))
                except (ValueError, TypeError):
                    self.bp_diastolic.setValue(0)
                
                # Safe int conversion for blood glucose
                try:
                    self.fbs.setValue(int(float(str(fbs or 0))))
                except (ValueError, TypeError):
                    self.fbs.setValue(0)
                try:
                    self.rbs.setValue(int(float(str(rbs or 0))))
                except (ValueError, TypeError):
                    self.rbs.setValue(0)
                
                # Symptoms mapping
                self.symptom_blurred.setChecked(str(symptom_blurred or "").lower() == "yes")
                self.symptom_floaters.setChecked(str(symptom_floaters or "").lower() == "yes")
                self.symptom_flashes.setChecked(str(symptom_flashes or "").lower() == "yes")
                self.symptom_vision_loss.setChecked(str(symptom_vision_loss or "").lower() == "yes")

                # Height, Weight, BMI
                try:
                    self.height.setValue(float(str(height_val or 0.0)))
                except (ValueError, TypeError):
                    self.height.setValue(0.0)
                try:
                    self.weight.setValue(float(str(weight_val or 0.0)))
                except (ValueError, TypeError):
                    self.weight.setValue(0.0)
                try:
                    self.bmi.setValue(float(str(bmi_val or 0.0)))
                except (ValueError, TypeError):
                    self.bmi.setValue(0.0)
                
                # Treatment regimen
                treat_str = str(treat_reg or "").strip()
                if treat_str and self.treatment_regimen.findText(treat_str) >= 0:
                    self.treatment_regimen.setCurrentText(treat_str)
                else:
                    self.treatment_regimen.setCurrentIndex(0)
                
                # Previous DR Stage
                prev_dr_str = str(prev_dr or "").strip()
                if prev_dr_str and self.prev_dr_stage.findText(prev_dr_str) >= 0:
                    self.prev_dr_stage.setCurrentText(prev_dr_str)
                else:
                    self.prev_dr_stage.setCurrentIndex(0)
                
                # Diagnosis Date
                qd = QDate.fromString(str(diag_date or ""), "dd/MM/yyyy")
                if qd.isValid():
                    self.diabetes_diagnosis_date.setDate(qd)
                else:
                    self.diabetes_diagnosis_date.setDate(self.min_diagnosis_date)

            except Exception as e:
                write_activity("WARNING", "LOAD_RESCREEN_MAPPING_PARTIAL", f"Error mapping clinical fields: {str(e)}")

            self.notes.setPlainText(str(notes or ""))
            self.last_ai_classification = str(ai_classification or result or "Pending")
            self.last_doctor_classification = str(doctor_classification or final_diagnosis_icdr or result or "Pending")
            self.last_decision_mode = str(decision_mode or "accepted")
            self.last_override_justification = str(override_justification or "")
            self.last_doctor_findings = str(doctor_findings or "")
            if hasattr(self, "results_page"):
                self.results_page._doctor_classification = self.last_doctor_classification
                self.results_page._decision_mode = self.last_decision_mode
                self.results_page._override_justification = self.last_override_justification
                self.results_page._doctor_findings = self.last_doctor_findings
                self.results_page.override_reason_input.setText(self.last_override_justification)
                self.results_page.findings_input.setText(self.last_doctor_findings)
                if self.last_doctor_classification in ("No DR", "Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"):
                    if hasattr(self.results_page, "doctor_classification_combo"):
                        self.results_page.doctor_classification_combo.setCurrentText(self.last_doctor_classification)
                    elif hasattr(self.results_page, "doctor_classification_input"):
                        self.results_page.doctor_classification_input.setText(self.last_doctor_classification)
                self.results_page._refresh_decision_ui_state()

            # Set eye based on previous record - match against available options
            eye_value = str(eyes or "").strip()
            self._suspend_eye_guard = True
            try:
                if eye_value:
                    # Try exact match first
                    idx = self.p_eye.findText(eye_value)
                    if idx >= 0:
                        self.p_eye.setCurrentIndex(idx)
                    else:
                        # Try partial match as fallback
                        matched = False
                        for i in range(self.p_eye.count()):
                            item_text = self.p_eye.itemText(i)
                            if eye_value.lower() in item_text.lower() or item_text.lower() in eye_value.lower():
                                self.p_eye.setCurrentIndex(i)
                                matched = True
                                break
                        if not matched:
                            self.p_eye.setCurrentIndex(0)
                else:
                    self.p_eye.setCurrentIndex(0)
            finally:
                self._suspend_eye_guard = False
            self._last_eye_choice = str(self.p_eye.currentText() or "").strip()

            # Clear image for fresh screening
            self.clear_image()
            self._current_eye_saved = False
            self._first_eye_result = None
            self._flow_guard.reset()
            self._set_navigation_locked(False)

            # Store replace mode flag
            self._rescreen_replace_record_id = record_id if replace_mode else None
            self._current_screening_type = str(screening_type or "").strip()
            self._current_previous_screening_id = int(previous_screening_id) if str(previous_screening_id or "").strip() else None
            self._current_follow_up_flag = str(follow_up_flag or "").strip()
            self._current_followup_date = str(followup_date or "").strip()
            self._current_followup_label = str(followup_label or "").strip()
            self._current_screening_group_id = str(screening_group_id or "").strip() if replace_mode else ""

            # Front desk UI: pin Purpose to current mode while on assessment.
            self.sync_frontdesk_purpose_lock()

            # Bridge legacy patient_records.db patient_id (code) to EMR patient_code so front desk
            # follow-ups save/queue the same patient instead of creating duplicates.
            try:
                legacy_code = str(patient_id or "").strip()
                if legacy_code:
                    emr_patient = emr.get_patient_by_code(legacy_code)
                    if emr_patient:
                        self._emr_patient_pk = int(emr_patient.get("patient_id") or 0) or None
                    else:
                        username = str(getattr(self, "username", "") or os.environ.get("EYESHIELD_CURRENT_USER", "")).strip()
                        uid = emr.get_user_id(username)
                        if uid:
                            full_name = self.p_name.text().strip()
                            parts = [p for p in full_name.split() if p.strip()]
                            first = parts[0] if parts else "Unknown"
                            last = " ".join(parts[1:]) if len(parts) > 1 else "Unknown"
                            dob_dt = self._get_dob_date()
                            dob_iso = dob_dt.toString("yyyy-MM-dd") if hasattr(dob_dt, "isValid") and dob_dt.isValid() else ""
                            if dob_iso:
                                new_pid = emr.create_patient(
                                    uid,
                                    last_name=last,
                                    first_name=first,
                                    date_of_birth=dob_iso,
                                    patient_code=legacy_code,
                                    sex=self.p_sex.currentText().strip(),
                                    contact_number=self.p_contact.text().strip(),
                                )
                                self._emr_patient_pk = int(new_pid)
            except Exception:
                # Non-fatal; front desk can still save & queue which will create patient if needed.
                pass

            write_activity("INFO", "LOAD_RESCREEN", f"Loaded patient for rescreening: record_id={record_id}, replace_mode={replace_mode}")
            return True

        except Exception as e:
            err_detail = traceback.format_exc()
            write_activity("ERROR", "LOAD_RESCREEN_FAILED", f"Exception: {type(e).__name__}: {str(e)} | {err_detail}")
            return False

    def load_patient_for_followup(self, record_id: int) -> bool:
        """Load an existing patient and continue the case as a follow-up screening."""
        if not self.load_patient_for_rescreen(record_id, replace_mode=False):
            return False

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._rescreen_replace_record_id = None
        self._current_screening_type = "follow_up"
        self._current_previous_screening_id = int(record_id)
        self._current_follow_up_flag = "Yes"
        self._current_followup_date = timestamp
        self._current_followup_label = "Follow-up screening"
        self._current_screening_group_id = ""

        # Front desk UI: follow-up loads should pin Purpose to Follow-up.
        self.sync_frontdesk_purpose_lock()
        
        # Show follow-up header
        if hasattr(self, "followup_header"):
            name = self.p_name.text().strip() or "Unknown"
            pid = self.p_id.text().strip() or "No ID"
            self.followup_label.setText(f"Follow-Up Screening for Patient: {name} ({pid})")
            self.followup_header.show()
        
        # Lock patient context
        self._set_patient_context_locked(True)
        
        write_activity("INFO", "LOAD_FOLLOW_UP", f"Loaded patient for follow-up: record_id={record_id}")
        return True

    def upload_image(self):
        if self._guard_upload_permission():
            return
        if self._guard_busy_action("uploading a new image"):
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Image Upload",
            "Before uploading, please ensure the selected image is clear, in focus, and properly illuminated so it can be accepted by the system.\n\nDo you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Fundus Image", "", "Image Files (*.jpg *.jpeg *.png *.tif *.tiff *.bmp)"
        )
        if path:
            ok, msg = self._validate_image_selection(path)
            if not ok:
                self._set_upload_error(msg)
                return
            self.current_image = path
            if hasattr(self.image_label, "set_image"):
                self.image_label.set_image(path)
            else:
                self._set_preview_image(path)
            self.btn_analyze.setEnabled(True)
            self._set_upload_error("")

    def take_picture_for_screening(self):
        QMessageBox.information(
            self,
            "Camera Removed",
            "Camera capture has been removed from this build. Please use Upload Image to continue.",
        )

    def _on_camera_capture_return(self, capture_packet: dict):
        image_path = str((capture_packet or {}).get("image_path") or "").strip()
        if not image_path or not os.path.isfile(image_path):
            QMessageBox.warning(
                self,
                "Capture Failed",
                "Captured image could not be loaded. Please retake and save again.",
            )
            return

        packet_patient_id = str((capture_packet or {}).get("patient_id") or "").strip()
        current_patient_id = self.p_id.text().strip()
        if packet_patient_id and current_patient_id and packet_patient_id != current_patient_id:
            QMessageBox.critical(
                self,
                "Patient Mismatch",
                "Captured image patient ID does not match the active Screening patient.\n\n"
                "Please restart capture from the current patient record.",
            )
            write_activity(
                "ERROR",
                "CAMERA_CAPTURE_PATIENT_MISMATCH",
                f"packet_patient_id={packet_patient_id}; current_patient_id={current_patient_id}; path={image_path}",
            )
            return

        eye_label = str((capture_packet or {}).get("eye_label") or "").strip()
        if eye_label in ("OD", "Right Eye"):
            self.p_eye.setCurrentText("Right Eye")
        elif eye_label in ("OS", "Left Eye"):
            self.p_eye.setCurrentText("Left Eye")

        self.current_image = image_path
        if hasattr(self.image_label, "set_image"):
            self.image_label.set_image(image_path)
        else:
            self._set_preview_image(image_path)
        self.btn_analyze.setEnabled(True)
        self._set_upload_error("")

        main_window = self.window()
        if main_window is not self and hasattr(main_window, "_navigate_to"):
            main_window._navigate_to(1, nav_key="Screening")

    def _on_image_dropped(self, path: str):
        if self._guard_upload_permission():
            return
        if self._guard_busy_action("uploading a new image"):
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Image Upload",
            "Before uploading, please ensure the selected image is clear, in focus, and properly illuminated so it can be accepted by the system.\n\nDo you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        ok, msg = self._validate_image_selection(path)
        if not ok:
            self._set_upload_error(msg)
            return
        self.current_image = path
        if hasattr(self.image_label, "set_image"):
            self.image_label.set_image(path)
        else:
            self._set_preview_image(path)
        self.btn_analyze.setEnabled(True)
        self._set_upload_error("")

    def _set_upload_error(self, message: str):
        if not hasattr(self, "upload_error_label"):
            return
        text = str(message or "").strip()
        self.upload_error_label.setText(text)
        self.upload_error_label.setVisible(bool(text))

    def _validate_image_selection(self, path: str) -> tuple[bool, str]:
        allowed_ext = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
        ext = os.path.splitext(path)[1].lower()
        if ext not in allowed_ext:
            return False, "Unsupported format. Use JPG, PNG, TIFF, or BMP."
        try:
            size_bytes = os.path.getsize(path)
        except OSError as err:
            return False, f"Cannot access selected image: {err}"
        max_bytes = 50 * 1024 * 1024
        if size_bytes > max_bytes:
            return False, "File is too large. Maximum allowed size is 50MB."
        if not os.path.isfile(path):
            return False, "Source file no longer found at selected path. Please re-select the image."
        return True, ""

    def screen_another_image(self):
        """Pick a new image from the results page, re-run analysis, update results in place."""
        if self._guard_upload_permission():
            return
        if self._guard_busy_action("re-screening"):
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Image Upload",
            "Before uploading, please ensure the selected image is clear, in focus, and properly illuminated so it can be accepted by the system.\n\nDo you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Fundus Image", "", "Image Files (*.jpg *.jpeg *.png *.tif *.tiff *.bmp)"
        )
        if not path:
            return
        ok, msg = self._validate_image_selection(path)
        if not ok:
            QMessageBox.warning(self, "Invalid Image", msg)
            return
        self.current_image = path
        # Update the upload panel too so it stays in sync
        self._set_preview_image(path)
        self.btn_analyze.setEnabled(True)

        # Re-run inference with the new image
        self.results_page.set_results(
            self.p_name.text(), path,
            "Analyzing…", "Please wait",
        )
        patient_data = self._collect_patient_data()
        self._worker = _InferenceWorker(path)
        self._worker.result_ready.connect(
            lambda label, conf: self._on_prediction_ready(
                label, conf, self.p_eye.currentText(), patient_data
            )
        )
        self._worker.finished.connect(
            lambda label, conf, hmap: self._on_inference_done(
                label, conf, hmap, self.p_eye.currentText(), patient_data
            )
        )
        self._worker.error.connect(self._on_inference_error)
        self._worker.ungradable.connect(self._on_image_ungradable)
        self._set_navigation_locked(True)
        self._worker.start()

    def _set_preview_image(self, path: str):
        if hasattr(self.image_label, "set_image"):
            self.image_label.set_image(path)
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        target_size = self.image_label.size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            target_size = QSize(320, 260)
        scaled = pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setText("")
        self._apply_upload_loaded_style()
        self.image_label.setPixmap(scaled)

    def open_results_window(self):
        if self._guard_upload_permission():
            return
        if self._guard_busy_action("proceeding to results"):
            return

        image_path = str(getattr(self, "current_image", "") or "").strip()
        if not image_path or not os.path.isfile(image_path):
            message = "Please upload image or take fundus image on camera."
            self._set_upload_error(message)
            QMessageBox.information(self, "Image Required", message)
            return

        # Doctor queue diagnosis mode: demographics are already gathered in EMR.
        # Skip intake validation + confirm prompts; proceed straight to analysis.
        doctor_mode = bool(getattr(self, "_doctor_queue_mode", False))
        if not doctor_mode:
            if not self._validate_patient_basics():
                return

            ok, message = self._flow_guard.validate()
            if not ok:
                if self._handle_flow_blocked(message):
                    return
                if "upload a fundus image" in message.lower() or "no image" in message.lower():
                    self._set_upload_error("Please select a fundus image to proceed")
                QMessageBox.warning(self, "Incomplete Form", message)
                return
            self._set_upload_error("")

            self._resolve_duplicate_patient()

            confirm_box = QMessageBox(self)
            confirm_box.setWindowTitle("Confirm Details")
            confirm_box.setText("Please confirm all patient information is correct before proceeding to results.")
            proceed_button = confirm_box.addButton("Proceed to Results", QMessageBox.ButtonRole.AcceptRole)
            confirm_box.addButton("Edit Information", QMessageBox.ButtonRole.RejectRole)

            confirm_box.exec()
            if confirm_box.clickedButton() != proceed_button:
                return
        else:
            self._set_upload_error("")

        # Analyze Image confirmation (applies to all flows, including doctor queue mode).
        confirm_box = QMessageBox(self)
        confirm_box.setWindowTitle("Confirm Before Analysis")
        confirm_box.setText("Please confirm that all patient information is correct before proceeding to the results.")
        proceed_button = confirm_box.addButton("Proceed to Results", QMessageBox.ButtonRole.AcceptRole)
        confirm_box.addButton("Review Information", QMessageBox.ButtonRole.RejectRole)
        confirm_box.exec()
        if confirm_box.clickedButton() != proceed_button:
            return

        if not self._ensure_emr_screening_session(self.current_image):
            return

        self._current_eye_saved = False
        eye_label = self.p_eye.currentText()

        # Show the results page immediately with a loading state
        self.results_page.set_results(
            self.p_name.text(),
            self.current_image,
            "Analyzing…",
            "Please wait",
            eye_label=eye_label,
            first_eye_result=self._first_eye_result,
        )
        self.stacked_widget.setCurrentIndex(1)
        self.btn_analyze.setEnabled(False)

        # Run inference on a background thread
        patient_data = self._collect_patient_data()
        self._worker = _InferenceWorker(self.current_image)
        self._worker.result_ready.connect(
            lambda label, conf: self._on_prediction_ready(label, conf, eye_label, patient_data)
        )
        self._worker.finished.connect(
            lambda label, conf, hmap: self._on_inference_done(label, conf, hmap, eye_label, patient_data)
        )
        self._worker.error.connect(self._on_inference_error)
        self._worker.ungradable.connect(self._on_image_ungradable)
        self._set_navigation_locked(True)
        self._worker.start()

    def _resolve_duplicate_patient(self):
        dob_date = self._get_dob_date()
        if not dob_date.isValid():
            return

        match = self._duplicate_detector.find_duplicate(
            name=self.p_name.text().strip(),
            dob=dob_date.toString("yyyy-MM-dd"),
            contact=self.p_contact.text().strip(),
        )
        if not match or not match.get("patient_id"):
            return

        matched_id = str(match["patient_id"]).strip()
        if not matched_id:
            return

        current_id = self.p_id.text().strip()
        if current_id != matched_id:
            decision = DuplicateDialog(match, self).exec()
            if decision == DuplicateDialog.USE_EXISTING:
                self._session_patient_code = matched_id
                self.p_id.setText(matched_id)
                write_activity("INFO", "DUPLICATE_PATIENT", f"Reused existing patient_id={matched_id}")
            else:
                write_activity("INFO", "DUPLICATE_PATIENT", "User kept new patient ID")

    def _on_prediction_ready(self, label: str, conf: str, eye_label: str, patient_data: dict | None = None):
        self.last_result_class = label
        self.last_result_conf = conf
        self.results_page.set_results(
            self.p_name.text(),
            self.current_image,
            label,
            conf,
            eye_label=eye_label,
            first_eye_result=self._first_eye_result,
            patient_data=patient_data,
            heatmap_pending=True,
        )

    def _on_inference_done(self, label: str, conf: str, heatmap_path: str, eye_label: str, patient_data: dict | None = None):
        self._set_navigation_locked(False)
        self.last_result_class = label
        self.last_result_conf = conf
        if eye_label:
            self._flow_guard.mark_eye_done(eye_label)
        write_activity(
            "INFO",
            "SCREENING_RUN",
            f"eye={eye_label}; image={self.current_image}; result={label}; confidence={conf}",
        )
        self.btn_analyze.setEnabled(True)
        self.results_page.set_results(
            self.p_name.text(),
            self.current_image,
            label,
            conf,
            eye_label=eye_label,
            first_eye_result=self._first_eye_result,
            heatmap_path=heatmap_path,
            patient_data=patient_data,
            heatmap_pending=False,
        )

        # Handle follow-up progression summary
        if hasattr(self, "_current_previous_screening_id") and self._current_previous_screening_id:
            try:
                conn = sqlite3.connect(DB_FILE)
                cur = conn.cursor()
                cur.execute("SELECT result FROM patient_records WHERE id = ?", (self._current_previous_screening_id,))
                row = cur.fetchone()
                conn.close()
                if row:
                    prev_result = row[0]
                    self.results_page.set_progression_info(prev_result, label)
            except Exception as e:
                write_activity("WARNING", "PROGRESSION_SUMMARY_FAILED", f"Error fetching previous result: {str(e)}")

    def _on_inference_error(self, message: str):
        self._set_navigation_locked(False)
        self.btn_analyze.setEnabled(True)
        self._reparent_results_into_stack(set_index_0=True)
        write_activity("ERROR", "SCREENING_INFERENCE_FAILED", message)
        QMessageBox.critical(
            self, "Analysis Failed",
            f"Could not run the DR model:\n\n{message}"
        )

    def _on_image_ungradable(self, message: str):
        """Called when the quality check rejects the uploaded image."""
        self._set_navigation_locked(False)
        self.btn_analyze.setEnabled(True)
        self._reparent_results_into_stack(set_index_0=True)
        write_activity("WARNING", "SCREENING_UNGRADABLE", message)
        msg = QMessageBox(self)
        msg.setWindowTitle("Image Not Gradable")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(
            "<b>The uploaded image does not meet the minimum quality "
            "requirements for DR screening.</b>"
        )
        msg.setInformativeText(
            message + "\n\nPlease upload a clearer, well-lit fundus photograph and try again."
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _collect_patient_data(self) -> dict:
        """Snapshot the current intake form into a plain dict for the explanation generator."""
        # Collect symptoms
        symptoms = []
        if self.symptom_blurred.isChecked():
            symptoms.append("Blurred vision")
        if self.symptom_floaters.isChecked():
            symptoms.append("Floaters")
        if self.symptom_flashes.isChecked():
            symptoms.append("Flashes")
        if self.symptom_vision_loss.isChecked():
            symptoms.append("Vision loss")
        symptom_other = self.symptom_other.text().strip()
        if symptom_other:
            symptoms.append(f"Other: {symptom_other}")

        return {
            "age":            self.p_age.value(),
            "hba1c":          0.0,
            "duration":       self.diabetes_duration.value(),
            "prev_treatment": bool(getattr(getattr(self, "prev_treatment", None), "isChecked", lambda: False)()),
            "diabetes_type":  self.diabetes_type.currentText(),
            "eye":            self.p_eye.currentText(),
            # Vital signs
            "va_left":        self.va_left.text().strip(),
            "va_right":       self.va_right.text().strip(),
            "bp_systolic":    self.bp_systolic.value() if self.bp_systolic.value() > 0 else None,
            "bp_diastolic":   self.bp_diastolic.value() if self.bp_diastolic.value() > 0 else None,
            "fbs":            self.fbs.value() if self.fbs.value() > 0 else None,
            "rbs":            self.rbs.value() if self.rbs.value() > 0 else None,
            "symptoms":       symptoms,
            # Phase 1 additions
            "height":         self.height.value() if self.height.value() > 0 else None,
            "weight":         self.weight.value() if self.weight.value() > 0 else None,
            "bmi":            self.bmi.value() if self.bmi.value() > 0 else None,
            "treatment_regimen": self.treatment_regimen.currentText(),
            "prev_dr_stage":  self.prev_dr_stage.currentText(),
        }

    def has_unsaved_result(self) -> bool:
        has_result = self.last_result_class not in ("Pending", "Analyzing…")
        return has_result and not self._current_eye_saved

    def _draft_payload(self) -> dict:
        prev_treatment_checked = bool(getattr(getattr(self, "prev_treatment", None), "isChecked", lambda: False)())
        return {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "patient_id": self.p_id.text().strip(),
            "name": self.p_name.text().strip(),
            "dob": self.p_dob.text().strip(),
            "age": self.p_age.value(),
            "sex": self.p_sex.currentText(),
            "contact": self.p_contact.text().strip(),
            "phone": self.p_phone.text().strip() if hasattr(self, "p_phone") else "",
            "address": self.p_address.text().strip() if hasattr(self, "p_address") else "",
            "eye": self.p_eye.currentText(),
            "diabetes_type": self.diabetes_type.currentText(),
            "diagnosis_date": self._get_diagnosis_date().toString("dd/MM/yyyy") if self._get_diagnosis_date().isValid() else "",
            "duration": self.diabetes_duration.value(),
            "hba1c": 0.0,
            "prev_treatment": prev_treatment_checked,
            "va_left": self.va_left.text().strip() if hasattr(self, "va_left") else "",
            "va_right": self.va_right.text().strip() if hasattr(self, "va_right") else "",
            "bp_systolic": self.bp_systolic.value() if hasattr(self, "bp_systolic") else 0,
            "bp_diastolic": self.bp_diastolic.value() if hasattr(self, "bp_diastolic") else 0,
            "fbs": self.fbs.value() if hasattr(self, "fbs") else 0,
            "rbs": self.rbs.value() if hasattr(self, "rbs") else 0,
            "symptom_blurred": bool(getattr(getattr(self, "symptom_blurred", None), "isChecked", lambda: False)()),
            "symptom_floaters": bool(getattr(getattr(self, "symptom_floaters", None), "isChecked", lambda: False)()),
            "symptom_flashes": bool(getattr(getattr(self, "symptom_flashes", None), "isChecked", lambda: False)()),
            "symptom_vision_loss": bool(getattr(getattr(self, "symptom_vision_loss", None), "isChecked", lambda: False)()),
            "symptom_other": (self.symptom_other.text().strip() if hasattr(self, "symptom_other") else ""),
            "notes": (self.notes.toPlainText() if hasattr(self, "notes") else ""),
            "height": self.height.value() if hasattr(self, "height") else 0,
            "weight": self.weight.value() if hasattr(self, "weight") else 0,
            "bmi": self.bmi.value() if hasattr(self, "bmi") else 0,
            "treatment_regimen": self.treatment_regimen.currentText() if hasattr(self, "treatment_regimen") else "",
            "prev_dr_stage": self.prev_dr_stage.currentText() if hasattr(self, "prev_dr_stage") else "",
            "image_path": str(self.current_image or "").strip(),
            "result_class": self.last_result_class,
            "result_conf": self.last_result_conf,
            "ai_classification": self.last_ai_classification,
            "doctor_classification": self.last_doctor_classification,
            "decision_mode": self.last_decision_mode,
            "override_justification": self.last_override_justification,
            "doctor_findings": self.last_doctor_findings,
            "result_saved": self._current_eye_saved,
        }

    def _has_any_draft_content(self) -> bool:
        payload = self._draft_payload()
        keys = ("name", "dob", "contact", "image_path", "symptom_other", "notes", "result_class")
        if any(str(payload.get(k, "")).strip() for k in keys):
            return True
        return any(bool(payload.get(k)) for k in ("symptom_blurred", "symptom_floaters", "symptom_flashes", "symptom_vision_loss"))

    def _autosave_draft(self):
        if not self._has_any_draft_content() or self._current_eye_saved:
            return
        try:
            self._draft_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._draft_path, "w", encoding="utf-8") as f:
                json.dump(self._draft_payload(), f, indent=2)
            write_activity("INFO", "AUTOSAVE_DRAFT", f"Draft saved to {self._draft_path}")
        except OSError as err:
            write_activity("ERROR", "AUTOSAVE_DRAFT_FAILED", str(err))

    def has_draft_session(self) -> bool:
        return self._draft_path.exists()

    def draft_timestamp(self) -> str:
        if not self._draft_path.exists():
            return ""
        return datetime.fromtimestamp(self._draft_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    def restore_draft_session(self) -> bool:
        if not self._draft_path.exists():
            return False
        try:
            with open(self._draft_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False

        has_unsaved_progress = (not getattr(self, "_current_eye_saved", False)) and self._has_any_draft_content()
        if has_unsaved_progress:
            confirm = QMessageBox.question(
                self,
                "Restore Draft",
                "This will replace the current unsaved form data with the saved draft. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return False

        self.p_id.setText(str(data.get("patient_id") or self.generate_patient_id()))
        self._set_name_parts_from_full_name(str(data.get("name") or ""))
        self.p_dob.setText(str(data.get("dob") or ""))
        self.p_age.setValue(int(data.get("age") or 0))
        self.p_sex.setCurrentText(str(data.get("sex") or ""))
        self.p_contact.setText(str(data.get("contact") or ""))
        if hasattr(self, "p_phone"):
            self.p_phone.setText(str(data.get("phone") or ""))
        if hasattr(self, "p_address"):
            self.p_address.setText(str(data.get("address") or ""))
        self.p_eye.setCurrentText(str(data.get("eye") or ""))
        self.diabetes_type.setCurrentText(str(data.get("diabetes_type") or "Select"))
        if hasattr(self, "diabetes_diagnosis_date"):
            qd = QDate.fromString(str(data.get("diagnosis_date") or ""), "dd/MM/yyyy")
            if qd.isValid():
                self.diabetes_diagnosis_date.setDate(qd)
            else:
                self.diabetes_diagnosis_date.setDate(self.min_diagnosis_date)
        self.diabetes_duration.setValue(int(data.get("duration") or 0))
        # HbA1c removed from UI.
        if hasattr(self, "prev_treatment"):
            self.prev_treatment.setChecked(bool(data.get("prev_treatment")))

        if hasattr(self, "va_left"):
            self.va_left.setText(str(data.get("va_left") or ""))
        if hasattr(self, "va_right"):
            self.va_right.setText(str(data.get("va_right") or ""))
        if hasattr(self, "bp_systolic"):
            self.bp_systolic.setValue(int(data.get("bp_systolic") or 0))
        if hasattr(self, "bp_diastolic"):
            self.bp_diastolic.setValue(int(data.get("bp_diastolic") or 0))
        if hasattr(self, "fbs"):
            self.fbs.setValue(int(data.get("fbs") or 0))
        if hasattr(self, "rbs"):
            self.rbs.setValue(int(data.get("rbs") or 0))

        self.symptom_blurred.setChecked(bool(data.get("symptom_blurred")))
        self.symptom_floaters.setChecked(bool(data.get("symptom_floaters")))
        self.symptom_flashes.setChecked(bool(data.get("symptom_flashes")))
        self.symptom_vision_loss.setChecked(bool(data.get("symptom_vision_loss")))
        self.symptom_other.setText(str(data.get("symptom_other") or ""))
        self.notes.setPlainText(str(data.get("notes") or ""))

        image_path = str(data.get("image_path") or "")
        if image_path and os.path.isfile(image_path):
            self.current_image = image_path
            self._set_preview_image(image_path)
            self.btn_analyze.setEnabled(True)
        else:
            self.clear_image()

        self.last_result_class = str(data.get("result_class") or "Pending")
        self.last_result_conf = str(data.get("result_conf") or "Pending")
        self.last_ai_classification = str(data.get("ai_classification") or self.last_result_class or "Pending")
        self.last_doctor_classification = str(data.get("doctor_classification") or self.last_result_class or "Pending")
        self.last_decision_mode = str(data.get("decision_mode") or "pending")
        self.last_override_justification = str(data.get("override_justification") or "")
        self.last_doctor_findings = str(data.get("doctor_findings") or "")
        if hasattr(self, "results_page"):
            self.results_page._doctor_classification = self.last_doctor_classification
            self.results_page._decision_mode = self.last_decision_mode
            self.results_page._override_justification = self.last_override_justification
            self.results_page._doctor_findings = self.last_doctor_findings
            self.results_page.override_reason_input.setText(self.last_override_justification)
            self.results_page.findings_input.setText(self.last_doctor_findings)
            if self.last_doctor_classification in ("No DR", "Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"):
                if hasattr(self.results_page, "doctor_classification_combo"):
                    self.results_page.doctor_classification_combo.setCurrentText(self.last_doctor_classification)
                elif hasattr(self.results_page, "doctor_classification_input"):
                    self.results_page.doctor_classification_input.setText(self.last_doctor_classification)
            self.results_page._refresh_decision_ui_state()
        self._current_eye_saved = bool(data.get("result_saved"))
        write_activity("INFO", "RESTORE_DRAFT", f"Draft restored from {self._draft_path}")
        return True

    def discard_draft_session(self):
        safe_remove_file(self._draft_path)
        write_activity("INFO", "DISCARD_DRAFT", f"Draft removed at {self._draft_path}")


    def clear_image(self):
        """Reset the image state and clear both the UI and session attributes."""
        self.current_image = None
        self._last_saved_source_path = ""
        
        # Thoroughly reset the image label/dropzone
        if hasattr(self, "image_label"):
            if hasattr(self.image_label, "clear_image"):
                self.image_label.clear_image()
            else:
                self.image_label.setPixmap(QPixmap())
                self._apply_upload_placeholder_style()
        
        # Disable action buttons until a new image is loaded
        if hasattr(self, "btn_analyze"):
            self.btn_analyze.setEnabled(False)
        self._set_upload_error("")
        
        # Synchronize with Results window if present
        if hasattr(self, "results_page"):
            with contextlib.suppress(Exception):
                self.results_page.set_results(
                    self.p_name.text(), "", "Pending", "—",
                    eye_label=self.p_eye.currentText()
                )

    def _persist_screening_assets(self, patient_id: str, eye_label: str) -> tuple[str, str, str, str]:
        """Copy source/heatmap images into app-managed storage and return DB-ready metadata."""
        source_path = str(getattr(self, "current_image", "") or "").strip()
        heatmap_path = str(getattr(self.results_page, "_current_heatmap_path", "") or "").strip()

        if not source_path or not os.path.isfile(source_path):
            raise FileNotFoundError("Source fundus image is missing and cannot be saved.")

        storage_root = self._custom_storage_root.strip() if hasattr(self, "_custom_storage_root") else ""
        if storage_root:
            base_dir = os.path.join(storage_root, patient_id)
        else:
            base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stored_images", patient_id)
        os.makedirs(base_dir, exist_ok=True)

        eye_tag = re.sub(r"[^a-z0-9]+", "_", eye_label.lower()).strip("_") or "eye"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        src_ext = os.path.splitext(source_path)[1].lower() or ".jpg"
        dst_source = os.path.join(base_dir, f"{stamp}_{eye_tag}_source{src_ext}")

        norm_source = source_path.replace("\\", "/").lower()
        pending_marker = "/stored_images/pending/"
        if pending_marker in norm_source:
            shutil.move(source_path, dst_source)
        else:
            shutil.copy2(source_path, dst_source)

        dst_heatmap = ""
        if heatmap_path and os.path.isfile(heatmap_path):
            h_ext = os.path.splitext(heatmap_path)[1].lower() or ".png"
            dst_heatmap = os.path.join(base_dir, f"{stamp}_{eye_tag}_heatmap{h_ext}")
            shutil.copy2(heatmap_path, dst_heatmap)

        hasher = hashlib.sha256()
        with open(dst_source, "rb") as src_file:
            for chunk in iter(lambda: src_file.read(1024 * 1024), b""):
                hasher.update(chunk)

        app_root = os.path.dirname(os.path.abspath(__file__))
        rel_source = os.path.relpath(dst_source, app_root).replace("\\", "/")
        rel_heatmap = os.path.relpath(dst_heatmap, app_root).replace("\\", "/") if dst_heatmap else ""
        image_sha256 = hasher.hexdigest()
        image_saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return rel_source, rel_heatmap, image_sha256, image_saved_at

    def _find_existing_eye_record(self, patient_id: str, eye_label: str):
        patient_id = str(patient_id or "").strip()
        eye_label = str(eye_label or "").strip()
        if not patient_id or not eye_label:
            return None

        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT id, screened_at, screening_group_id
                    FROM patient_records
                    WHERE patient_id = ? AND lower(eyes) = lower(?) AND archived_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (patient_id, eye_label),
                )
            except sqlite3.Error:
                cur.execute(
                    """
                    SELECT id, screened_at, screening_group_id
                    FROM patient_records
                    WHERE patient_id = ? AND lower(eyes) = lower(?)
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (patient_id, eye_label),
                )
            row = cur.fetchone()
            conn.close()
        except Exception:
            return None

        if not row:
            return None
        return {
            "id": int(row[0]),
            "screened_at": str(row[1] or "").strip(),
            "screening_group_id": str(row[2] or "").strip(),
        }

    def _start_new_screening_session_for_current_patient(self) -> None:
        """Keep the same patient ID but force a fresh screening session/save."""
        self._rescreen_replace_record_id = None
        self._current_previous_screening_id = None
        self._current_screening_type = "initial"
        self._current_follow_up_flag = ""
        self._current_followup_date = ""
        self._current_followup_label = ""
        self._current_screening_group_id = ""
        self._first_eye_result = None

    def _prompt_duplicate_eye_action(self, patient_id: str, eye_label: str) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("Existing Eye Record")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"A saved <b>{eye_label}</b> record already exists for patient ID <b>{patient_id}</b>."
        )
        box.setInformativeText(
            "Choose Replace to overwrite that eye result, or Save as New Session to keep this patient ID and create a fresh screening record."
        )
        replace_btn = box.addButton("Replace Existing", QMessageBox.ButtonRole.AcceptRole)
        new_session_btn = box.addButton("Save as New Session", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked == replace_btn:
            return "replace"
        if clicked == new_session_btn:
            return "new_session"
        return "cancel"

    def _update_screening_record(self, record_id: int, patient_data, screener_username: str, screener_name: str) -> tuple[bool, str, int | None]:
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE patient_records SET
                    patient_id = ?, name = ?, birthdate = ?, age = ?, sex = ?, contact = ?, phone = ?, address = ?, eyes = ?,
                    diabetes_type = ?, duration = ?, hba1c = ?, prev_treatment = ?, notes = ?,
                    result = ?, confidence = ?, screened_at = ?,
                    ai_classification = ?, doctor_classification = ?, decision_mode = ?, override_justification = ?,
                    final_diagnosis_icdr = ?, doctor_findings = ?, decision_by_username = ?, decision_at = ?,
                    visual_acuity_left = ?, visual_acuity_right = ?,
                    blood_pressure_systolic = ?, blood_pressure_diastolic = ?,
                    fasting_blood_sugar = ?, random_blood_sugar = ?,
                    diabetes_diagnosis_date = ?,
                    symptom_blurred_vision = ?, symptom_floaters = ?,
                    symptom_flashes = ?, symptom_vision_loss = ?,
                    source_image_path = ?, heatmap_image_path = ?,
                    image_sha256 = ?, image_saved_at = ?,
                    height = ?, weight = ?, bmi = ?, treatment_regimen = ?, prev_dr_stage = ?,
                    follow_up = ?, followup_date = ?, followup_label = ?, screening_type = ?, previous_screening_id = ?,
                    screening_group_id = ?,
                    original_screener_username = COALESCE(NULLIF(original_screener_username, ''), ?),
                    original_screener_name = COALESCE(NULLIF(original_screener_name, ''), ?)
                WHERE id = ?
                """,
                [*patient_data, screener_username, screener_name, int(record_id)],
            )
            conn.commit()
            updated = cur.rowcount > 0
            conn.close()
            if not updated:
                return False, "No matching record was updated.", None
            return True, "", int(record_id)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            write_activity("ERROR", "SAVE_UPDATE_FAILED", err)
            return False, err, None

    def save_screening(self, reset_after=True):
        if self._guard_busy_action("saving the result"):
            return {"status": "blocked", "error": "Please wait for image analysis to finish before saving."}

        if not self._validate_patient_basics():
            return {"status": "invalid"}

        # Validate new fields
        if not self._validate_blood_pressure():
            return {"status": "invalid"}
        if not self._validate_blood_glucose():
            return {"status": "invalid"}

        # ------------------------------------------------------------------
        # EMR-backed clinician flow (Doctor Queue Mode)
        # ------------------------------------------------------------------
        # When this ScreeningPage is launched from the EMR queue, the screening
        # session and visit already exist in EMR tables. Saving must persist:
        # - per-visit details into emr_visit_details (queue-scoped)
        # - clinician decision into emr_screening_eyes/emr_screenings
        # - mark the visit completed when allowed
        # and must NOT touch legacy patient_records for clinical correctness.
        # Doctor Queue Mode: ensure EMR screening session exists before saving so we
        # never fall back to legacy patient_records inserts (which create duplicate cards).
        if bool(getattr(self, "_doctor_queue_mode", False)) and (
            getattr(self, "_emr_queue_entry_id", None) and getattr(self, "_emr_patient_pk", None)
        ):
            if not getattr(self, "_emr_screening_id", None):
                if not self._ensure_emr_screening_session(str(getattr(self, "current_image", "") or "")):
                    return {"status": "error", "error": "Could not start EMR screening session for this visit."}
            username = str(getattr(self, "username", "") or os.environ.get("EYESHIELD_CURRENT_USER", "")).strip()
            uid = emr.get_user_id(username)
            if not uid:
                return {"status": "error", "error": "Could not resolve current clinician account."}

            qid = int(getattr(self, "_emr_queue_entry_id", 0) or 0)
            pid_pk = int(getattr(self, "_emr_patient_pk", 0) or 0)
            sid = int(getattr(self, "_emr_screening_id", 0) or 0)
            if not (qid and pid_pk and sid):
                return {"status": "error", "error": "Missing EMR context (patient/visit/screening)."}

            decision_payload = self.results_page.get_decision_payload() if hasattr(self, "results_page") else {}
            decision_ok, decision_msg = self.results_page.validate_decision_before_save() if hasattr(self, "results_page") else (True, "")
            if not decision_ok:
                QMessageBox.warning(self, "Clinical Decision Required", decision_msg)
                return {"status": "invalid"}

            ai_classification = str(decision_payload.get("ai_classification") or self.last_result_class or "").strip()
            doctor_classification = str(decision_payload.get("doctor_classification") or self.last_result_class or "").strip()
            decision_mode = str(decision_payload.get("decision_mode") or "accepted").strip()
            override_justification = str(decision_payload.get("override_justification") or "").strip()
            final_diagnosis_icdr = str(decision_payload.get("final_diagnosis_icdr") or doctor_classification or "").strip()
            doctor_findings = str(decision_payload.get("doctor_findings") or "").strip()
            decision_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            label_to_grade = {
                "No DR": 0,
                "Mild DR": 1,
                "Moderate DR": 2,
                "Severe DR": 3,
                "Proliferative DR": 4,
            }
            def _norm(v: str) -> str:
                t = str(v or "").strip()
                low = t.lower()
                if "proliferative" in low or low in {"pdr"}:
                    return "Proliferative DR"
                if "severe" in low:
                    return "Severe DR"
                if "moderate" in low:
                    return "Moderate DR"
                if "mild" in low:
                    return "Mild DR"
                if "no dr" in low or low in {"normal", "none"}:
                    return "No DR"
                return t

            final_diagnosis_icdr = _norm(final_diagnosis_icdr)
            doctor_classification = _norm(doctor_classification)
            final_grade = label_to_grade.get(final_diagnosis_icdr)
            if final_grade is None:
                final_grade = label_to_grade.get(doctor_classification)
            if final_grade is None:
                QMessageBox.warning(self, "Save Failed", "Please select a valid final DR classification before saving.")
                return {"status": "invalid"}

            # Visit details (queue-scoped)
            visit_details = {
                "visual_acuity_left": self.va_left.text().strip() if hasattr(self, "va_left") else "",
                "visual_acuity_right": self.va_right.text().strip() if hasattr(self, "va_right") else "",
                "blood_pressure_systolic": int(self.bp_systolic.value()) if hasattr(self, "bp_systolic") and self.bp_systolic.value() > 0 else None,
                "blood_pressure_diastolic": int(self.bp_diastolic.value()) if hasattr(self, "bp_diastolic") and self.bp_diastolic.value() > 0 else None,
                "fasting_blood_sugar": float(self.fbs.value()) if hasattr(self, "fbs") and self.fbs.value() > 0 else None,
                "random_blood_sugar": float(self.rbs.value()) if hasattr(self, "rbs") and self.rbs.value() > 0 else None,
                "diabetes_type": (self.diabetes_type.currentText().strip() if hasattr(self, "diabetes_type") else "") or None,
                "dm_duration_years": float(self.diabetes_duration.value()) if hasattr(self, "diabetes_duration") and self.diabetes_duration.value() > 0 else None,
                "hba1c": None,
            "diabetes_diagnosis_date": self._get_diagnosis_date().toString("dd/MM/yyyy") if self._get_diagnosis_date().isValid() else None,
                "treatment_regimen": (self.treatment_regimen.currentText().strip() if hasattr(self, "treatment_regimen") else "") or None,
            "prev_dr_stage": (self.clinical_prev_dr_stage.currentText().strip() if hasattr(self, "clinical_prev_dr_stage") else "") or None,

                "symptom_blurred_vision": 1 if getattr(self, "symptom_blurred", None) and self.symptom_blurred.isChecked() else 0,
                "symptom_floaters": 1 if getattr(self, "symptom_floaters", None) and self.symptom_floaters.isChecked() else 0,
                "symptom_flashes": 1 if getattr(self, "symptom_flashes", None) and self.symptom_flashes.isChecked() else 0,
                "symptom_vision_loss": 1 if getattr(self, "symptom_vision_loss", None) and self.symptom_vision_loss.isChecked() else 0,
                "symptom_other": (self.symptom_other.text().strip() if hasattr(self, "symptom_other") else "") or None,
                "height_cm": float(self.height.value()) if hasattr(self, "height") and self.height.value() > 0 else None,
                "weight_kg": float(self.weight.value()) if hasattr(self, "weight") and self.weight.value() > 0 else None,
                "notes": self.notes.toPlainText().strip() if hasattr(self, "notes") else "",
            }
            emr.upsert_visit_details(queue_id=qid, patient_id=pid_pk, captured_by=int(uid), details=visit_details)

            # Determine which EMR eye row to update based on current selection.
            eye_text = str(self.p_eye.currentText() or "").strip().lower()
            eye_side = "Left" if "left" in eye_text else "Right" if "right" in eye_text else ""
            if not eye_side:
                QMessageBox.warning(self, "Save Failed", "Please select an eye before saving.")
                return {"status": "invalid"}

            sc = emr.get_screening(int(sid)) or {}
            def _match_eye(row: dict, side: str) -> bool:
                raw = str((row or {}).get("eye_side") or "").strip().lower()
                if not raw:
                    return False
                if raw == side.lower():
                    return True
                # Defensive: allow legacy labels if any slipped in.
                return (side.lower() in raw) or (raw in {"od", "right"} and side == "Right") or (raw in {"os", "left"} and side == "Left")

            eyes = sc.get("eyes") or []
            eye_row = next((e for e in eyes if _match_eye(e, eye_side)), None)
            if not eye_row:
                # Recovery: some sessions may have emr_screenings without emr_screening_eyes rows.
                # Ensure the missing eye row exists using the current fundus image as source.
                with contextlib.suppress(Exception):
                    emr.ensure_screening_eye_row(
                        screening_id=int(sid),
                        eye_side=str(eye_side),
                        fundus_source_path=str(self.current_image or ""),
                        performed_by=int(uid),
                    )
                sc = emr.get_screening(int(sid)) or {}
                eyes = sc.get("eyes") or []
                eye_row = next((e for e in eyes if _match_eye(e, eye_side)), None)
            if not eye_row:
                return {"status": "error", "error": "Could not resolve EMR eye record for this screening."}

            # Attach local Grad-CAM (if available) so Patient Overview can render images.
            heatmap_src = ""
            if hasattr(self, "results_page"):
                heatmap_src = str(getattr(self.results_page, "_current_heatmap_path", "") or "").strip()
            with contextlib.suppress(Exception):
                emr.attach_gradcam_to_eye(
                    eye_id=int(eye_row.get("eye_id")),
                    screening_id=int(sid),
                    eye_side=str(eye_side),
                    gradcam_source_path=heatmap_src,
                    performed_by=int(uid),
                )

            accepted = 1
            if decision_mode.lower() != "accepted" or (doctor_classification and ai_classification and doctor_classification != ai_classification):
                accepted = 0

            eye_updates = [
                {
                    "eye_id": int(eye_row.get("eye_id")),
                    "doctor_accepted_ai": accepted,
                    "final_dr_grade": int(final_grade),
                    "override_justification": override_justification,
                    "final_treatment_notes": doctor_findings,
                }
            ]
            ok = emr.verify_screening(int(sid), int(uid), eye_updates)
            emr.update_screening_doctor_notes(int(sid), int(uid), doctor_findings)
            if not ok:
                return {"status": "error", "error": "Could not save clinician decision to EMR."}

            # Keep legacy Patient Records (patient_records.db) in sync so Patient Overview / Reports
            # can immediately render the saved final classification + notes + images for this visit.
            try:
                fundus_path = str(eye_row.get("fundus_image_path") or "")
            except Exception:
                fundus_path = ""
            heatmap_path = ""
            if hasattr(self, "results_page"):
                heatmap_path = str(getattr(self.results_page, "_current_heatmap_path", "") or "").strip()
            with contextlib.suppress(Exception):
                emr.upsert_legacy_patient_record_for_queue_eye(
                    queue_id=int(qid),
                    patient_id=int(pid_pk),
                    captured_by=int(uid),
                    eye_label=("Right Eye" if eye_side == "Right" else "Left Eye"),
                    screening_type=str(sc.get("screening_type") or "initial"),
                    screened_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    source_image_path=fundus_path,
                    heatmap_image_path=heatmap_path,
                    ai_classification=ai_classification,
                    doctor_classification=doctor_classification,
                    decision_mode=decision_mode,
                    override_justification=override_justification,
                    final_diagnosis_icdr=final_diagnosis_icdr,
                    doctor_findings=doctor_findings,
                    confidence=str(self.last_result_conf or ""),
                )

            # Mark the visit completed when allowed.
            can_done, reason = emr.can_complete_visit(int(qid))
            if can_done:
                emr.set_queue_status(int(qid), "completed", int(uid))
            else:
                # Non-fatal; decision saved, visit just remains open if pipeline not ready.
                write_activity("WARNING", "VISIT_COMPLETE_BLOCKED", reason)

            # Keep UI behaviour consistent with legacy path.
            self._current_eye_saved = True
            if hasattr(self, "results_page"):
                self.results_page.mark_saved(self.p_name.text().strip(), self.p_eye.currentText() or "eye", self.last_result_class)
            return {"status": "saved", "path": "", "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        name = self.p_name.text().strip()

        pid = str(getattr(self, "_session_patient_code", "") or "").strip() or self.p_id.text().strip()
        if not pid:
            pid = self.generate_patient_id()
        else:
            # Keep UI aligned with the session ID to prevent "Assessment vs Records" mismatch.
            self._session_patient_code = pid
            with contextlib.suppress(Exception):
                self.p_id.setText(pid)

        dob_date = self._get_dob_date()
        dob_str = dob_date.toString("yyyy-MM-dd") if dob_date.isValid() else ""

        diag_date = self._get_diagnosis_date()
        diag_date_str = diag_date.toString("yyyy-MM-dd") if diag_date.isValid() else ""

        age = self.p_age.value()
        sex = self.p_sex.currentText()
        contact = self.p_contact.text().strip()
        phone = self.p_phone.text().strip() if hasattr(self, "p_phone") else ""
        address = self.p_address.text().strip() if hasattr(self, "p_address") else ""
        eye = self.p_eye.currentText()
        diabetes_type = self.diabetes_type.currentText()
        duration = self.diabetes_duration.value()
        hba1c = ""
        prev_treatment = "Yes" if bool(getattr(getattr(self, "prev_treatment", None), "isChecked", lambda: False)()) else "No"
        notes = self.notes.toPlainText().strip()
        result = self.last_result_class
        confidence = self.last_result_conf
        decision_payload = self.results_page.get_decision_payload() if hasattr(self, "results_page") else {}
        decision_ok, decision_msg = self.results_page.validate_decision_before_save() if hasattr(self, "results_page") else (True, "")
        if not decision_ok:
            QMessageBox.warning(self, "Clinical Decision Required", decision_msg)
            return {"status": "invalid"}
        ai_classification = str(decision_payload.get("ai_classification") or result).strip()
        doctor_classification = str(decision_payload.get("doctor_classification") or result).strip()
        decision_mode = str(decision_payload.get("decision_mode") or "accepted").strip()
        override_justification = str(decision_payload.get("override_justification") or "").strip()
        final_diagnosis_icdr = str(decision_payload.get("final_diagnosis_icdr") or doctor_classification or result).strip()
        doctor_findings = str(decision_payload.get("doctor_findings") or "").strip()
        decision_by_username = str(os.environ.get("EYESHIELD_CURRENT_USER", "")).strip()
        decision_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_ai_classification = ai_classification
        self.last_doctor_classification = doctor_classification
        self.last_decision_mode = decision_mode
        self.last_override_justification = override_justification
        self.last_doctor_findings = doctor_findings
        screening_type = str(self._current_screening_type or "").strip() or "initial"
        previous_screening_id = self._current_previous_screening_id
        follow_up_flag = str(self._current_follow_up_flag or "").strip()
        followup_date = str(self._current_followup_date or "").strip()
        followup_label = str(self._current_followup_label or "").strip()
        if screening_type == "follow_up":
            follow_up_flag = follow_up_flag or "Yes"
            followup_date = followup_date or decision_at
            followup_label = followup_label or "Follow-up screening"
        else:
            follow_up_flag = ""
            followup_date = ""
            followup_label = ""
            previous_screening_id = None

        # New fields
        va_left = self.va_left.text().strip()
        va_right = self.va_right.text().strip()
        bp_sys = str(self.bp_systolic.value()) if self.bp_systolic.value() > 0 else ""
        bp_dia = str(self.bp_diastolic.value()) if self.bp_diastolic.value() > 0 else ""
        fbs_val = str(self.fbs.value()) if self.fbs.value() > 0 else ""
        rbs_val = str(self.rbs.value()) if self.rbs.value() > 0 else ""

        # Phase 1 additions
        height_val = str(self.height.value()) if self.height.value() > 0 else ""
        weight_val = str(self.weight.value()) if self.weight.value() > 0 else ""
        bmi_val = str(self.bmi.value()) if self.bmi.value() > 0 else ""
        treatment_regimen = self.treatment_regimen.currentText() if self.treatment_regimen.currentText() != "Select" else ""
        prev_dr_stage = self.prev_dr_stage.currentText() if self.prev_dr_stage.currentText() != "Select" else ""

        # Symptoms as Yes/No flags
        symptom_blurred_flag = "Yes" if self.symptom_blurred.isChecked() else "No"
        symptom_floaters_flag = "Yes" if self.symptom_floaters.isChecked() else "No"
        symptom_flashes_flag = "Yes" if self.symptom_flashes.isChecked() else "No"
        symptom_vision_loss_flag = "Yes" if self.symptom_vision_loss.isChecked() else "No"
        symptom_other = self.symptom_other.text().strip()
        if symptom_other:
            notes = (notes + f"\nOther symptom: {symptom_other}").strip() if notes else f"Other symptom: {symptom_other}"

        initial_signature_payload = {
            "pid": pid,
            "name": name,
            "dob": dob_str,
            "age": age,
            "sex": sex,
            "contact": contact,
            "phone": phone,
            "address": address,
            "eye": eye,
            "diabetes_type": diabetes_type,
            "diag_date": diag_date_str,
            "duration": duration,
            "hba1c": hba1c,
            "prev_treatment": prev_treatment,
            "notes": notes,
            "result": result,
            "confidence": confidence,
            "ai_classification": ai_classification,
            "doctor_classification": doctor_classification,
            "decision_mode": decision_mode,
            "override_justification": override_justification,
            "final_diagnosis_icdr": final_diagnosis_icdr,
            "doctor_findings": doctor_findings,
            "va_left": va_left,
            "va_right": va_right,
            "bp_sys": bp_sys,
            "bp_dia": bp_dia,
            "fbs": fbs_val,
            "rbs": rbs_val,
            "symptom_blurred": symptom_blurred_flag,
            "symptom_floaters": symptom_floaters_flag,
            "symptom_flashes": symptom_flashes_flag,
            "symptom_vision_loss": symptom_vision_loss_flag,
            "image": str(self.current_image or ""),
            "heatmap": str(getattr(self.results_page, "_current_heatmap_path", "") or ""),
            "height": height_val,
            "weight": weight_val,
            "bmi": bmi_val,
            "treatment_regimen": treatment_regimen,
            "prev_dr_stage": prev_dr_stage,
            "screening_type": screening_type,
            "previous_screening_id": previous_screening_id,
        }
        initial_signature = hashlib.sha256(
            json.dumps(initial_signature_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if self._current_eye_saved and initial_signature == self._last_saved_signature:
            return {"status": "unchanged"}

        replace_record_id = None

        # Check if this is a rescreening request (from Reports page)
        if self._rescreen_replace_record_id is not None:
            replace_record_id = self._rescreen_replace_record_id
            self._rescreen_replace_record_id = None  # Clear flag after use
        elif screening_type == "follow_up" and previous_screening_id:
            replace_record_id = None
        else:
            # Standard duplicate detection
            existing_eye_record = self._find_existing_eye_record(pid, eye)
            if existing_eye_record:
                # Saving from Screening Results should never warn/prompt to replace an existing
                # record. Always save as a NEW session so prior visits remain immutable.
                self._start_new_screening_session_for_current_patient()

        pre_signature_payload = {
            "pid": pid,
            "name": name,
            "dob": dob_str,
            "age": age,
            "sex": sex,
            "contact": contact,
            "phone": phone,
            "address": address,
            "eye": eye,
            "diabetes_type": diabetes_type,
            "diag_date": diag_date_str,
            "duration": duration,
            "hba1c": hba1c,
            "prev_treatment": prev_treatment,
            "notes": notes,
            "result": result,
            "confidence": confidence,
            "ai_classification": ai_classification,
            "doctor_classification": doctor_classification,
            "decision_mode": decision_mode,
            "override_justification": override_justification,
            "final_diagnosis_icdr": final_diagnosis_icdr,
            "doctor_findings": doctor_findings,
            "va_left": va_left,
            "va_right": va_right,
            "bp_sys": bp_sys,
            "bp_dia": bp_dia,
            "fbs": fbs_val,
            "rbs": rbs_val,
            "symptom_blurred": symptom_blurred_flag,
            "symptom_floaters": symptom_floaters_flag,
            "symptom_flashes": symptom_flashes_flag,
            "symptom_vision_loss": symptom_vision_loss_flag,
            "image": str(self.current_image or ""),
            "heatmap": str(getattr(self.results_page, "_current_heatmap_path", "") or ""),
            "height": height_val,
            "weight": weight_val,
            "bmi": bmi_val,
            "treatment_regimen": treatment_regimen,
            "prev_dr_stage": prev_dr_stage,
            "screening_type": screening_type,
            "previous_screening_id": previous_screening_id,
        }
        pre_signature = hashlib.sha256(
            json.dumps(pre_signature_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()

        try:
            source_image_path, heatmap_image_path, image_sha256, image_saved_at = self._persist_screening_assets(
                pid,
                eye,
            )
        except (OSError, FileNotFoundError) as exc:
            QMessageBox.warning(self, "Save Failed", f"Unable to store screening images:\n\n{exc}")
            write_activity("ERROR", "SAVE_FAILED", str(exc))
            return {"status": "error", "error": str(exc)}

        # Canonicalize to the persisted image path so subsequent saves don't point
        # at stale temporary camera paths under stored_images/pending.
        app_root = os.path.dirname(os.path.abspath(__file__))
        persisted_source_abs = os.path.join(app_root, source_image_path)
        self.current_image = persisted_source_abs
        if hasattr(self, "results_page"):
            self.results_page._current_image_path = persisted_source_abs

        pre_signature_payload["image"] = str(self.current_image or "")
        pre_signature = hashlib.sha256(
            json.dumps(pre_signature_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()

        screened_at = image_saved_at
        screening_group_id = self._ensure_screening_group_id(pid)
        screener_username = str(os.environ.get("EYESHIELD_CURRENT_USER", "")).strip()
        screener_name = str(os.environ.get("EYESHIELD_CURRENT_NAME", "")).strip() or screener_username

        patient_data = [
            pid,
            name,
            dob_str,
            age if age > 0 else "",
            sex,
            contact,
            phone,
            address,
            eye,
            diabetes_type if diabetes_type != "Select" else "",
            duration,
            hba1c,
            prev_treatment,
            notes,
            result,
            confidence,
            screened_at,
            ai_classification,
            doctor_classification,
            decision_mode,
            override_justification,
            final_diagnosis_icdr,
            doctor_findings,
            decision_by_username,
            decision_at,
            # New fields (11 columns)
            va_left,
            va_right,
            bp_sys,
            bp_dia,
            fbs_val,
            rbs_val,
            diag_date_str,
            symptom_blurred_flag,
            symptom_floaters_flag,
            symptom_flashes_flag,
            symptom_vision_loss_flag,
            source_image_path,
            heatmap_image_path,
            image_sha256,
            image_saved_at,
            # Phase 1 additions
            height_val,
            weight_val,
            bmi_val,
            treatment_regimen,
            prev_dr_stage,
            follow_up_flag,
            followup_date,
            followup_label,
            screening_type,
            previous_screening_id if previous_screening_id is not None else "",
            screening_group_id,
        ]

        save_ok, save_error, saved_record_id = (
            self._update_screening_record(replace_record_id, patient_data, screener_username, screener_name)
            if replace_record_id is not None
            else self._save_screening_to_db(patient_data, screener_username, screener_name)
        )
        if not save_ok:
            action_label = "update" if replace_record_id is not None else "save"
            detail = str(save_error or f"Database {action_label} failed")
            QMessageBox.warning(
                self,
                "Save Failed",
                f"Unable to {action_label} screening record.\n\n{detail}",
            )
            write_activity("ERROR", "SAVE_FAILED", f"Database {action_label} failed: {detail}")
            return {"status": "error", "error": detail}

        self._last_saved_record_id = saved_record_id
        self._current_eye_saved = True
        self._last_saved_signature = pre_signature
        self._last_saved_at = image_saved_at
        self._last_saved_source_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), source_image_path)
        self.discard_draft_session()
        write_activity(
            "INFO",
            "RESULT_REPLACED" if replace_record_id is not None else "RESULT_SAVED",
            f"patient_id={pid}; eye={eye}; path={self._last_saved_source_path}; result={result}; confidence={confidence}",
        )
        if screener_username:
            UserManager.add_activity_log(
                screener_username,
                (
                    f"SCREENED_PATIENT patient_id={pid}; eye={eye}; "
                    f"result={result}; confidence={confidence}; "
                    f"mode={'replace' if replace_record_id is not None else 'new'}"
                ),
            )
        if reset_after:
            saved_name = name or "Unknown Patient"
            saved_eye = eye or "Selected Eye"
            QMessageBox.information(
                self,
                "Saved to Records",
                (
                    "Patient screening updated in records.\n\n"
                    if replace_record_id is not None else
                    "Patient screening saved to records.\n\n"
                ) + f"Patient ID: {pid}\nName: {saved_name}\nEye: {saved_eye}",
            )
            self.reset_screening()
            return {
                "status": "replaced" if replace_record_id is not None else "saved",
                "path": self._last_saved_source_path,
                "saved_at": self._last_saved_at,
            }
        else:
            eye_label = eye or "eye"
            next_action = ""

            # Store per-eye result payloads for bilateral reports.
            # IMPORTANT: do not overwrite the first-eye payload when saving the second eye.
            payload = {
                "eye": eye_label,
                "result": self.last_result_class,
                "confidence": self.last_result_conf,
                "image_path": getattr(self, "current_image", "") or "",
                "heatmap_path": getattr(self.results_page, "_current_heatmap_path", "") or "",
            }
            if bool(getattr(self, "_is_second_eye_flow", False)):
                self._second_eye_result = payload
            else:
                self._first_eye_result = payload
            if hasattr(self, "results_page"):
                self.results_page.mark_saved(self.p_name.text().strip(), eye_label, self.last_result_class)

            # Important: post-save workflow decisions (finish vs other eye) are handled
            # by the Results window, not here, to keep saving deterministic.

            if bool(getattr(self, "_doctor_queue_mode", False)):
                self._reparent_results_into_stack(set_index_0=True)
                next_action = "screening_history"

            return {
                "status": "replaced" if replace_record_id is not None else "saved",
                "path": self._last_saved_source_path,
                "saved_at": self._last_saved_at,
                "next_action": next_action,
            }

    def screen_other_eye(self):
        """Save the current eye's result and switch to the same patient's other eye."""
        if self._guard_busy_action("switching eyes"):
            return

        self._reparent_results_into_stack(set_index_0=True)

        patient_id = self.p_id.text().strip()
        left_record = self._find_existing_eye_record(patient_id, "Left Eye")
        right_record = self._find_existing_eye_record(patient_id, "Right Eye")

        # Per updated workflow: "Screen Other Eye" is a UI/workflow action only.
        # Do not prompt to replace previously-saved eye records here.
        if self._current_eye_saved and left_record and right_record:
            current_eye = self.p_eye.currentText().strip()
            opposite_eye = "Left Eye" if current_eye == "Right Eye" else "Right Eye"
            self._set_eye_selection(opposite_eye)
            self.stacked_widget.setCurrentIndex(0)
            return

        current_eye = self.p_eye.currentText().strip()
        opposite_eye = "Left Eye" if current_eye == "Right Eye" else "Right Eye"

        if not self._current_eye_saved:
            eye_label = current_eye or "current eye"
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved Result")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f"Save this <b>{eye_label}</b> result before screening the <b>{opposite_eye}</b>?")
            save_btn = box.addButton("Save and Continue", QMessageBox.ButtonRole.AcceptRole)
            skip_btn = box.addButton("Skip and Continue", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel_btn)
            box.exec()
            chosen = box.clickedButton()
            if chosen == cancel_btn:
                return
            if chosen == save_btn:
                save_result = self.save_screening(reset_after=False)
                if not isinstance(save_result, dict) or save_result.get("status") not in {"saved", "replaced"}:
                    return  # save failed, abort
            if chosen == skip_btn:
                write_activity("WARNING", "SCREEN_OTHER_EYE_SKIP_SAVE", f"Skipped save for {eye_label}")

        # UX fix:
        # Do NOT hard-reset the entire form (it causes the "messy" feeling: IDs regenerating,
        # fields flickering, and state being restored manually). We only need to switch the
        # eye target and clear the image/result state for the next capture.

        # Mark that we are now screening the other (second) eye in this session.
        self._is_second_eye_flow = True

        # Switch eye.
        self._set_eye_selection(opposite_eye)
        if hasattr(self, "p_eye"):
            self.p_eye.setEnabled(False)

        # Clear per-eye state aggressively.
        self._current_eye_saved = False
        self.last_result_class = "—"
        self.last_result_conf = "—"
        self.last_ai_classification = "Pending"
        self.last_doctor_classification = "Pending"
        self.last_decision_mode = "pending"
        self.last_override_justification = ""
        self.last_doctor_findings = ""

        # Clear Results page state to prevent flickering of old results
        if hasattr(self, "results_page"):
            with contextlib.suppress(Exception):
                # Using public set_results with empty data is safest
                self.results_page.set_results(
                    self.p_name.text(), "", "Pending", "—",
                    eye_label=opposite_eye,
                    heatmap_path=""
                )
                self.results_page._doctor_classification = "Pending"
                self.results_page._decision_mode = "pending"
                self.results_page._override_justification = ""
                self.results_page._doctor_findings = ""
                self.results_page.override_reason_input.clear()
                self.results_page.findings_input.clear()
                self.results_page._refresh_decision_ui_state()

        # Clear the image and require a new upload before analysis.
        self.clear_image()
        if hasattr(self, "btn_analyze"):
            self.btn_analyze.setEnabled(False)
        if hasattr(self, "btn_save"):
            self.btn_save.setEnabled(False)

        # Force UI update for cleared area
        QApplication.processEvents()

        # Reset result UI controls if present.
        with contextlib.suppress(Exception):
            self.r_class.setText("—")
        with contextlib.suppress(Exception):
            self.r_conf.setText("—")

        # Return to the intake form (image upload step).
        self.stacked_widget.setCurrentIndex(0)

    def _save_screening_to_db(self, patient_data, screener_username: str, screener_name: str) -> tuple[bool, str, int | None]:
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO patient_records (
                    patient_id, name, birthdate, age, sex, contact, phone, address, eyes,
                    diabetes_type, duration, hba1c, prev_treatment, notes,
                    result, confidence, screened_at,
                    ai_classification, doctor_classification, decision_mode, override_justification,
                    final_diagnosis_icdr, doctor_findings, decision_by_username, decision_at,
                    visual_acuity_left, visual_acuity_right,
                    blood_pressure_systolic, blood_pressure_diastolic,
                    fasting_blood_sugar, random_blood_sugar,
                    diabetes_diagnosis_date,
                    symptom_blurred_vision, symptom_floaters,
                    symptom_flashes, symptom_vision_loss,
                    source_image_path, heatmap_image_path,
                    image_sha256, image_saved_at,
                    height, weight, bmi, treatment_regimen, prev_dr_stage,
                    follow_up, followup_date, followup_label, screening_type, previous_screening_id,
                    screening_group_id,
                    original_screener_username, original_screener_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [*patient_data, screener_username, screener_name],
            )
            new_id = cur.lastrowid
            conn.commit()
            conn.close()
            return True, "", new_id
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            write_activity("ERROR", "SAVE_INSERT_FAILED", err)
            return False, err, None

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_calendar_themes()

    def apply_theme(self, _theme: str):
        self._apply_calendar_themes()

    def apply_language(self, language: str):
        from translations import get_pack
        pack = get_pack(language)

        # Legacy multi-page layout support
        if hasattr(self, "_scr_patient_group"):
            self._scr_patient_group.setTitle(pack["scr_patient_info"])
        if hasattr(self, "_scr_clinical_group"):
            self._scr_clinical_group.setTitle(pack["scr_clinical_history"])
        if hasattr(self, "_scr_image_group"):
            self._scr_image_group.setTitle(pack["scr_image_upload"])

        # Unified layout support
        if hasattr(self, "_scr_unified_labels") and isinstance(self._scr_unified_labels, dict):
            keys = [
                "scr_patient_info", "scr_clinical_history", "scr_image_upload",
                "scr_label_pid", "scr_label_name", "scr_label_dob", "scr_label_age",
                "scr_label_sex", "scr_label_contact", "scr_label_eye",
                "scr_label_diabetes", "scr_label_duration", "scr_label_hba1c", "scr_label_notes",
            ]
            for key in keys:
                label = self._scr_unified_labels.get(key)
                if label is not None and key in pack:
                    label.setText(pack[key])

        self.btn_upload.setText(pack["scr_upload_btn"])
        # Camera capture has been removed from this build.
        self.btn_clear.setText(pack["scr_clear_btn"])
        self.btn_analyze.setText(pack["scr_analyze_btn"])
        patient_labels = [
            pack["scr_label_pid"], pack["scr_label_name"], pack["scr_label_dob"],
            pack["scr_label_age"], pack["scr_label_sex"], pack["scr_label_contact"],
            pack["scr_label_eye"],
        ]
        if hasattr(self, "_scr_patient_form"):
            for row, text in enumerate(patient_labels):
                item = self._scr_patient_form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item and item.widget():
                    item.widget().setText(text)
                    item.widget().setStyleSheet(self._form_label_stylesheet())
        clinical_labels = [
            pack["scr_label_diabetes"], pack["scr_label_duration"], pack["scr_label_hba1c"],
            None,
            pack["scr_label_notes"],
        ]
        if hasattr(self, "_scr_clinical_form"):
            for row, text in enumerate(clinical_labels):
                if text is None:
                    continue
                item = self._scr_clinical_form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item and item.widget():
                    item.widget().setText(text)
                    item.widget().setStyleSheet(self._form_label_stylesheet())

