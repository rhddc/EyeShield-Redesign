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
)
from PySide6.QtGui import QPixmap, QFont, QRegularExpressionValidator, QIcon, QPainter, QColor, QDragEnterEvent, QDropEvent
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
    from .screening_widgets import ClickableImageLabel
    from .screening_results import ResultsWindow
    from .logic_improvements import (
        ScreeningFlowGuard,
        DuplicateDetector,
        DuplicateDialog,
    )
    from .app_paths import PATIENT_RECORDS_DB_PATH
    from .auth import UserManager
    import emr_service as emr
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
    from screening_widgets import ClickableImageLabel
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
        "QLabel { border:2px solid #3f7ca7; border-radius:10px;"
        " background:#000000; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pixmap_full = QPixmap()
        self._reset_placeholder()

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
        self.setText(
            "Drop fundus image here or click to browse\n\n"
            "Supports JPG, PNG, JPEG"
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


class ModernCalendarDateEdit(QDateEdit):
    """Clean date picker — dropdown arrow only, no separate button panel."""

    def __init__(self, min_date: QDate, max_date: QDate, arrow_icon_path: str, default_date: QDate = None, parent=None):
        super().__init__(parent)
        self._min_date = min_date
        self._max_date = max_date
        self._default_date = default_date or QDate(2000, 1, 1)
        self._arrow_icon_path = str(arrow_icon_path or "").replace("\\", "/")

        self.setDisplayFormat("dd/MM/yyyy")
        self.setCalendarPopup(True)
        self.setMinimumDate(min_date)
        self.setMaximumDate(max_date)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setSpecialValueText("")
        self.setDate(self._default_date)

        cal = QCalendarWidget(self)
        cal.setGridVisible(False)
        cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        cal.setMinimumSize(410, 320)
        cal.currentPageChanged.connect(self._sync_year_dropdown)
        self.setCalendarWidget(cal)

        # Build the custom year dropdown once the calendar nav is initialized.
        QTimer.singleShot(0, self._setup_year_dropdown)

    def _setup_year_dropdown(self):
        cal = self.calendarWidget()
        if not cal:
            return

        nav = cal.findChild(QWidget, "qt_calendar_navigationbar")
        if not nav:
            QTimer.singleShot(0, self._setup_year_dropdown)
            return

        year_spin = nav.findChild(QSpinBox, "qt_calendar_yearedit")
        if not year_spin:
            return

        year_combo = nav.findChild(QComboBox, "qt_calendar_yearcombo")
        if year_combo is None:
            year_combo = QComboBox(nav)
            year_combo.setObjectName("qt_calendar_yearcombo")
            year_combo.setMinimumWidth(92)
            year_combo.setMaxVisibleItems(12)
            year_combo.setEditable(False)
            year_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

            for year in range(self._min_date.year(), self._max_date.year() + 1):
                year_combo.addItem(str(year), year)

            year_combo.currentIndexChanged.connect(self._on_year_dropdown_changed)

            nav_layout = nav.layout()
            if nav_layout is not None:
                idx = nav_layout.indexOf(year_spin)
                if idx >= 0:
                    nav_layout.insertWidget(idx, year_combo)
                else:
                    nav_layout.addWidget(year_combo)

        # Hide spinbox so year changes are done from dropdown.
        year_spin.hide()
        year_spin.setEnabled(False)

        # Hide the default year text button so only the dropdown is visible.
        year_button = nav.findChild(QWidget, "qt_calendar_yearbutton")
        if year_button is not None:
            year_button.hide()
            year_button.setEnabled(False)
        self._sync_year_dropdown()

    def _sync_year_dropdown(self, year: int | None = None, _month: int | None = None):
        cal = self.calendarWidget()
        if not cal:
            return

        if year is None:
            year = cal.yearShown()

        nav = cal.findChild(QWidget, "qt_calendar_navigationbar")
        if not nav:
            return

        year_combo = nav.findChild(QComboBox, "qt_calendar_yearcombo")
        if not year_combo:
            return

        idx = year_combo.findData(int(year))
        if idx >= 0 and year_combo.currentIndex() != idx:
            prev_state = year_combo.blockSignals(True)
            year_combo.setCurrentIndex(idx)
            year_combo.blockSignals(prev_state)

    def _on_year_dropdown_changed(self, index: int):
        cal = self.calendarWidget()
        if not cal or index < 0:
            return

        nav = cal.findChild(QWidget, "qt_calendar_navigationbar")
        if not nav:
            return

        year_combo = nav.findChild(QComboBox, "qt_calendar_yearcombo")
        if not year_combo:
            return

        year = year_combo.itemData(index)
        if year is None:
            return

        cal.setCurrentPage(int(year), cal.monthShown())

    def apply_theme(self, dark: bool):
        if dark:
            f_bg, f_text, border, focus = "#2b3038", "#d8dee8", "#495160", "#7b92ad"
            d_bg, d_border = "#343c48", "#596577"
            c_bg, c_text, c_border = "#262c34", "#d8dee8", "#495160"
            nav_bg = "#2d3440"
            sel_bg, sel_fg = "#4f5f75", "#eaf0f7"
            today, menu_bg = "#8ea3bb", "#2a3038"
            weekend = "#a6b3c3"
        else:
            f_bg, f_text, border, focus = "#ffffff", "#1f2933", "#d7dde6", "#6f8aa6"
            d_bg, d_border = "#f3f6fa", "#c1ccd9"
            c_bg, c_text, c_border = "#ffffff", "#1f2933", "#dde4ed"
            nav_bg = "#f7f9fc"
            sel_bg, sel_fg = "#dbe5f0", "#1f2933"
            today, menu_bg = "#8ea6bf", "#ffffff"
            weekend = "#6b7787"

        arrow = self._arrow_icon_path

        self.setStyleSheet(f"""
            QDateEdit {{
                background: {f_bg};
                color: {f_text};
                border: 1.5px solid {border};
                border-radius: 6px;
                padding: 6px 36px 6px 10px;
                min-height: 28px;
                selection-background-color: {focus};
            }}
            QDateEdit:focus {{
                border: 1.5px solid {focus};
            }}
            QDateEdit::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left: 1px solid {border};
                background: {d_bg};
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            QDateEdit::down-arrow {{
                image: url("{arrow}");
                width: 10px;
                height: 10px;
            }}
        """)

        cal = self.calendarWidget()
        if not cal:
            return

        cal.setStyleSheet(f"""
            QCalendarWidget {{
                background: {c_bg};
                border: 1px solid {c_border};
                border-radius: 10px;
            }}

            /* ── Navigation bar ── */
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background: {nav_bg};
                border-bottom: 1px solid {c_border};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                padding: 4px 6px;
            }}
            QCalendarWidget QToolButton {{
                color: {c_text};
                background: transparent;
                border: none;
                border-radius: 5px;
                font-size: 13px;
                font-weight: 600;
                padding: 4px 10px;
            }}
            QCalendarWidget QToolButton:hover {{
                background: {d_bg};
            }}

            /* Hide the forward/back arrow buttons — navigation via month/year dropdowns only */
            QCalendarWidget QToolButton#qt_calendar_prevmonth,
            QCalendarWidget QToolButton#qt_calendar_nextmonth {{
                qproperty-icon: none;
                font-size: 16px;
                font-weight: 700;
                padding: 2px 8px;
                color: {focus};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth::menu-indicator,
            QCalendarWidget QToolButton#qt_calendar_nextmonth::menu-indicator {{
                width: 0;
                height: 0;
            }}

            /* Month / year dropdowns */
            QCalendarWidget QToolButton::menu-indicator {{
                image: url("{arrow}");
                width: 10px;
                height: 7px;
                subcontrol-position: right center;
                subcontrol-origin: padding;
                right: 4px;
            }}
            QCalendarWidget QMenu {{
                background: {menu_bg};
                color: {c_text};
                border: 1px solid {c_border};
                border-radius: 6px;
                padding: 4px;
            }}
            QCalendarWidget QMenu::item {{
                padding: 5px 18px;
                border-radius: 4px;
            }}
            QCalendarWidget QMenu::item:selected {{
                background: {sel_bg};
                color: {sel_fg};
            }}
            QCalendarWidget QSpinBox {{
                background: {c_bg};
                color: {c_text};
                border: 1px solid {c_border};
                border-radius: 5px;
                padding: 2px 6px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo {{
                background: {c_bg};
                color: {c_text};
                border: 1px solid {c_border};
                border-radius: 5px;
                padding: 2px 20px 2px 8px;
                min-width: 76px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo::drop-down {{
                border: none;
                width: 18px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo::down-arrow {{
                image: url("{arrow}");
                width: 9px;
                height: 6px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo QAbstractItemView {{
                background: {menu_bg};
                color: {c_text};
                border: 1px solid {c_border};
                selection-background-color: {sel_bg};
                selection-color: {sel_fg};
            }}

            /* ── Day grid ── */
            QCalendarWidget QAbstractItemView {{
                background: {c_bg};
                color: {c_text};
                selection-background-color: {sel_bg};
                selection-color: {sel_fg};
                outline: none;
                gridline-color: transparent;
            }}
            QCalendarWidget QAbstractItemView:disabled {{
                color: #9ca3af;
            }}
            QCalendarWidget QTableView {{
                alternate-background-color: {c_bg};
                border: none;
            }}
            QCalendarWidget QTableView::item {{
                border: none;
                border-radius: 5px;
                padding: 3px;
                margin: 1px;
            }}
            QCalendarWidget QTableView::item:hover {{
                background: {d_bg};
                color: {c_text};
            }}
            QCalendarWidget QTableView::item:selected {{
                background: {sel_bg};
                color: {sel_fg};
            }}
            QCalendarWidget QTableView::item:disabled {{
                color: #9aa5b1;
            }}
            QCalendarWidget QTableView::item:today {{
                border: none;
                background: {d_bg};
                color: {c_text};
                font-weight: 600;
                border-radius: 5px;
            }}

            /* Day-of-week header row */
            QCalendarWidget QWidget {{
                alternate-background-color: {c_bg};
            }}
        """)

        self._setup_year_dropdown()


_REDESIGN_STYLESHEET = """
QWidget { background:#ffffff; color:#1f2937; font-family:"Segoe UI","Inter","Calibri",sans-serif; font-size:13px; }
QFrame#card { background:#ffffff; border:1px solid #dde3ea; border-radius:14px; }
QLineEdit, QDateEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
    background:#ffffff; border:1.5px solid #d3dae3; border-radius:6px; padding:6px 10px;
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
    border-radius:8px;
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
        self._ensure_patient_records_schema()
        self.current_image = None
        self.patient_counter = 0
        self.min_dob_date = QDate(1900, 1, 1)
        self.max_dob_date = QDate.currentDate()
        self.default_dob_date = QDate(2000, 1, 1)  # Default calendar view year
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

    def _apply_dob_theme_style(self):
        if not hasattr(self, "p_dob"):
            return
        dark = self._is_dark_theme_active()
        if hasattr(self.p_dob, "apply_theme"):
            self.p_dob.apply_theme(dark)
            self._dob_default_style = self.p_dob.styleSheet()
            self._dob_invalid_style = self._dob_default_style + "QDateEdit{border:1.5px solid #ef4444;}"
            return

        # Fallback for non-modern date widgets.
        self._dob_default_style = "QDateEdit{border:1.5px solid #d3dae3;border-radius:6px;}"
        self._dob_invalid_style = "QDateEdit{border:1.5px solid #ef4444;border-radius:6px;}"
        self.p_dob.setStyleSheet(self._dob_default_style)

    def create_unified_page(self):
        root = QWidget()
        root.setStyleSheet(_REDESIGN_STYLESHEET)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(12)

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

        content_root = QWidget()
        content_layout = QHBoxLayout(content_root)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        root_layout.addWidget(content_root)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(0)
        content_layout.addWidget(splitter)

        def make_card():
            frame = QFrame()
            frame.setObjectName("card")
            layout = QVBoxLayout(frame)
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
        section_title(c1, "PATIENT INFORMATION", "scr_patient_info")

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

        self.p_name = QLineEdit()
        self.p_name.setPlaceholderText("Full name")
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
        self._apply_dob_theme_style()
        c1.addLayout(row2(field("Full Name", self.p_name, "scr_label_name"), field("Date of Birth", self.p_dob, "scr_label_dob")))

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
        c1.addLayout(row2(field("Age", self.p_age, "scr_label_age"), field("Sex", self.p_sex, "scr_label_sex")))
        self.p_eye.hide()

        self.p_contact = QLineEdit()
        self.p_contact.setPlaceholderText("Phone or Email")

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

        c1.addLayout(field("Contact", self.p_contact, "scr_label_contact"))

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
        section_title(c2, "CLINICAL HISTORY", "scr_clinical_history")
        self.diabetes_type = QComboBox()
        self.diabetes_type.setObjectName("diabetesTypeDropdown")
        self.diabetes_type.addItems(["Select", "Type 1", "Type 2", "Gestational", "Other"])
        self._apply_visible_dropdown_style(self.diabetes_type)
        self.diabetes_diagnosis_date = QLineEdit()
        self.diabetes_diagnosis_date.setPlaceholderText("dd/mm/yyyy")
        self.diabetes_diagnosis_date.setMaxLength(10)
        self.diabetes_diagnosis_date.textChanged.connect(self._on_diagnosis_date_changed)
        c2.addLayout(row2(field("Diabetes Type", self.diabetes_type, "scr_label_diabetes"), field("Diagnosis Date", self.diabetes_diagnosis_date)))

        self.diabetes_duration = QSpinBox()
        self.diabetes_duration.setSuffix(" years")
        self.diabetes_duration.setRange(0, 80)
        self.diabetes_duration.setReadOnly(True)
        self.diabetes_duration.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.diabetes_duration.setStyleSheet(
            "QSpinBox{background:#f6f8fb;color:#475569;border:1.5px solid #d3dae3;border-radius:6px;padding:6px 10px;}"
        )
        self.hba1c = QDoubleSpinBox()
        self.hba1c.setRange(0.0, 20.0)
        self.hba1c.setDecimals(1)
        self.hba1c.setSuffix(" %")
        self.hba1c.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.hba1c.setSpecialValueText(" ")
        self.hba1c.setValue(0.0)
        self.hba1c.valueChanged.connect(self._on_hba1c_changed)
        c2.addLayout(row2(field("Duration", self.diabetes_duration, "scr_label_duration"), field("HbA1c (%)", self.hba1c, "scr_label_hba1c")))
        self.hba1c_warn_label = QLabel("")
        self.hba1c_warn_label.setStyleSheet("color:#b45309;background:transparent;font-size:12px;font-weight:600;")
        self.hba1c_warn_label.hide()
        c2.addWidget(self.hba1c_warn_label)

        # Treatment regimen dropdown
        self.treatment_regimen = QComboBox()
        self.treatment_regimen.setObjectName("treatmentRegimenDropdown")
        self.treatment_regimen.addItems(["Select", "Insulin only", "Oral medications only", "Insulin + Oral medications", "Diet control only", "None/Unknown"])
        self._apply_visible_dropdown_style(self.treatment_regimen)
        c2.addLayout(field("Treatment Regimen", self.treatment_regimen, "scr_label_treatment"))

        # Previous DR stage dropdown
        self.prev_dr_stage = QComboBox()
        self.prev_dr_stage.setObjectName("prevDRStageDropdown")
        self.prev_dr_stage.addItems(["Select", "No previous DR", "Mild NPDR", "Moderate NPDR", "Severe NPDR", "PDR (Proliferative)", "Unknown"])
        self._apply_visible_dropdown_style(self.prev_dr_stage)
        c2.addLayout(field("Previous DR Stage", self.prev_dr_stage, "scr_label_prev_dr"))

        self.prev_treatment = QCheckBox("Previous DR Treatment (Laser/Injection)")
        c2.addWidget(self.prev_treatment)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Enter clinical notes…")
        self.notes.setMinimumHeight(72)
        self.notes.setMaximumHeight(90)
        self.notes.hide()
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(card1, 1)
        top_row.addWidget(card2, 1)
        left_col.addLayout(top_row)

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
        left_col.addWidget(card_vitals)

        # Front desk quick actions (shown only when upload is restricted).
        self._fd_action_row = QFrame()
        self._fd_action_row.setObjectName("card")
        fd_actions = QHBoxLayout(self._fd_action_row)
        fd_actions.setContentsMargins(12, 10, 12, 10)
        fd_actions.setSpacing(8)
        fd_actions.addWidget(QLabel("Purpose:"), 0)
        self.fd_purpose_combo = QComboBox()
        self.fd_purpose_combo.addItems(["New patient", "Follow-up patient"])
        self.fd_purpose_combo.setFixedHeight(34)
        self.fd_purpose_combo.setMinimumWidth(170)
        self.fd_purpose_combo.setCursor(Qt.PointingHandCursor)
        fd_actions.addWidget(self.fd_purpose_combo, 0)
        self.btn_fd_save_queue = QPushButton("Save && Queue Patient")
        self.btn_fd_save_queue.setObjectName("btnPrimary")
        self.btn_fd_save_queue.setMinimumHeight(38)
        self.btn_fd_save_queue.clicked.connect(self._save_and_queue_patient)
        fd_actions.addStretch(1)
        fd_actions.addWidget(self.btn_fd_save_queue)
        self._fd_action_row.hide()
        left_col.addWidget(self._fd_action_row)

        left_col.addStretch()
        splitter.addWidget(left_panel)

        card3 = QFrame()
        card3.setObjectName("card")
        card3.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._upload_card = card3
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(24, 20, 24, 22)
        c3.setSpacing(12)
        section_title(c3, "Fundus Image Upload", "scr_image_upload")

        self.image_label = DropZoneLabel()
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
        self.btn_upload.setMinimumHeight(36)
        self.btn_upload.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        upload_icon = self._resolve_icon_path("upload.svg", "camera.svg")
        if upload_icon:
            self.btn_upload.setIcon(self._tinted_icon(upload_icon, "#60a5fa", 18))
            self.btn_upload.setIconSize(QSize(18, 18))
        self.btn_upload.clicked.connect(self.upload_image)

        self.btn_take_picture = None

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("btnDanger")
        self.btn_clear.setMinimumHeight(36)
        self.btn_clear.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        clear_icon = self._resolve_icon_path("discard.svg")
        if clear_icon:
            self.btn_clear.setIcon(self._tinted_icon(clear_icon, "#60a5fa", 18))
            self.btn_clear.setIconSize(QSize(18, 18))
        self.btn_clear.clicked.connect(self.clear_image)
        btn_row.addWidget(self.btn_upload)
        btn_row.addWidget(self.btn_clear)
        c3.addLayout(btn_row)

        self.btn_analyze = QPushButton("Analyze Image")
        self.btn_analyze.setObjectName("btnAnalyze")
        self.btn_analyze.setMinimumHeight(42)
        self.btn_analyze.setEnabled(True)
        self.btn_analyze.clicked.connect(self.open_results_window)
        c3.addWidget(self.btn_analyze)

        self.upload_error_label = QLabel("")
        self.upload_error_label.setStyleSheet("color:#dc2626;background:transparent;font-size:12px;font-weight:600;")
        self.upload_error_label.setWordWrap(True)
        self.upload_error_label.hide()
        c3.addWidget(self.upload_error_label)

        splitter.addWidget(card3)
        self._intake_splitter = splitter
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([640, 520])
        handle = splitter.handle(1)
        handle.setDisabled(True)
        handle.setCursor(Qt.CursorShape.ArrowCursor)
        self._apply_role_permissions()
        self._set_tab_order_unified()
        return root

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
                self._intake_splitter.setSizes([1, 0])
            else:
                self._intake_splitter.setSizes([640, 520])
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

    def _guard_upload_permission(self) -> bool:
        if self._is_upload_restricted():
            QMessageBox.information(
                self,
                "Assessment Restricted",
                "Fundus image upload is available only on clinician assessment.",
            )
            return True
        return False

    def _save_and_queue_patient(self) -> None:
        """Frontdesk action: save intake demographics and assign queue entry."""
        if self._guard_busy_action("saving and queueing this patient"):
            return
        if not self._is_upload_restricted():
            # Clinician flow should continue using the full screening save path.
            self.save_screening()
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
        if not self._validate_patient_basics():
            return
        if not self._validate_blood_pressure() or not self._validate_blood_glucose():
            return

        full_name = self.p_name.text().strip()
        parts = [p for p in full_name.split() if p.strip()]
        if len(parts) < 2:
            QMessageBox.warning(self, "Missing Information", "Please enter both first and last name.")
            return
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
        contact = self.p_contact.text().strip()

        # Visit-scoped details (do NOT write into emr_patients; these vary per date/visit).
        height_cm = float(self.height.value()) if self.height.value() > 0 else None
        weight_kg = float(self.weight.value()) if self.weight.value() > 0 else None
        dm_type = self.diabetes_type.currentText().strip()
        if dm_type == "Select":
            dm_type = ""
        dm_duration = float(self.diabetes_duration.value()) if self.diabetes_duration.value() > 0 else None
        hba1c_val = float(self.hba1c.value()) if self.hba1c.value() > 0 else None

        patient_id = int(self._emr_patient_pk) if self._emr_patient_pk else None
        if patient_id is None:
            duplicate_patient = emr.find_duplicate_patient(first_name, last_name, dob_str)
            if duplicate_patient:
                patient_id = int(duplicate_patient.get("patient_id") or 0) or None
                self._emr_patient_pk = patient_id
                existing_code = str(duplicate_patient.get("patient_code") or "").strip()
                if existing_code:
                    self.p_id.setText(existing_code)
        if patient_id is not None:
            patient_fields = {
                "last_name": last_name,
                "first_name": first_name,
                "date_of_birth": dob_str,
                "age": int(self.p_age.value()) if self.p_age.value() > 0 else None,
                "sex": sex or None,
                "contact_number": contact or None,
            }
            ok = emr.update_patient_fields(patient_id, patient_fields, uid, action="UPDATE_PATIENT")
            if not ok:
                QMessageBox.warning(self, "Save Failed", "Could not update patient information.")
                return
        else:
            try:
                patient_id = emr.create_patient(
                    uid,
                    last_name=last_name,
                    first_name=first_name,
                    date_of_birth=dob_str,
                    sex=sex,
                    contact_number=contact,
                )
            except Exception as exc:
                QMessageBox.warning(self, "Save Failed", f"Could not save patient information.\n\n{exc}")
                return
            self._emr_patient_pk = int(patient_id)

        can_queue, reason = emr.can_create_visit_for_patient(int(patient_id))
        if not can_queue:
            QMessageBox.warning(self, "Already Queued", reason)
            return

        purpose_ui = str(getattr(self, "fd_purpose_combo", None).currentText() if hasattr(self, "fd_purpose_combo") else "")
        purpose = "follow_up" if "follow" in purpose_ui.lower() else "new"
        queue_id = emr.assign_queue_entry(int(patient_id), uid, screening_purpose=purpose)
        self._emr_queue_entry_id = int(queue_id)
        # Persist visit-scoped vitals/history for this queue entry.
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
            "diabetes_diagnosis_date": (self.diabetes_diagnosis_date.text().strip() if hasattr(self, "diabetes_diagnosis_date") else "") or None,
            "treatment_regimen": (self.treatment_regimen.currentText().strip() if hasattr(self, "treatment_regimen") else "") or None,
            "prev_dr_stage": (self.prev_dr_stage.currentText().strip() if hasattr(self, "prev_dr_stage") else "") or None,
            "prev_treatment": "Yes" if self.prev_treatment.isChecked() else "No",
            "symptom_blurred_vision": 1 if getattr(self, "symptom_blurred", None) and self.symptom_blurred.isChecked() else 0,
            "symptom_floaters": 1 if getattr(self, "symptom_floaters", None) and self.symptom_floaters.isChecked() else 0,
            "symptom_flashes": 1 if getattr(self, "symptom_flashes", None) and self.symptom_flashes.isChecked() else 0,
            "symptom_vision_loss": 1 if getattr(self, "symptom_vision_loss", None) and self.symptom_vision_loss.isChecked() else 0,
            "symptom_other": (self.symptom_other.text().strip() if hasattr(self, "symptom_other") else "") or None,
            "height_cm": height_cm,
            "weight_kg": weight_kg,
            "notes": self.notes.toPlainText().strip() if hasattr(self, "notes") else "",
        }
        with contextlib.suppress(Exception):
            emr.upsert_visit_details(
                queue_id=int(queue_id),
                patient_id=int(patient_id),
                captured_by=int(uid),
                details=visit_details,
            )
        queue_entry = emr.get_queue_entry(int(queue_id)) or {}
        queue_number = str(queue_entry.get("queue_number") or "")

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

    def _set_tab_order_unified(self):
        self.setTabOrder(self.p_name, self.p_dob)
        self.setTabOrder(self.p_dob, self.p_sex)
        self.setTabOrder(self.p_sex, self.p_contact)
        self.setTabOrder(self.p_contact, self.p_eye)
        self.setTabOrder(self.p_eye, self.diabetes_type)
        self.setTabOrder(self.diabetes_type, self.diabetes_diagnosis_date)
        self.setTabOrder(self.diabetes_diagnosis_date, self.diabetes_duration)
        self.setTabOrder(self.diabetes_duration, self.hba1c)
        self.setTabOrder(self.hba1c, self.prev_treatment)
        self.setTabOrder(self.prev_treatment, self.va_left)
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
        self.p_name.setValidator(QRegularExpressionValidator(self.name_regex, self))

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
        name = self.p_name.text().strip()
        dob_date = self._get_dob_date()
        sex = self.p_sex.currentText().strip()
        contact = self.p_contact.text().strip()
        age_val = self.p_age.value()
        height_val = self.height.value() if hasattr(self, "height") else 0.0
        weight_val = self.weight.value() if hasattr(self, "weight") else 0.0

        missing_fields = []
        if not name:
            missing_fields.append("Name")
        if not dob_date.isValid():
            missing_fields.append("Date of Birth")
        if not sex:
            missing_fields.append("Sex")
        if not contact:
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

        if isinstance(self.p_dob, QDateEdit) and not self._dob_user_selected:
            QMessageBox.warning(self, "Missing Information", "Please select the patient's actual date of birth.")
            return False

        if not self.name_regex.match(name).hasMatch():
            QMessageBox.warning(self, "Error", "Name can only include letters, spaces, hyphens, and apostrophes")
            return False

        if age_val < 1 or age_val > 120:
            QMessageBox.warning(self, "Invalid Age", "Age must be between 1 and 120.")
            return False

        va_left_text, _ = self._normalize_visual_acuity(self.va_left.text()) if hasattr(self, "va_left") else ("", True)
        va_right_text, _ = self._normalize_visual_acuity(self.va_right.text()) if hasattr(self, "va_right") else ("", True)

        if hasattr(self, "va_left"):
            self.va_left.setText(va_left_text)
        if hasattr(self, "va_right"):
            self.va_right.setText(va_right_text)

        return True

    def _on_hba1c_changed(self, value: float):
        if value > 9.0:
            self.hba1c_warn_label.setText("High HbA1c - verify this value before continuing")
            self.hba1c_warn_label.show()
        else:
            self.hba1c_warn_label.hide()

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

    def _on_diagnosis_date_changed(self, text):
        """Format diagnosis date input and auto-calculate duration."""
        digits = "".join(ch for ch in text if ch.isdigit())[:8]

        if len(digits) <= 2:
            formatted = digits
        elif len(digits) <= 4:
            formatted = f"{digits[:2]}/{digits[2:]}"
        else:
            formatted = f"{digits[:2]}/{digits[2:4]}/{digits[4:]}"

        if formatted != text:
            self.diabetes_diagnosis_date.blockSignals(True)
            self.diabetes_diagnosis_date.setText(formatted)
            self.diabetes_diagnosis_date.blockSignals(False)
            self.diabetes_diagnosis_date.setCursorPosition(len(formatted))

        # Validate and style
        self._update_diagnosis_date_style(digits)

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
        date = QDate.fromString(self.diabetes_diagnosis_date.text().strip(), "dd/MM/yyyy")

        if not date.isValid():
            return QDate()
        if date < QDate(1900, 1, 1) or date > QDate.currentDate():
            return QDate()

        # Check if diagnosis date is after birth date
        dob_date = self._get_dob_date()
        if dob_date.isValid() and date < dob_date:
            return QDate()

        return date

    def _update_duration_from_diagnosis_date(self):
        """Auto-calculate diabetes duration from diagnosis date."""
        diag_date = self._get_diagnosis_date()
        if not diag_date.isValid():
            self.diabetes_duration.setValue(0)
            return

        today = QDate.currentDate()
        years = today.year() - diag_date.year()
        if (today.month(), today.day()) < (diag_date.month(), diag_date.day()):
            years -= 1

        self.diabetes_duration.setValue(max(0, years))

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

        self.p_contact = QLineEdit()
        self.p_contact.setPlaceholderText("Phone or Email")
        patient_form.addRow("Contact:", self.p_contact)

        self.p_eye = QComboBox()
        self.p_eye.addItems(["", "Both Eyes", "Left Eye", "Right Eye"])
        patient_form.addRow("Eye(s):", self.p_eye)

        patient_group.setLayout(patient_form)
        layout.addWidget(patient_group)

        clinical_group = QGroupBox("Clinical History")
        clinical_form = QFormLayout()

        self.diabetes_type = QComboBox()
        self.diabetes_type.addItems(["Select", "Type 1", "Type 2", "Gestational", "Other"])
        clinical_form.addRow("Diabetes Type:", self.diabetes_type)

        self.diabetes_duration = QSpinBox()
        self.diabetes_duration.setSuffix(" years")
        self.diabetes_duration.setRange(0, 80)
        clinical_form.addRow("Duration:", self.diabetes_duration)

        self.hba1c = QDoubleSpinBox()
        self.hba1c.setRange(0.0, 20.0)
        self.hba1c.setDecimals(1)
        self.hba1c.setSuffix(" %")
        self.hba1c.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.hba1c.setSpecialValueText(" ")
        self.hba1c.setValue(0.0)
        clinical_form.addRow("HbA1c:", self.hba1c)

        self.prev_treatment = QCheckBox("Previous DR Treatment")
        self.prev_treatment.setStyleSheet(CHECKBOX_STYLE)
        clinical_form.addRow("", self.prev_treatment)

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
        pid = self._next_unique_patient_id()
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
        if code:
            self.p_id.setText(code)
        fn = str(emr_patient.get("first_name") or "").strip()
        ln = str(emr_patient.get("last_name") or "").strip()
        self.p_name.setText(f"{fn} {ln}".strip() or (ln or fn))

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
        self.p_contact.setText(str(emr_patient.get("contact_number") or ""))

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
        ha = emr_patient.get("hba1c")
        if ha is not None and hasattr(self, "hba1c"):
            try:
                self.hba1c.setValue(float(ha))
            except (TypeError, ValueError):
                pass

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
        if not date.isValid():
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

    def _set_patient_context_locked(self, locked: bool):
        """Lock demographics fields during follow-up to prevent patient switching."""
        # For clinicians, lock demographics to prevent accidental patient switching mid-diagnosis.
        # For front desk follow-ups, keep fields editable so they can update details before queueing.
        allow_edit = self._is_upload_restricted()
        if hasattr(self, "p_name"):
            self.p_name.setReadOnly(locked and not allow_edit)
        if hasattr(self, "p_contact"):
            self.p_contact.setReadOnly(locked and not allow_edit)
        if hasattr(self, "p_dob"):
            if isinstance(self.p_dob, QDateEdit):
                self.p_dob.setReadOnly(locked and not allow_edit)
            else:
                self.p_dob.setReadOnly(locked and not allow_edit)
        if hasattr(self, "p_sex"):
            self.p_sex.setEnabled((not locked) or allow_edit)

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
        self._emr_patient_pk = None
        self._emr_screening_id = None
        self._emr_queue_entry_id = None
        if hasattr(self, "fd_purpose_combo"):
            idx = self.fd_purpose_combo.findText("New patient")
            if idx >= 0:
                self.fd_purpose_combo.setCurrentIndex(idx)

        self.generate_patient_id()
        self.p_name.clear()
        self.p_contact.clear()
        if isinstance(self.p_dob, QDateEdit):
            self._set_dob_date(self.default_dob_date, user_selected=False)
        else:
            self.p_dob.clear()
        self.p_age.setValue(0)
        self.p_sex.setCurrentIndex(0)
        self.p_eye.setCurrentIndex(0)
        self.diabetes_type.setCurrentIndex(0)
        if hasattr(self, "diabetes_diagnosis_date"):
            self.diabetes_diagnosis_date.clear()
        self.diabetes_duration.setValue(0)
        self.hba1c.setValue(0.0)
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
                SELECT id, patient_id, name, birthdate, age, sex, contact, eyes,
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
            (id_val, patient_id, name, birthdate, age, sex, contact, eyes,
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
            self.p_name.setText(str(name or ""))

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

            # Safe diabetes type setting
            diabetes_str = str(diabetes_type or "").strip()
            if diabetes_str and diabetes_str != "Select":
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

            # Safe float conversion for hba1c
            try:
                hba1c_text = str(hba1c or "").replace("%", "").strip()
                hba1c_val = float(hba1c_text) if hba1c_text else 0.0
                self.hba1c.setValue(hba1c_val)
            except (ValueError, TypeError):
                self.hba1c.setValue(0.0)

            # Safe prev_treatment boolean
            try:
                self.prev_treatment.setChecked(bool(prev_treatment and str(prev_treatment).lower() in ("yes", "true", "1")))
            except Exception:
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
                self.diabetes_diagnosis_date.setText(str(diag_date or ""))

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

            # Front desk UI: auto-detect purpose for rescreens/follow-ups.
            if hasattr(self, "fd_purpose_combo"):
                purpose = str(self._current_screening_type or "").strip().lower()
                target = "Follow-up patient" if "follow" in purpose else "New patient"
                idx = self.fd_purpose_combo.findText(target)
                if idx >= 0:
                    self.fd_purpose_combo.setCurrentIndex(idx)

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

        # Front desk UI: follow-up loads should default to follow-up purpose.
        if hasattr(self, "fd_purpose_combo"):
            idx = self.fd_purpose_combo.findText("Follow-up patient")
            if idx >= 0:
                self.fd_purpose_combo.setCurrentIndex(idx)
        
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
            "hba1c":          self.hba1c.value(),
            "duration":       self.diabetes_duration.value(),
            "prev_treatment": self.prev_treatment.isChecked(),
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
        return {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "patient_id": self.p_id.text().strip(),
            "name": self.p_name.text().strip(),
            "dob": self.p_dob.text().strip(),
            "age": self.p_age.value(),
            "sex": self.p_sex.currentText(),
            "contact": self.p_contact.text().strip(),
            "eye": self.p_eye.currentText(),
            "diabetes_type": self.diabetes_type.currentText(),
            "diagnosis_date": self.diabetes_diagnosis_date.text().strip() if hasattr(self, "diabetes_diagnosis_date") else "",
            "duration": self.diabetes_duration.value(),
            "hba1c": self.hba1c.value(),
            "prev_treatment": self.prev_treatment.isChecked(),
            "va_left": self.va_left.text().strip() if hasattr(self, "va_left") else "",
            "va_right": self.va_right.text().strip() if hasattr(self, "va_right") else "",
            "bp_systolic": self.bp_systolic.value() if hasattr(self, "bp_systolic") else 0,
            "bp_diastolic": self.bp_diastolic.value() if hasattr(self, "bp_diastolic") else 0,
            "fbs": self.fbs.value() if hasattr(self, "fbs") else 0,
            "rbs": self.rbs.value() if hasattr(self, "rbs") else 0,
            "symptom_blurred": self.symptom_blurred.isChecked(),
            "symptom_floaters": self.symptom_floaters.isChecked(),
            "symptom_flashes": self.symptom_flashes.isChecked(),
            "symptom_vision_loss": self.symptom_vision_loss.isChecked(),
            "symptom_other": self.symptom_other.text().strip(),
            "notes": self.notes.toPlainText(),
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
        self.p_name.setText(str(data.get("name") or ""))
        self.p_dob.setText(str(data.get("dob") or ""))
        self.p_age.setValue(int(data.get("age") or 0))
        self.p_sex.setCurrentText(str(data.get("sex") or ""))
        self.p_contact.setText(str(data.get("contact") or ""))
        self.p_eye.setCurrentText(str(data.get("eye") or ""))
        self.diabetes_type.setCurrentText(str(data.get("diabetes_type") or "Select"))
        if hasattr(self, "diabetes_diagnosis_date"):
            self.diabetes_diagnosis_date.setText(str(data.get("diagnosis_date") or ""))
        self.diabetes_duration.setValue(int(data.get("duration") or 0))
        self.hba1c.setValue(float(str(data.get("hba1c") or "").replace("%", "").strip() or 0.0))
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
        self.current_image = None
        if hasattr(self.image_label, "clear_image"):
            self.image_label.clear_image()
        else:
            self._apply_upload_placeholder_style()
        self.btn_analyze.setEnabled(True)
        self._set_upload_error("")

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
                    patient_id = ?, name = ?, birthdate = ?, age = ?, sex = ?, contact = ?, eyes = ?,
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
        if bool(getattr(self, "_doctor_queue_mode", False)) and getattr(self, "_emr_screening_id", None):
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

            label_to_grade = {
                "No DR": 0,
                "Mild DR": 1,
                "Moderate DR": 2,
                "Severe DR": 3,
                "Proliferative DR": 4,
            }
            final_grade = label_to_grade.get(final_diagnosis_icdr) or label_to_grade.get(doctor_classification)
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
                "hba1c": float(self.hba1c.value()) if hasattr(self, "hba1c") and self.hba1c.value() > 0 else None,
                "diabetes_diagnosis_date": (self.diabetes_diagnosis_date.text().strip() if hasattr(self, "diabetes_diagnosis_date") else "") or None,
                "treatment_regimen": (self.treatment_regimen.currentText().strip() if hasattr(self, "treatment_regimen") else "") or None,
                "prev_dr_stage": (self.prev_dr_stage.currentText().strip() if hasattr(self, "prev_dr_stage") else "") or None,
                "prev_treatment": "Yes" if self.prev_treatment.isChecked() else "No",
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
            eye_row = next((e for e in (sc.get("eyes") or []) if str(e.get("eye_side") or "") == eye_side), None)
            if not eye_row:
                return {"status": "error", "error": "Could not resolve EMR eye record for this screening."}

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

        pid = self.p_id.text().strip()
        if not pid:
            pid = self.generate_patient_id()

        dob_date = self._get_dob_date()
        dob_str = dob_date.toString("yyyy-MM-dd") if dob_date.isValid() else ""

        diag_date = self._get_diagnosis_date()
        diag_date_str = diag_date.toString("yyyy-MM-dd") if diag_date.isValid() else ""

        age = self.p_age.value()
        sex = self.p_sex.currentText()
        contact = self.p_contact.text().strip()
        eye = self.p_eye.currentText()
        diabetes_type = self.diabetes_type.currentText()
        duration = self.diabetes_duration.value()
        hba1c = f"{self.hba1c.value():.1f}%" if self.hba1c.value() > 0 else ""
        prev_treatment = "Yes" if self.prev_treatment.isChecked() else "No"
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
            # Store first eye result with image/heatmap paths for dual-eye reports
            self._first_eye_result = {
                "eye": eye_label,
                "result": self.last_result_class,
                "confidence": self.last_result_conf,
                "image_path": getattr(self, 'current_image', '') or '',
                "heatmap_path": getattr(self.results_page, '_current_heatmap_path', '') or '',
            }
            self.results_page.mark_saved(self.p_name.text().strip(), eye_label, self.last_result_class)

            # Auto-prompt to compare if follow-up
            if screening_type == "follow_up" and previous_screening_id:
                box = QMessageBox(self._modal_parent_widget())
                box.setWindowTitle("Follow-up Completed")
                box.setIcon(QMessageBox.Icon.Information)
                box.setText(
                    f"<b>{eye_label}</b> follow-up screening saved successfully.\n\n"
                    f"Would you like to compare this result with the previous screening?"
                )
                compare_btn = box.addButton("Compare Results", QMessageBox.ButtonRole.AcceptRole)
                box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
                box.exec()
                if box.clickedButton() == compare_btn:
                    # Logic to trigger comparison view (if available in current scope)
                    if hasattr(self, "parent_dashboard") and hasattr(self.parent_dashboard, "reports_page"):
                        # We can navigate to reports and trigger comparison there
                        self.parent_dashboard.reports_page.open_comparison_dialog(
                            int(previous_screening_id), 
                            int(self._last_saved_record_id) if hasattr(self, "_last_saved_record_id") else 0
                        )

            # Auto-prompt to screen the other eye (only if first eye, not second)
            if not self._first_eye_result.get('_is_second_eye'):
                opposite_eye = "Left Eye" if eye_label == "Right Eye" else "Right Eye"
                # Check if opposite eye already has a saved record
                opposite_eye_exists = self._find_existing_eye_record(pid, opposite_eye) is not None

                # Only prompt if opposite eye hasn't been saved yet
                if not opposite_eye_exists:
                    box = QMessageBox(self._modal_parent_widget())
                    box.setWindowTitle("Screen Other Eye")
                    box.setIcon(QMessageBox.Icon.Question)
                    box.setText(
                        f"<b>{eye_label}</b> screening {'updated' if replace_record_id is not None else 'saved'} successfully.\n\n"
                        f"Would you like to screen the <b>{opposite_eye}</b> now?"
                    )
                    continue_btn = box.addButton("Screen Other Eye", QMessageBox.ButtonRole.AcceptRole)
                    box.addButton("Just This Eye", QMessageBox.ButtonRole.RejectRole)
                    box.exec()
                    if box.clickedButton() == continue_btn:
                        next_action = "screen_other_eye"
                        self.screen_other_eye()
            if bool(getattr(self, "_doctor_queue_mode", False)):
                self._reparent_results_into_stack(set_index_0=True)
                if next_action != "screen_other_eye":
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

        if self._current_eye_saved and left_record and right_record:
            box = QMessageBox(self)
            box.setWindowTitle("Both Eyes Screened")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("Both eyes are already screened for this patient.")
            box.setInformativeText("Choose an eye to replace, or continue reviewing current results.")
            replace_left_btn = box.addButton("Replace Left Eye", QMessageBox.ButtonRole.AcceptRole)
            replace_right_btn = box.addButton("Replace Right Eye", QMessageBox.ButtonRole.ActionRole)
            continue_btn = box.addButton("Continue", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(continue_btn)
            box.exec()

            chosen = box.clickedButton()
            if chosen == replace_left_btn:
                if self.load_patient_for_rescreen(int(left_record["id"]), replace_mode=True):
                    self.p_eye.setCurrentText("Left Eye")
                    self.stacked_widget.setCurrentIndex(0)
                return
            if chosen == replace_right_btn:
                if self.load_patient_for_rescreen(int(right_record["id"]), replace_mode=True):
                    self.p_eye.setCurrentText("Right Eye")
                    self.stacked_widget.setCurrentIndex(0)
                return
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

        # Capture current patient demographics and patient_id before resetting
        saved_pid = self.p_id.text().strip()
        name = self.p_name.text()
        dob_date = self._get_dob_date()
        dob_text = self.p_dob.text()
        age = self.p_age.value()
        sex = self.p_sex.currentText()
        contact = self.p_contact.text()
        d_type = self.diabetes_type.currentText()
        d_dur = self.diabetes_duration.value()
        hba1c_val = self.hba1c.value()
        prev = self.prev_treatment.isChecked()
        notes_text = self.notes.toPlainText()

        # Capture vitals
        va_l = self.va_left.text()
        va_r = self.va_right.text()
        bp_s = self.bp_systolic.value()
        bp_d = self.bp_diastolic.value()
        fbs_v = self.fbs.value()
        rbs_v = self.rbs.value()
        diag_date_text = self.diabetes_diagnosis_date.text() if hasattr(self, 'diabetes_diagnosis_date') else ""
        sym_blurred = self.symptom_blurred.isChecked()
        sym_floaters = self.symptom_floaters.isChecked()
        sym_flashes = self.symptom_flashes.isChecked()
        sym_vision_loss = self.symptom_vision_loss.isChecked()
        sym_other = self.symptom_other.text()
        
        # Capture Phase 1 additions
        height_v = self.height.value()
        weight_v = self.weight.value()
        bmi_v = self.bmi.value()
        treatment_reg = self.treatment_regimen.currentText()
        prev_dr = self.prev_dr_stage.currentText()
        current_screening_type = self._current_screening_type
        current_previous_screening_id = self._current_previous_screening_id
        current_follow_up_flag = self._current_follow_up_flag
        current_followup_date = self._current_followup_date
        current_followup_label = self._current_followup_label
        current_screening_group_id = self._current_screening_group_id

        # Preserve first eye result across reset so results page can show bilateral comparison
        saved_first_eye_result = self._first_eye_result

        # Full reset (generates new patient ID, clears everything)
        self.reset_screening()

        # Restore first eye result and mark second eye
        self._first_eye_result = saved_first_eye_result
        if self._first_eye_result:
            self._first_eye_result['_is_second_eye'] = True

        # Restore the same patient_id so both eyes share one ID
        self.p_id.setText(saved_pid)

        # Restore demographics for the same patient
        self.p_name.setText(name)
        if isinstance(self.p_dob, QDateEdit):
            if dob_date.isValid():
                self._set_dob_date(dob_date, user_selected=True)
            else:
                self._set_dob_date(self.default_dob_date, user_selected=False)
        else:
            self.p_dob.setText(dob_text)
        self.p_age.setValue(age)
        self.p_sex.setCurrentText(sex)
        self.p_contact.setText(contact)
        self.diabetes_type.setCurrentText(d_type)
        self.diabetes_duration.setValue(d_dur)
        self.hba1c.setValue(hba1c_val)
        self.prev_treatment.setChecked(prev)
        self.notes.setPlainText(notes_text)

        # Restore vitals
        self.va_left.setText(va_l)
        self.va_right.setText(va_r)
        self.bp_systolic.setValue(bp_s)
        self.bp_diastolic.setValue(bp_d)
        self.fbs.setValue(fbs_v)
        self.rbs.setValue(rbs_v)
        if hasattr(self, 'diabetes_diagnosis_date'):
            self.diabetes_diagnosis_date.setText(diag_date_text)
        self.symptom_blurred.setChecked(sym_blurred)
        self.symptom_floaters.setChecked(sym_floaters)
        self.symptom_flashes.setChecked(sym_flashes)
        self.symptom_vision_loss.setChecked(sym_vision_loss)
        self.symptom_other.setText(sym_other)
        
        # Restore Phase 1 additions
        self.height.setValue(height_v)
        self.weight.setValue(weight_v)
        self.bmi.setValue(bmi_v)
        self.treatment_regimen.setCurrentText(treatment_reg)
        self.prev_dr_stage.setCurrentText(prev_dr)
        self._current_screening_type = current_screening_type
        self._current_previous_screening_id = current_previous_screening_id
        self._current_follow_up_flag = current_follow_up_flag
        self._current_followup_date = current_followup_date
        self._current_followup_label = current_followup_label
        self._current_screening_group_id = current_screening_group_id

        # Pre-select the opposite eye for bilateral workflow continuity.
        self._set_eye_selection(opposite_eye)

        # Return to intake form — only the image needs to be uploaded
        self.stacked_widget.setCurrentIndex(0)

    def _save_screening_to_db(self, patient_data, screener_username: str, screener_name: str) -> tuple[bool, str, int | None]:
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO patient_records (
                    patient_id, name, birthdate, age, sex, contact, eyes,
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self._apply_dob_theme_style()

    def apply_theme(self, _theme: str):
        self._apply_dob_theme_style()

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

