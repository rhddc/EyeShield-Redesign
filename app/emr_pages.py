"""
EMR worklist — front desk registration, queue, and patient visit (Overview + screening history).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from datetime import date, datetime

from PySide6.QtCore import Qt, QDate, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QDoubleSpinBox,
)

try:
    from . import emr_service as emr
    from .ui_feedback import show_success, show_error, show_warning, confirm, loading_state, apply_dialog_style
except Exception:  # pragma: no cover
    import emr_service as emr
    from ui_feedback import show_success, show_error, show_warning, confirm, loading_state, apply_dialog_style
try:
    from .patient_record_groups import group_patient_record_rows
except Exception:
    from patient_record_groups import group_patient_record_rows

try:
    from .patient_timeline_dialog import PatientTimelineDialog
except Exception:
    from patient_timeline_dialog import PatientTimelineDialog

try:
    from .reports import ScreeningComparisonDialog
except Exception:
    from reports import ScreeningComparisonDialog

try:
    from .app_paths import PATIENT_RECORDS_DB_PATH
except Exception:
    from app_paths import PATIENT_RECORDS_DB_PATH

LEGACY_DB_FILE = str(PATIENT_RECORDS_DB_PATH)


# ---------------------------------------------------------------------------
# Visit status helpers (badge colours + human labels)
# ---------------------------------------------------------------------------

_STATUS_BADGE = {
    "waiting":      ("Waiting",      "#f59e0b", "#fef3c7"),
    "in_progress":  ("In Progress",  "#2563eb", "#dbeafe"),
    "completed":    ("Completed",    "#15803d", "#dcfce7"),
    "cancelled":    ("Cancelled",    "#b91c1c", "#fee2e2"),
}


def _status_label(status: str) -> str:
    return _STATUS_BADGE.get(str(status or "").lower(), (str(status or "-"),))[0]


def _status_item(status: str) -> "QTableWidgetItem":
    from PySide6.QtGui import QBrush, QColor
    label, fg, bg = _STATUS_BADGE.get(
        str(status or "").lower(), (str(status or "-"), "#334155", "#e2e8f0")
    )
    it = QTableWidgetItem(f"  {label}  ")
    it.setForeground(QBrush(QColor(fg)))
    it.setBackground(QBrush(QColor(bg)))
    it.setTextAlignment(Qt.AlignCenter)
    f = it.font()
    f.setBold(True)
    it.setFont(f)
    return it


def _label_style() -> str:
    return "font-size:11px;font-weight:600;color:#64748b;"


def _input_style() -> str:
    return (
        "QLineEdit,QComboBox,QTextEdit,QDateEdit,QDoubleSpinBox{"
        "background:#fff;border:1px solid #cbd5e1;border-radius:6px;padding:6px 8px;font-size:13px;}"
    )


def _fmt_queue_time(created_at) -> str:
    if not created_at:
        return "—"
    s = str(created_at).strip().replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            d = datetime.strptime(s, fmt)
            return d.strftime("%I:%M %p").replace(" 0", " ", 1)
        except ValueError:
            continue
    return "—"


class EmrToast(QLabel):
    """Local-only toast (top-right of parent widget)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setStyleSheet(
            "QLabel{background:#15803d;color:#fff;border-radius:8px;padding:10px 14px;font-weight:600;max-width:420px;}"
        )
        self.hide()
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.timeout.connect(self.hide)

    def show_text(self, text: str, ms: int = 4000) -> None:
        self.setText(text)
        self.adjustSize()
        if self.parent() is not None and self.parent().width() > 0:
            self.move(self.parent().width() - self.width() - 16, 12)
        self.raise_()
        self.show()
        self._t.start(ms)


class DiagnosisModal(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Diagnosis")
        self.setMinimumWidth(520)
        self.setStyleSheet("QDialog{background:#f8fafc;}" + _input_style())
        self.selected_eye_screened = "Left"
        self.paths: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        root.addWidget(QLabel("Step 1 — Eye selection", styleSheet=_label_style()))
        self.eye_group = QButtonGroup(self)
        self.rb_left = QRadioButton("Left Eye")
        self.rb_right = QRadioButton("Right Eye")
        self.rb_both = QRadioButton("Both Eyes")
        self.rb_left.setChecked(True)
        for i, rb in enumerate((self.rb_left, self.rb_right, self.rb_both)):
            self.eye_group.addButton(rb, i)
            root.addWidget(rb)
            rb.toggled.connect(self._sync_upload_visibility)

        root.addWidget(QLabel("Step 2 — Upload image(s)", styleSheet=_label_style()))
        self.left_row = self._build_upload_row("Left Eye")
        self.right_row = self._build_upload_row("Right Eye")
        root.addLayout(self.left_row["layout"])
        root.addLayout(self.right_row["layout"])
        self._sync_upload_visibility()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _build_upload_row(self, label: str) -> dict:
        wrap = QHBoxLayout()
        path = QLineEdit()
        path.setReadOnly(True)
        pick = QPushButton(f"Upload {label}")
        preview = QLabel("No image")
        preview.setFixedSize(84, 84)
        preview.setAlignment(Qt.AlignCenter)
        preview.setStyleSheet("QLabel{border:1px solid #cbd5e1;background:#fff;color:#94a3b8;}")

        def _choose():
            selected, _ = QFileDialog.getOpenFileName(
                self,
                f"Select {label} image",
                "",
                "Images (*.jpg *.jpeg *.png *.tif *.tiff);;All files (*.*)",
            )
            if not selected:
                return
            path.setText(selected)
            pix = QPixmap(selected)
            if not pix.isNull():
                preview.setPixmap(pix.scaled(preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        pick.clicked.connect(_choose)
        wrap.addWidget(QLabel(label, styleSheet=_label_style()))
        wrap.addWidget(path, 1)
        wrap.addWidget(pick)
        wrap.addWidget(preview)
        return {"layout": wrap, "path": path, "preview": preview}

    def _sync_upload_visibility(self) -> None:
        both = self.rb_both.isChecked()
        left_only = self.rb_left.isChecked()
        # left row always visible except when strictly right eye.
        for i in range(self.left_row["layout"].count()):
            w = self.left_row["layout"].itemAt(i).widget()
            if w:
                w.setVisible(both or left_only)
        for i in range(self.right_row["layout"].count()):
            w = self.right_row["layout"].itemAt(i).widget()
            if w:
                w.setVisible(both or self.rb_right.isChecked())

    def _validate_and_accept(self) -> None:
        if self.rb_both.isChecked():
            eye_screened = "Both"
        elif self.rb_right.isChecked():
            eye_screened = "Right"
        else:
            eye_screened = "Left"
        left = self.left_row["path"].text().strip()
        right = self.right_row["path"].text().strip()
        if eye_screened == "Left" and not left:
            QMessageBox.warning(self, "Diagnosis", "Left eye image is required.")
            return
        if eye_screened == "Right" and not right:
            QMessageBox.warning(self, "Diagnosis", "Right eye image is required.")
            return
        if eye_screened == "Both" and (not left or not right):
            QMessageBox.warning(self, "Diagnosis", "Both eye images are required.")
            return
        self.selected_eye_screened = eye_screened
        self.paths = {"Left": left, "Right": right}
        self.accept()


class ScreeningVerifyDialog(QDialog):
    def __init__(self, screening_id: int, username: str, parent=None):
        super().__init__(parent)
        self._screening_id = int(screening_id)
        self._username = username
        self._row_controls: list[dict] = []
        self.setWindowTitle(f"Verify Screening #{screening_id}")
        self.setMinimumSize(760, 520)
        self.setStyleSheet("QDialog{background:#f8fafc;}" + _input_style())

        root = QVBoxLayout(self)
        data = emr.get_screening(self._screening_id) or {}
        eyes = data.get("eyes") or []
        if not eyes:
            root.addWidget(QLabel("No eye records found."))
            return
        for eye in eyes:
            box = QGroupBox(f"{eye.get('eye_side', '')} Eye")
            bl = QVBoxLayout(box)
            ai = eye.get("ai_dr_grade")
            conf = eye.get("ai_confidence")
            unc = str(eye.get("uncertainty_status") or "pending")
            bl.addWidget(QLabel(f"AI grade: {ai if ai is not None else 'Pending'}   Confidence: {round((conf or 0)*100, 1) if conf is not None else '-'}%   Uncertainty: {unc}"))
            if unc == "rejected":
                warn = QLabel("AI confidence low - manual grading required")
                warn.setStyleSheet("color:#b91c1c;font-weight:700;")
                bl.addWidget(warn)

            media = QHBoxLayout()
            img_lbl = QLabel("No image")
            img_lbl.setFixedSize(220, 180)
            img_lbl.setAlignment(Qt.AlignCenter)
            img_lbl.setStyleSheet("QLabel{border:1px solid #cbd5e1;background:#fff;}")
            src_path = str(eye.get("fundus_image_path") or "")
            grad_path = str(eye.get("gradcam_image_path") or "")
            current_path = src_path if os.path.isfile(src_path) else ""
            if current_path:
                pix = QPixmap(current_path)
                if not pix.isNull():
                    img_lbl.setPixmap(pix.scaled(img_lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            toggle_btn = QPushButton("Show GradCAM")
            toggle_btn.setEnabled(bool(grad_path and os.path.isfile(grad_path)))

            def _toggle(label=img_lbl, src=src_path, grad=grad_path, btn=toggle_btn):
                showing_grad = btn.text().lower().startswith("show fundus")
                next_path = src if showing_grad else grad
                pix = QPixmap(next_path)
                if not pix.isNull():
                    label.setPixmap(pix.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                btn.setText("Show GradCAM" if showing_grad else "Show Fundus")

            toggle_btn.clicked.connect(_toggle)
            media.addWidget(img_lbl)
            media.addWidget(toggle_btn)
            media.addStretch()
            bl.addLayout(media)

            mode = QComboBox()
            mode.addItems(["Accept AI result", "Override"])
            grade = QComboBox()
            grade.addItems(["", "0", "1", "2", "3", "4"])
            if ai is not None:
                grade.setCurrentText(str(int(ai)))
            justification = QLineEdit()
            justification.setPlaceholderText("Required when overriding")
            notes = QLineEdit()
            notes.setPlaceholderText("Treatment notes (optional)")
            bl.addWidget(mode)
            bl.addWidget(QLabel("Final DR Grade", styleSheet=_label_style()))
            bl.addWidget(grade)
            bl.addWidget(QLabel("Override Justification", styleSheet=_label_style()))
            bl.addWidget(justification)
            bl.addWidget(QLabel("Treatment Notes", styleSheet=_label_style()))
            bl.addWidget(notes)
            root.addWidget(box)

            self._row_controls.append(
                {
                    "eye_id": int(eye["eye_id"]),
                    "ai_dr_grade": ai,
                    "mode": mode,
                    "grade": grade,
                    "justification": justification,
                    "notes": notes,
                }
            )

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        uid = emr.get_user_id(self._username)
        if not uid:
            QMessageBox.warning(self, "Verify", "Could not resolve doctor user.")
            return
        updates = []
        for row in self._row_controls:
            mode = row["mode"].currentText()
            grade_txt = row["grade"].currentText().strip()
            if not grade_txt:
                QMessageBox.warning(self, "Verify", "Please select final grade for each eye.")
                return
            accepted = 1 if mode.startswith("Accept") else 0
            justification = row["justification"].text().strip()
            if accepted == 0 and not justification:
                QMessageBox.warning(self, "Verify", "Override justification is required.")
                return
            updates.append(
                {
                    "eye_id": row["eye_id"],
                    "doctor_accepted_ai": accepted,
                    "final_dr_grade": int(grade_txt),
                    "override_justification": justification,
                    "final_treatment_notes": row["notes"].text().strip(),
                }
            )
        if emr.verify_screening(self._screening_id, uid, updates):
            self.accept()
        else:
            QMessageBox.warning(self, "Verify", "Unable to save screening verification.")


class PatientVisitDialog(QDialog):
    """Overview + screening history; start diagnosis or follow-up screening."""

    def __init__(
        self,
        parent,
        parent_app,
        *,
        patient_id: int,
        queue_id: int | None,
        username: str,
        can_clinical: bool,
        can_edit_overview: bool = False,
        actor_role: str = "",
        initial_tab: str = "overview",
        allow_proceed_to_diagnosis: bool = False,
        embedded_mode: bool = False,
    ):
        super().__init__(parent)
        self._patient_id = patient_id
        self._queue_id = queue_id
        self._username = username
        self._can_clinical = bool(can_clinical)
        self._can_edit_overview = bool(can_edit_overview)
        self._actor_role = str(actor_role or "").strip().lower()
        self._initial_tab = str(initial_tab or "overview").strip().lower()
        self._parent_app = parent_app
        self._allow_proceed = bool(allow_proceed_to_diagnosis)
        self._embedded_mode = bool(embedded_mode)
        self.proceed_to_diagnosis = False
        if self._embedded_mode:
            # Let this dialog behave like a regular widget when embedded in layouts.
            self.setWindowFlags(Qt.Widget)
        self.setWindowTitle("Patient visit")
        self.setMinimumSize(640, 520)
        self.setStyleSheet("QDialog{background:#f0f4f8;} QTableWidget{background:#ffffff;color:#334155;} QTableWidget::item{color:#334155;}" + _input_style())

        self._patient = emr.get_patient(patient_id) or {}
        self._history_rows: list[dict] = []
        self._legacy_rows: list[dict] = []
        code = self._patient.get("patient_code", "")
        name = f"{self._patient.get('first_name', '')} {self._patient.get('last_name', '')}".strip()
        self.setWindowTitle(f"Visit — {name} ({code})")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        self._header_info = QLabel("")
        self._header_info.setWordWrap(True)
        self._header_info.setStyleSheet(
            "QLabel{background:#e2e8f0;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:12px;font-weight:600;}"
        )
        root.addWidget(self._header_info)

        tabs = QTabWidget()
        overview = QWidget()
        ov_l = QVBoxLayout(overview)
        ov_l.setContentsMargins(8, 8, 8, 8)

        form = QFormLayout()
        self.f_last = QLineEdit(str(self._patient.get("last_name") or ""))
        self.f_first = QLineEdit(str(self._patient.get("first_name") or ""))
        self.f_dob = QDateEdit()
        self.f_dob.setCalendarPopup(True)
        self.f_dob.setDisplayFormat("yyyy-MM-dd")
        dstr = str(self._patient.get("date_of_birth") or "")[:10]
        qd = QDate.fromString(dstr, "yyyy-MM-dd")
        if qd.isValid():
            self.f_dob.setDate(qd)
        self.f_sex = QComboBox()
        self.f_sex.addItems(["", "Male", "Female", "Other"])
        sx = str(self._patient.get("sex") or "")
        if sx:
            i = self.f_sex.findText(sx)
            if i >= 0:
                self.f_sex.setCurrentIndex(i)
        self.f_contact = QLineEdit(str(self._patient.get("contact_number") or ""))
        self.f_email = QLineEdit(str(self._patient.get("email") or ""))
        self.f_address = QLineEdit(str(self._patient.get("address") or ""))
        self.f_height = QDoubleSpinBox()
        self.f_height.setRange(0, 300)
        self.f_height.setDecimals(1)
        self.f_height.setSuffix(" cm")
        self.f_weight = QDoubleSpinBox()
        self.f_weight.setRange(0, 500)
        self.f_weight.setDecimals(1)
        self.f_weight.setSuffix(" kg")
        h = self._patient.get("height_cm")
        w = self._patient.get("weight_kg")
        if h is not None:
            try:
                if float(h) > 0:
                    self.f_height.setValue(float(h))
            except (TypeError, ValueError):
                pass
        if w is not None:
            try:
                if float(w) > 0:
                    self.f_weight.setValue(float(w))
            except (TypeError, ValueError):
                pass
        self.f_dm = QLineEdit(str(self._patient.get("diabetes_type") or ""))
        self.f_hba1c = QDoubleSpinBox()
        self.f_hba1c.setRange(0, 20)
        self.f_hba1c.setDecimals(1)
        if self._patient.get("hba1c") is not None:
            try:
                self.f_hba1c.setValue(float(self._patient.get("hba1c")))
            except (TypeError, ValueError):
                pass
        self.f_meds = QLineEdit(str(self._patient.get("current_medications") or ""))
        self.f_allergies = QLineEdit(str(self._patient.get("known_allergies") or ""))
        self.f_other = QTextEdit()
        self.f_other.setMaximumHeight(72)
        self.f_other.setPlainText(str(self._patient.get("other_conditions") or ""))

        for wdg in (
            self.f_last,
            self.f_first,
            self.f_dob,
            self.f_sex,
            self.f_contact,
            self.f_email,
            self.f_address,
            self.f_height,
            self.f_weight,
            self.f_dm,
            self.f_hba1c,
            self.f_meds,
            self.f_allergies,
        ):
            wdg.setStyleSheet("")

        form.addRow(QLabel("Last name", styleSheet=_label_style()), self.f_last)
        form.addRow(QLabel("First name", styleSheet=_label_style()), self.f_first)
        form.addRow(QLabel("Date of birth", styleSheet=_label_style()), self.f_dob)
        form.addRow(QLabel("Sex", styleSheet=_label_style()), self.f_sex)
        form.addRow(QLabel("Contact", styleSheet=_label_style()), self.f_contact)
        form.addRow(QLabel("Email", styleSheet=_label_style()), self.f_email)
        form.addRow(QLabel("Address", styleSheet=_label_style()), self.f_address)
        form.addRow(QLabel("Height", styleSheet=_label_style()), self.f_height)
        form.addRow(QLabel("Weight", styleSheet=_label_style()), self.f_weight)
        form.addRow(QLabel("Diabetes type", styleSheet=_label_style()), self.f_dm)
        form.addRow(QLabel("HbA1c", styleSheet=_label_style()), self.f_hba1c)
        form.addRow(QLabel("Medications", styleSheet=_label_style()), self.f_meds)
        form.addRow(QLabel("Allergies", styleSheet=_label_style()), self.f_allergies)
        form.addRow(QLabel("Other conditions", styleSheet=_label_style()), self.f_other)
        ov_l.addLayout(form)

        self.btn_save = QPushButton("Save overview")
        self.btn_save.setStyleSheet(
            "QPushButton{background:#2563eb;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;}"
        )
        self.btn_save.clicked.connect(self._save_overview)
        self.btn_save.setEnabled(False)
        self.btn_edit = QPushButton("Edit")
        self.btn_edit.setStyleSheet(
            "QPushButton{background:#0f766e;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;}"
        )
        self.btn_edit.clicked.connect(self._start_edit_overview)
        self.btn_edit.setEnabled(self._can_edit_overview)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_edit)
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        ov_l.addLayout(btn_row)

        hist = QWidget()
        h_l = QVBoxLayout(hist)
        h_l.setContentsMargins(8, 8, 8, 8)
        self.table_hist = QTableWidget(0, 6)
        self.table_hist.setHorizontalHeaderLabels(["ID", "Type", "Date", "Eyes", "Status", "Final DR / Eye"])
        self.table_hist.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_hist.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_hist.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_hist.itemSelectionChanged.connect(self._show_selected_screening_detail)
        h_l.addWidget(self.table_hist)
        self.hist_empty_label = QLabel("No screenings on record.")
        self.hist_empty_label.setStyleSheet("color:#64748b;font-size:12px;")
        h_l.addWidget(self.hist_empty_label)
        self.hist_detail_label = QLabel("")
        self.hist_detail_label.setWordWrap(True)
        self.hist_detail_label.setStyleSheet("color:#334155;background:#eef2f7;border:1px solid #cbd5e1;border-radius:6px;padding:8px;")
        h_l.addWidget(self.hist_detail_label)

        legacy = QWidget()
        lg_l = QVBoxLayout(legacy)
        lg_l.setContentsMargins(8, 8, 8, 8)
        self.table_legacy = QTableWidget(0, 5)
        self.table_legacy.setHorizontalHeaderLabels(["Record ID", "Date", "Eye(s)", "Result", "Final (ICDR)"])
        self.table_legacy.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_legacy.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_legacy.setSelectionBehavior(QAbstractItemView.SelectRows)
        lg_l.addWidget(self.table_legacy)
        self.legacy_empty_label = QLabel("No legacy screenings on record.")
        self.legacy_empty_label.setStyleSheet("color:#64748b;font-size:12px;")
        lg_l.addWidget(self.legacy_empty_label)

        action_row = QHBoxLayout()
        self.btn_start = QPushButton()
        self._refresh_action_button_label()
        self.btn_start.setStyleSheet(
            "QPushButton{background:#059669;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;}"
        )
        self.btn_start.setEnabled(self._can_clinical)
        if not self._can_clinical:
            self.btn_start.setToolTip("Sign in as a doctor or clinician to start diagnosis.")
        self.btn_start.clicked.connect(self._on_start_screening)
        self.btn_verify = QPushButton("Verify Selected Screening")
        self.btn_verify.setStyleSheet(
            "QPushButton{background:#7c3aed;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;}"
        )
        self.btn_verify.clicked.connect(self._open_verify_dialog)
        self.btn_verify.setEnabled(False)
        self.btn_done = QPushButton("Mark visit completed")
        self.btn_done.setStyleSheet(
            "QPushButton{background:#334155;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;}"
        )
        self.btn_done.clicked.connect(self._mark_completed)
        self.btn_done.setEnabled(self._can_clinical and queue_id is not None)
        action_row.addWidget(self.btn_start)
        action_row.addWidget(self.btn_verify)
        action_row.addWidget(self.btn_done)
        action_row.addStretch()
        h_l.addLayout(action_row)

        tabs.addTab(overview, "Overview")
        tabs.addTab(hist, "Screening History")
        tabs.addTab(legacy, "Legacy History")
        self._tabs = tabs
        tabs.setCurrentIndex(1 if self._initial_tab == "history" else 0)
        root.addWidget(tabs)

        close_row = QHBoxLayout()
        close_row.addStretch()
        if self._allow_proceed and self._can_clinical:
            b_proceed = QPushButton("Proceed to diagnosis")
            b_proceed.setStyleSheet(
                "QPushButton{background:#059669;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:700;}"
            )
            b_proceed.clicked.connect(self._on_proceed)
            close_row.addWidget(b_proceed)
        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        self.btn_close = b
        self._close_row = close_row
        close_row.addWidget(b)
        root.addLayout(close_row)

        self._load_history()
        self._load_legacy_history()
        self._set_overview_editable(False)
        self._refresh_header_info()
        if not self._can_edit_overview:
            for wdg in (
                self.f_last,
                self.f_first,
                self.f_dob,
                self.f_sex,
                self.f_contact,
                self.f_email,
                self.f_address,
                self.f_height,
                self.f_weight,
                self.f_dm,
                self.f_hba1c,
                self.f_meds,
                self.f_allergies,
                self.f_other,
            ):
                wdg.setReadOnly(True)
            self.f_other.setReadOnly(True)
            self.btn_edit.setEnabled(False)
            self.btn_save.setEnabled(False)

    def _refresh_action_button_label(self) -> None:
        n = emr.count_screenings_for_patient(self._patient_id)
        if n == 0:
            self.btn_start.setText("Start Diagnosis")
        else:
            self.btn_start.setText("New Follow-up Screening")

    def _save_overview(self) -> None:
        uid = emr.get_user_id(self._username)
        if not uid:
            QMessageBox.warning(self, "Save", "Could not resolve current user.")
            return
        dob = self.f_dob.date()
        if not dob.isValid():
            QMessageBox.warning(self, "Save", "Invalid date of birth.")
            return
        fields = {
            "last_name": self.f_last.text().strip(),
            "first_name": self.f_first.text().strip(),
            "date_of_birth": dob.toString("yyyy-MM-dd"),
            "sex": self.f_sex.currentText() or None,
            "contact_number": self.f_contact.text().strip() or None,
            "email": self.f_email.text().strip() or None,
            "address": self.f_address.text().strip() or None,
            "height_cm": self.f_height.value() or None,
            "weight_kg": self.f_weight.value() or None,
            "diabetes_type": self.f_dm.text().strip() or None,
            "hba1c": self.f_hba1c.value() or None,
            "current_medications": self.f_meds.text().strip() or None,
            "known_allergies": self.f_allergies.text().strip() or None,
            "other_conditions": self.f_other.toPlainText().strip() or None,
        }
        if not confirm(
            self,
            "Save changes?",
            "Update this patient's profile with the new values?",
            yes_text="Save",
            no_text="Cancel",
        ):
            return
        action = "DOCTOR_UPDATE_PATIENT" if self._actor_role in {"clinician", "doctor"} else "UPDATE_PATIENT"
        with loading_state([self.btn_save], loading_text="Saving…"):
            ok_set = emr.update_patient_fields(
                self._patient_id, fields, uid, action=action, target_type="patient"
            )
        if ok_set:
            self._patient = emr.get_patient(self._patient_id) or {}
            self._refresh_header_info()
            self._set_overview_editable(False)
            show_success(self, "Saved", "Patient information has been updated.")
        else:
            show_warning(self, "Save", "Nothing changed or the update could not be applied.")

    def _start_edit_overview(self) -> None:
        if not self._can_edit_overview:
            return
        self._set_overview_editable(True)

    def _set_overview_editable(self, editable: bool) -> None:
        for wdg in (
            self.f_last,
            self.f_first,
            self.f_dob,
            self.f_sex,
            self.f_contact,
            self.f_email,
            self.f_address,
            self.f_height,
            self.f_weight,
            self.f_dm,
            self.f_hba1c,
            self.f_meds,
            self.f_allergies,
            self.f_other,
        ):
            if hasattr(wdg, "setReadOnly"):
                wdg.setReadOnly(not editable)
            if hasattr(wdg, "setEnabled") and not isinstance(wdg, QTextEdit):
                wdg.setEnabled(editable)
        self.f_other.setReadOnly(not editable)
        self.btn_save.setEnabled(editable and self._can_edit_overview)
        self.btn_edit.setEnabled((not editable) and self._can_edit_overview)

    @staticmethod
    def _compute_age(dob_iso: str) -> str:
        dob = str(dob_iso or "").strip()[:10]
        if not dob:
            return "-"
        try:
            born = datetime.strptime(dob, "%Y-%m-%d").date()
        except ValueError:
            return "-"
        today = date.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return str(max(age, 0))

    def _resolve_queue_number_for_header(self) -> str:
        if self._queue_id:
            row = emr.get_queue_entry(self._queue_id)
            if row and str(row.get("queue_number") or "").strip():
                return str(row.get("queue_number"))
        today_row = emr.get_today_queue_for_patient(self._patient_id)
        if today_row and str(today_row.get("queue_number") or "").strip():
            return str(today_row.get("queue_number"))
        return "-"

    def _refresh_header_info(self) -> None:
        p = emr.get_patient(self._patient_id) or self._patient or {}
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "Unknown"
        code = str(p.get("patient_code") or "-")
        sex = str(p.get("sex") or "-")
        age = self._compute_age(str(p.get("date_of_birth") or ""))
        qn = self._resolve_queue_number_for_header()
        self._header_info.setText(
            f"Patient: {name}    Code: {code}    Age: {age}    Sex: {sex}    Today's Queue: {qn}"
        )

    def _load_history(self) -> None:
        self._history_rows = emr.list_screenings_for_patient(self._patient_id)
        # Hide unfinished visits from "Screening History" (they belong to the active visit UI, not history).
        rows = [
            r for r in (self._history_rows or [])
            if str(r.get("session_status") or "").strip().lower() not in {"pending", "in_progress"}
        ]
        self.table_hist.setRowCount(len(rows))
        for i, r in enumerate(rows):
            type_label = "Initial" if str(r.get("screening_type") or "").lower() == "initial" else "Follow-up"
            self.table_hist.setItem(i, 0, QTableWidgetItem(str(r.get("screening_id", ""))))
            self.table_hist.setItem(i, 1, QTableWidgetItem(type_label))
            self.table_hist.setItem(i, 2, QTableWidgetItem(str(r.get("screening_date", ""))))
            self.table_hist.setItem(i, 3, QTableWidgetItem(str(r.get("eye_screened", ""))))
            self.table_hist.setItem(i, 4, QTableWidgetItem(str(r.get("session_status", ""))))
            self.table_hist.setItem(i, 5, QTableWidgetItem(str(r.get("final_grade_summary", "Pending"))))
        has_rows = bool(rows)
        self.hist_empty_label.setVisible(not has_rows)
        self.hist_detail_label.setVisible(has_rows)
        self.btn_verify.setEnabled(has_rows and self._can_clinical)
        self._refresh_action_button_label()
        if has_rows:
            self.table_hist.selectRow(0)
            self._show_selected_screening_detail()
        else:
            self.hist_detail_label.setText("No per-eye details available yet.")

    def _load_legacy_history(self) -> None:
        code = str((self._patient or {}).get("patient_code") or "").strip()
        if not code:
            self.table_legacy.setRowCount(0)
            self.legacy_empty_label.setVisible(True)
            return

        rows: list[dict] = []
        conn = None
        try:
            conn = sqlite3.connect(LEGACY_DB_FILE)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, screened_at, eyes, result, final_diagnosis_icdr
                FROM patient_records
                WHERE patient_id = ?
                ORDER BY screened_at ASC, id ASC
                """,
                (code,),
            )
            for r in cur.fetchall():
                rows.append(
                    {
                        "id": r[0],
                        "screened_at": r[1],
                        "eyes": r[2],
                        "result": r[3],
                        "final_diagnosis_icdr": r[4],
                    }
                )
        except Exception:
            rows = []
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

        self._legacy_rows = rows
        self.table_legacy.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table_legacy.setItem(i, 0, QTableWidgetItem(str(r.get("id", ""))))
            self.table_legacy.setItem(i, 1, QTableWidgetItem(str(r.get("screened_at", ""))))
            self.table_legacy.setItem(i, 2, QTableWidgetItem(str(r.get("eyes", ""))))
            self.table_legacy.setItem(i, 3, QTableWidgetItem(str(r.get("result", ""))))
            self.table_legacy.setItem(i, 4, QTableWidgetItem(str(r.get("final_diagnosis_icdr", "")) or ""))
        self.legacy_empty_label.setVisible(not bool(rows))

    def _on_proceed(self) -> None:
        self.proceed_to_diagnosis = True
        self.accept()

    def _on_start_screening(self) -> None:
        if not self._can_clinical or not self._parent_app:
            return

        # Formal confirmation based on patient history
        n = emr.count_screenings_for_patient(self._patient_id)
        if n == 0:
            msg = "No prior screening records were found for this patient. You will be directed to the diagnosis form for initial entry."
        else:
            msg = "Existing screening records were found for this patient. Previous clinical history and results will be presented for your review before starting the new session."

        if not confirm(self, "Confirm Diagnosis Action", msg, yes_text="Continue", no_text="Cancel"):
            return

        uid = emr.get_user_id(self._username)
        if not uid:
            show_error(self, "Diagnosis", "Could not resolve doctor user id. Please sign in again.")
            return

        # Guard rail #1: must have an open visit (waiting or in-progress) for this patient today.
        active_q = emr.get_today_active_queue_for_patient(self._patient_id)
        active_qid = int(active_q["queue_id"]) if active_q else None
        ok, reason = emr.can_start_screening(self._patient_id, active_qid)
        if not ok:
            show_warning(self, "Cannot start diagnosis", reason)
            return

        # Guard rail #2: if the visit already has a screening session, confirm before creating another
        # (skip when the last session is redo-safe, e.g. rejected_all).
        existing = emr.latest_visit_screening(active_qid) if active_qid else None
        if existing and emr.should_prompt_before_new_visit_screening(existing):
            if not confirm(
                self,
                "Visit already has a screening",
                (
                    f"This visit already has screening #{existing['screening_id']} "
                    f"(status: {existing.get('session_status')}).\n\n"
                    "Create an additional screening under the same visit?"
                ),
                yes_text="Create another",
                no_text="Cancel",
            ):
                return

        # Ensure the visit moves to in-progress before data entry (doctor is actively working on it).
        if active_qid:
            ip_ok, ip_reason = emr.mark_visit_in_progress(active_qid, uid)
            if not ip_ok and ip_reason:
                # Not fatal, but surface it so the doctor knows why status didn't change.
                show_warning(self, "Visit status", ip_reason)

        modal = DiagnosisModal(self)
        if modal.exec() != QDialog.DialogCode.Accepted:
            return
        eye = modal.selected_eye_screened
        paths = modal.paths
        n = emr.count_screenings_for_patient(self._patient_id)
        stype = "initial" if n == 0 else "follow_up"

        # Final destructive confirmation — disk write + AI pipeline kick-off.
        if not confirm(
            self,
            "Confirm diagnosis",
            (
                f"Start a {stype.replace('_', ' ')} screening for "
                f"{self._patient.get('first_name', '')} {self._patient.get('last_name', '')} "
                f"({eye} eye).\n\n"
                "Images will be stored and the AI pipeline will run. Continue?"
            ),
            yes_text="Start diagnosis",
            no_text="Cancel",
        ):
            return

        try:
            with loading_state([self.btn_start], loading_text="Starting…"):
                sid = emr.create_screening_session(
                    self._patient_id,
                    active_qid,
                    uid,
                    stype,
                    eye,
                    paths,
                )
        except Exception as err:
            show_error(self, "Diagnosis failed", f"Could not create screening: {err}")
            return

        self._patient = emr.get_patient(self._patient_id) or {}
        self._load_history()
        if hasattr(self._parent_app, "screening_page"):
            first_path = paths.get("Left") or paths.get("Right") or ""
            self._parent_app.screening_page.apply_emr_context(
                self._patient,
                screening_id=sid,
                queue_entry_id=active_qid,
                fundus_path=first_path,
                eye_screened=eye,
            )
        show_success(
            self,
            "Screening started",
            (
                f"Screening #{sid} is now pending. The AI pipeline is queued and will populate "
                "results shortly. Verify per eye when ready, then mark the visit completed."
            ),
        )

    def _show_selected_screening_detail(self) -> None:
        row = self.table_hist.currentRow()
        if row < 0 or row >= len(self._history_rows):
            self.hist_detail_label.setText("Select a screening to view per-eye details.")
            return
        selected = self._history_rows[row]
        lines = []
        for eye in selected.get("eyes", []):
            ai = eye.get("ai_dr_grade")
            conf = eye.get("ai_confidence")
            conf_text = f"{round(float(conf) * 100, 1)}%" if conf is not None else "-"
            lines.append(
                f"{eye.get('eye_side','?')} Eye | AI: {ai if ai is not None else 'Pending'} ({conf_text}) | "
                f"Uncertainty: {eye.get('uncertainty_status','pending')} | "
                f"Final: {eye.get('final_dr_grade') if eye.get('final_dr_grade') is not None else 'Pending'} | "
                f"Notes: {eye.get('final_treatment_notes') or '-'}"
            )
        self.hist_detail_label.setText("\n".join(lines) if lines else "No per-eye details available yet.")

    def _open_verify_dialog(self) -> None:
        if not self._can_clinical:
            return
        row = self.table_hist.currentRow()
        if row < 0 or row >= len(self._history_rows):
            QMessageBox.information(self, "Verify", "Select a screening record first.")
            return
        screening_id = int(self._history_rows[row]["screening_id"])
        dlg = ScreeningVerifyDialog(screening_id, self._username, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_history()

    def _mark_completed(self) -> None:
        if not self._queue_id:
            show_warning(self, "Visit", "This patient record is not attached to a visit.")
            return
        uid = emr.get_user_id(self._username)
        if not uid:
            show_error(self, "Visit", "Could not resolve current user.")
            return
        ok, reason = emr.can_complete_visit(self._queue_id)
        if not ok:
            show_warning(self, "Cannot complete visit", reason)
            return
        if not confirm(
            self,
            "Mark visit completed",
            "This visit will be closed and removed from the active queue. Continue?",
            yes_text="Mark completed",
            no_text="Cancel",
        ):
            return
        with loading_state([self.btn_done], loading_text="Saving…"):
            ok_set = emr.set_queue_status(self._queue_id, "completed", uid)
        if ok_set:
            show_success(self, "Visit completed", "The visit has been closed.")
            self.accept()
        else:
            show_error(self, "Visit", "Could not update queue status. Please try again.")


class EmrVisitsPage(QWidget):
    """Queue-only visits page; intake happens on Assessment."""

    def __init__(self, parent_app):
        super().__init__()
        self._app = parent_app
        self._username = getattr(parent_app, "username", "") or ""
        self._role = str(getattr(parent_app, "role", "") or "").lower()
        self._selected_search_patient_id: int | None = None
        self._last_refresh_epoch: float = time.time()
        self._build()
        self._toast = EmrToast(self)
        self._au = QTimer(self)
        self._au.timeout.connect(self.refresh)
        if self._is_front():
            self._au.start(30_000)
        elif self._is_clinical():
            self._au.start(60_000)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_toast") and self._toast and self._toast.isVisible() and self.width() > 0:
            self._toast.move(self.width() - self._toast.width() - 16, 12)

    def _is_front(self) -> bool:
        return self._role in ("admin", "frontdesk")

    def _is_clinical(self) -> bool:
        return self._role in ("admin", "doctor", "clinician")

    @staticmethod
    def _compute_age(dob_iso: str) -> str:
        dob = str(dob_iso or "").strip()[:10]
        if not dob:
            return "-"
        try:
            born = datetime.strptime(dob, "%Y-%m-%d").date()
        except ValueError:
            return "-"
        today = date.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return str(max(age, 0))

    @staticmethod
    def _queue_with_timestamp(queue_number: str, created_at: str) -> str:
        q = str(queue_number or "").strip() or "-"
        c = str(created_at or "").strip()
        if not c:
            return q
        c_norm = c.replace("T", " ")
        if "." in c_norm:
            c_norm = c_norm.split(".", 1)[0]
        stamp = c_norm[:16]
        return f"{q} ({stamp})"

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.setStyleSheet(
            "QWidget#emrRoot{background:#f8fafc;}"
            + _input_style()
            + """
            QWidget {
                background: #f8fafc;
                color: #0f172a;
                font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            }
            QTableWidget#queueTable {
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
                padding: 12px 10px;
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
            QFrame#queueHeaderCard, QFrame#queueTableCard {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
            QLabel#queueTitle {
                font-size: 20px;
                font-weight: 400;
                color: #1d4ed8;
            }
            QLabel#queueSubtitle {
                font-size: 13px;
                color: #64748b;
            }
            QLineEdit#queueSearch {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 14px;
            }
            QLineEdit#queueSearch:focus {
                border: 2px solid #3b82f6;
                padding: 7px 13px;
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
            QPushButton:disabled {
                background: #f8fafc;
                color: #94a3b8;
                border: 1px solid #f1f5f9;
            }
            """
        )
        self.setObjectName("emrRoot")

        self._emr_stack = QStackedWidget()
        layout.addWidget(self._emr_stack, 1)

        queue_page = QWidget()
        qp_centering = QHBoxLayout(queue_page)
        qp_centering.setContentsMargins(0, 0, 0, 0)
        qp_centering.addStretch(1)

        page_container = QWidget()
        page_container.setMinimumWidth(1200)
        page_container.setMaximumWidth(1200)
        qp_centering.addWidget(page_container)
        qp_centering.addStretch(1)

        qp_layout = QVBoxLayout(page_container)
        qp_layout.setContentsMargins(32, 32, 32, 32)
        qp_layout.setSpacing(16)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content_l = QVBoxLayout(content)
        content_l.setContentsMargins(0, 0, 0, 0)
        content_l.setSpacing(10)
        qp_layout.addWidget(content, 1)

        # header_card will be added inside queue_list_page instead of here to allow hiding it during review.

        # ── Table card ───────────────────────────────────────────────────────
        table_card = QFrame()
        table_card.setObjectName("queueTableCard")
        results_layout = QVBoxLayout(table_card)
        results_layout.setContentsMargins(12, 12, 12, 10)
        results_layout.setSpacing(8)

        # Columns:
        # 0 Name | 1 Age | 2 Sex | 3 Purpose | 4 Queue Number | 5 queue_id(hidden) | 6 Action (clinical)
        _ncol = 7 if self._is_clinical() else 6
        self.table = QTableWidget(0, _ncol)
        self.table.setObjectName("queueTable")
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        headers = ["Name", "Age", "Sex", "Purpose", "Queue Number", "queue_id"]
        if self._is_clinical():
            headers.append("Actions")
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._on_row_activated)
        self.table.setColumnHidden(5, True)
        self.table.setFocusPolicy(Qt.NoFocus)

        # Column sizing (aesthetic + scannable)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(60)
        
        # Name column stretches to fill available space
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        # Other info columns fit their contents
        for col in (1, 2, 3, 4):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
            
        if self._is_clinical():
            # Actions column is fixed width to prevent clipping
            hh.setSectionResizeMode(6, QHeaderView.Fixed)
            self.table.setColumnWidth(6, 260)
            
        results_layout.addWidget(self.table)

        self._queue_stack = QStackedWidget()
        content_l.addWidget(self._queue_stack, 1)

        queue_list_page = QWidget()
        qlp = QVBoxLayout(queue_list_page)
        qlp.setContentsMargins(0, 0, 0, 0)
        qlp.setSpacing(10)

        # ── Header card (Relocated here to hide it during review) ────────────
        self.header_card = QFrame()
        self.header_card.setObjectName("queueHeaderCard")
        header_l = QVBoxLayout(self.header_card)
        header_l.setContentsMargins(14, 12, 14, 12)
        header_l.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("Patient Queue")
        title.setObjectName("queueTitle")
        title_col.addWidget(title)
        title_row.addLayout(title_col, 1)
        header_l.addLayout(title_row)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        self.queue_search = QLineEdit()
        self.queue_search.setObjectName("queueSearch")
        self.queue_search.setPlaceholderText("Search name, queue number, sex…")
        self.queue_search.setMinimumHeight(36)
        self.queue_search.textChanged.connect(self.refresh)
        controls_row.addWidget(self.queue_search, 1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setObjectName("queueBtnSecondary")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh)
        controls_row.addWidget(self.btn_refresh, 0)

        # Front desk actions
        self.btn_new_patient_visit = QPushButton("+ New patient / visit")
        self.btn_new_patient_visit.setObjectName("queueBtnPrimary")
        self.btn_new_patient_visit.setCursor(Qt.PointingHandCursor)
        self.btn_new_patient_visit.clicked.connect(self._go_to_new_patient_intake)
        self.btn_new_patient_visit.setVisible(self._is_front())
        controls_row.addWidget(self.btn_new_patient_visit, 0)

        self.btn_cancel_visit = QPushButton("Cancel visit")
        self.btn_cancel_visit.setObjectName("queueBtnDanger")
        self.btn_cancel_visit.setCursor(Qt.PointingHandCursor)
        self.btn_cancel_visit.clicked.connect(self._cancel_selected_visit)
        self.btn_cancel_visit.setVisible(self._is_front())
        controls_row.addWidget(self.btn_cancel_visit, 0)

        self.btn_clear_queue = QPushButton("Clear today’s queue")
        self.btn_clear_queue.setObjectName("queueBtnSecondary")
        self.btn_clear_queue.setCursor(Qt.PointingHandCursor)
        self.btn_clear_queue.clicked.connect(self._clear_today_queue)
        self.btn_clear_queue.setVisible(self._is_front() or self._is_clinical())
        controls_row.addWidget(self.btn_clear_queue, 0)

        header_l.addLayout(controls_row)
        qlp.addWidget(self.header_card)
        qlp.addWidget(table_card, 1)

        self._queue_stack.addWidget(queue_list_page)  # 0

        review_page = QWidget()
        review_page.setObjectName("EMRReviewPage")
        review_page.setStyleSheet("QWidget#EMRReviewPage { background-color: #f1f5f9; }")
        rlp = QVBoxLayout(review_page)
        rlp.setContentsMargins(0, 0, 0, 0)
        rlp.setSpacing(0)
        self._review_host = QWidget()
        self._review_host.setObjectName("EMRReviewHost")
        self._review_host.setStyleSheet("QWidget#EMRReviewHost { background-color: #f1f5f9; }")
        self._review_host_layout = QVBoxLayout(self._review_host)
        self._review_host_layout.setContentsMargins(0, 0, 0, 0)
        rlp.addWidget(self._review_host, 1)
        self._queue_stack.addWidget(review_page)  # 1

        self._review_ctx = {"qid": None, "pid": None}

        self._emr_stack.addWidget(queue_page)

        diagnosis_page = QWidget()
        dp_layout = QVBoxLayout(diagnosis_page)
        dp_layout.setContentsMargins(0, 0, 0, 0)
        dp_layout.setSpacing(0)

        # Removed the redundant back button row here as it's now handled inside the diagnosis form or via the form's header.
        self._diagnosis_host = QWidget()
        self._diagnosis_layout = QVBoxLayout(self._diagnosis_host)
        self._diagnosis_layout.setContentsMargins(0, 0, 0, 0)
        dp_layout.addWidget(self._diagnosis_host, 1)
        self._emr_stack.addWidget(diagnosis_page)

    def refresh(self) -> None:
        self._last_refresh_epoch = time.time()
        today = date.today().isoformat()
        all_rows = emr.list_queue_rows(today)
        wait_n = sum(1 for r in all_rows if str(r.get("status") or "").strip().lower() == "waiting")
        active_rows = [
            r for r in all_rows if str(r.get("status") or "").strip().lower() in {"waiting", "in_progress"}
        ]
        rows = active_rows
        search_term = str(getattr(self, "queue_search", QLineEdit()).text() if hasattr(self, "queue_search") else "").strip().lower()
        if search_term:
            rows = [
                r for r in rows
                if search_term in f"{r.get('first_name','')} {r.get('last_name','')}".lower()
                or search_term in str(r.get("queue_number", "")).lower()
                or search_term in str(r.get("sex", "")).lower()
            ]
        rows.sort(
            key=lambda r: (
                int(str(r.get("queue_number", "")).split("-")[-1]) if str(r.get("queue_number", "")).split("-")[-1].isdigit() else 999999,
                int(r.get("queue_id") or 0),
            )
        )
        _ = wait_n  # computed for internal checks/analytics; no UI chip shown
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip()
            age = self._compute_age(str(r.get("date_of_birth", "") or ""))
            sex = str(r.get("sex", "") or "-")
            purpose_raw = str(r.get("screening_purpose") or "new").strip().lower()
            purpose = "Follow-up" if purpose_raw == "follow_up" else "New"
            qlabel = str(r.get("queue_number") or "").strip() or "-"
            it0 = QTableWidgetItem(name)
            it0.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 0, it0)
            it1 = QTableWidgetItem(age)
            it1.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 1, it1)
            it2 = QTableWidgetItem(sex)
            it2.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 2, it2)
            it3 = QTableWidgetItem(purpose)
            it3.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 3, it3)
            it4 = QTableWidgetItem(qlabel)
            it4.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 4, it4)
            it5 = QTableWidgetItem(str(r.get("queue_id", "")))
            it5.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 5, it5)
            if self._is_clinical() and self.table.columnCount() > 6:
                qid = r.get("queue_id")
                pid = r.get("patient_id")
                btn_text = "Start Follow-up" if purpose == "Follow-up" else "Start diagnosis"
                btn = QPushButton(btn_text)
                btn.setObjectName("queueBtnPrimary")
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2563eb;
                        color: #ffffff;
                        border: 1px solid #1d4ed8;
                        border-radius: 10px;
                        font-weight: 700;
                        font-size: 13px;
                        padding: 8px 20px;
                        min-width: 160px;
                    }
                    QPushButton:hover {
                        background-color: #1d4ed8;
                        border-color: #1e40af;
                    }
                """)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(
                    lambda checked=False, q=qid, p=pid: self._on_start_diagnosis_clicked(q, p)
                )
                
                # Container to center the button in the cell
                wrap = QWidget()
                wrap.setStyleSheet("background: transparent; border: none;")
                wl = QHBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 0, 0)
                wl.setSpacing(0)
                wl.addStretch(1)
                wl.addWidget(btn)
                wl.addStretch(1)
                
                self.table.setCellWidget(i, 6, wrap)

        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, 64)

    def _selected_queue_and_patient(self) -> tuple[int | None, int | None]:
        r = self.table.currentRow()
        if r < 0:
            return None, None
        try:
            qid = int(self.table.item(r, 5).text())
        except (TypeError, ValueError, AttributeError):
            qid = None
        # patient from row data — re-query list (unfiltered) by queue id
        today = date.today().isoformat()
        rows = emr.list_queue_rows(today)
        for row in rows:
            if row.get("queue_id") == qid:
                return qid, int(row["patient_id"])
        return qid, None

    def _on_row_activated(self) -> None:
        if self._is_clinical():
            self._open_selected()

    def _prepare_clinical_visit_open(self, qid: int | None, pid: int) -> bool:
        if not self._is_clinical():
            return True
        uid = emr.get_user_id(self._username)
        if qid and uid:
            ok, reason = emr.mark_visit_in_progress(qid, uid)
            if not ok and reason:
                # Hard stop: completed/cancelled visits cannot be reopened for diagnosis.
                show_warning(self, "Visit status", reason)
                return False
            emr.log_open_patient_record(uid, qid, int(pid))
        return True

    def _embed_screening_in_emr_tab(self) -> None:
        if not self._app or not hasattr(self._app, "screening_page") or not hasattr(self._app, "pages"):
            return
        sp = self._app.screening_page
        pages = self._app.pages
        if pages.indexOf(sp) >= 0:
            pages.removeWidget(sp)
        self._diagnosis_layout.removeWidget(sp)
        self._diagnosis_layout.addWidget(sp)
        self._emr_stack.setCurrentIndex(1)

    def _embed_doctor_diagnosis_form(self) -> None:
        if not hasattr(self, "_diagnosis_layout"):
            return
        form = getattr(self, "_doctor_diagnosis_form", None)
        if form is None:
            try:
                from .doctor_diagnosis_form import DoctorDiagnosisForm
            except Exception:
                from doctor_diagnosis_form import DoctorDiagnosisForm
            form = DoctorDiagnosisForm(self)
            form.screening_history_requested.connect(self._open_saved_patient_screening_history)
            form.back_requested.connect(self._on_back_from_diagnosis)
            if hasattr(form, "patient_record_requested"):
                form.patient_record_requested.connect(self._open_patient_record_from_code)
            self._doctor_diagnosis_form = form
        # Provide session context to the form (used for editing patient info and permissions).
        if hasattr(form, "username"):
            form.username = str(self._username or "")
        if hasattr(form, "role"):
            form.role = str(self._role or "")
        if hasattr(self._app, "display_name") and hasattr(form, "display_name"):
            form.display_name = str(getattr(self._app, "display_name", "") or "")
        # Ensure only our form is inside the diagnosis host.
        while self._diagnosis_layout.count():
            item = self._diagnosis_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None and w is not form:
                w.setParent(None)
        if form.parentWidget() is not getattr(self, "_diagnosis_host", None):
            form.setParent(getattr(self, "_diagnosis_host", None))
        if self._diagnosis_layout.indexOf(form) < 0:
            self._diagnosis_layout.addWidget(form)
        self._emr_stack.setCurrentIndex(1)

    def _show_queue_page(self, *, refresh: bool = True) -> None:
        # Inner stack: queue table vs follow-up review overlay — always land on the table.
        if hasattr(self, "_queue_stack"):
            self._queue_stack.setCurrentIndex(0)
        # Outer stack: queue list vs embedded diagnosis — leaving diagnosis must restore the table.
        if hasattr(self, "_emr_stack"):
            self._emr_stack.setCurrentIndex(0)
        if refresh:
            self.refresh()

    def _show_review_list(self, *, refresh: bool = True) -> None:
        if hasattr(self, "_queue_stack"):
            self._queue_stack.setCurrentIndex(0)
        if refresh:
            self.refresh()

    def release_screening_to_main_stack_if_embedded(self) -> None:
        """Return the screening widget to the main QStackedWidget if it was embedded in this tab."""
        if not self._app or not hasattr(self._app, "screening_page") or not hasattr(self._app, "pages"):
            return
        sp = self._app.screening_page
        if sp.parentWidget() is not getattr(self, "_diagnosis_host", None):
            return
        self._diagnosis_layout.removeWidget(sp)
        sp.setParent(None)
        pages = self._app.pages
        if pages.indexOf(sp) < 0:
            pages.insertWidget(1, sp)
        self._show_queue_page()

    def _on_back_from_diagnosis(self) -> None:
        form = getattr(self, "_doctor_diagnosis_form", None)
        if form is not None and hasattr(form, "is_busy") and form.is_busy():
            show_warning(self, "Screening In Progress", "Please wait for image analysis to finish before leaving.")
            return

        if not confirm(self, "Return to Patient Queue", "Are you sure you want to go back to the list?"):
            return

        # If the global screening page is embedded, release it; otherwise just navigate back.
        if self._app and hasattr(self._app, "screening_page"):
            sp = self._app.screening_page
            if sp.parentWidget() is getattr(self, "_diagnosis_host", None):
                self.release_screening_to_main_stack_if_embedded()
                return
        self._show_queue_page()

    def _open_patient_record_from_code(self, patient_code: str) -> None:
        """
        Jump to the doctor-facing Patient Records page (Reports) and focus the selected patient.

        This restores the clinician workflow actions (referral, archive, export, etc.) that live on Reports.
        """
        code = str(patient_code or "").strip()
        if not code or not self._app:
            self._show_queue_page()
            return
        # Navigate out to the main Reports page.
        if hasattr(self._app, "_navigate_to"):
            self._app._navigate_to(3, nav_key="Reports")
        rp = getattr(self._app, "reports_page", None)
        if rp is not None and hasattr(rp, "focus_patient_record"):
            with contextlib.suppress(Exception):
                rp.focus_patient_record(code, open_overview=True)

    def _on_start_diagnosis_clicked(self, qid, pid) -> None:
        if not self._is_clinical():
            return
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            show_warning(self, "Diagnosis", "Invalid patient for this queue row.")
            return
        qid_i = None
        if qid is not None and str(qid).strip() != "":
            try:
                qid_i = int(qid)
            except (TypeError, ValueError):
                qid_i = None
        if not self._prepare_clinical_visit_open(qid_i, pid_i):
            self.refresh()
            return
        # Follow-up purpose: show inline review inside queue screen (no popup).
        is_follow_up = False
        if qid_i:
            qrow = emr.get_queue_entry(int(qid_i)) or {}
            is_follow_up = str(qrow.get("screening_purpose") or "").strip().lower() == "follow_up"
        if is_follow_up:
            show_warning(
                self,
                "Follow-up screening",
                "System found a previous screening for this patient.\n\nPlease review it first before proceeding to the diagnosis."
            )
            self._show_followup_review(qid_i, pid_i)
            return
        self._launch_screening_from_queue(qid_i, pid_i)
        self.refresh()

    def _launch_screening_from_queue(self, qid: int | None, pid: int, skip_confirm: bool = False) -> None:
        if not skip_confirm:
            # Formal confirmation based on patient history
            n = emr.count_screenings_for_patient(int(pid))
            if n == 0:
                msg = "No prior screening records were found for this patient. You will be directed to the diagnosis form for initial entry."
            else:
                msg = "Existing screening records were found for this patient. Previous clinical history and results will be presented for your review before starting the new session."

            if not confirm(self, "Confirm Diagnosis Action", msg, yes_text="Continue", no_text="Cancel"):
                return

        patient = emr.get_patient(int(pid)) or {}
        if not patient:
            # Fallback: build a minimal patient dict from queue listing if the patient row was removed/missing.
            today = date.today().isoformat()
            for r in emr.list_queue_rows(today):
                if int(r.get("patient_id") or 0) == int(pid):
                    patient = {
                        "patient_id": int(pid),
                        "patient_code": r.get("patient_code") or "",
                        "first_name": r.get("first_name") or "",
                        "last_name": r.get("last_name") or "",
                        "date_of_birth": r.get("date_of_birth") or "",
                        "sex": r.get("sex") or "",
                        "contact_number": "",
                    }
                    break
        if not patient:
            show_warning(self, "Diagnosis", "Could not load the selected patient record.")
            return

        # Doctor diagnosis flow: open dedicated diagnosis form (upload → existing results UI).
        self._embed_doctor_diagnosis_form()
        form = getattr(self, "_doctor_diagnosis_form", None)
        if form is not None and hasattr(form, "start_for_patient"):
            form.start_for_patient(patient, queue_entry_id=qid)

    def _show_followup_review(self, qid: int, pid: int) -> None:
        if not hasattr(self, "_queue_stack") or not hasattr(self, "_review_host_layout"):
            return
        # Clear previous embedded widget.
        while self._review_host_layout.count():
            item = self._review_host_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
        p = emr.get_patient(int(pid)) or {}
        code = str(p.get("patient_code") or "").strip()

        # Follow-up UX should show *previous* completed screenings with previews.
        # Merge EMR-derived timeline (new system) with legacy patient_records timeline.
        timeline_rows = []
        with contextlib.suppress(Exception):
            timeline_rows.extend(emr.list_emr_timeline_records(int(pid)) or [])
        if code:
            with contextlib.suppress(Exception):
                timeline_rows.extend(self._fetch_legacy_timeline_records(code) or [])

        timeline = group_patient_record_rows(timeline_rows)
        patient_summary = timeline[-1] if timeline else {
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "patient_id": code,
        }

        # Prefer selecting the most recent *non-pending* screening for the top card.
        initial_record = None
        for rec in reversed(timeline or []):
            result = str(rec.get("result") or "").strip().lower()
            has_image = bool(str(rec.get("source_image_path") or "").strip())
            if result and result != "pending" and has_image:
                initial_record = rec
                break
        if initial_record is None and timeline:
            initial_record = timeline[-1]
        panel = PatientTimelineDialog(
            patient_summary,
            timeline,
            parent=self,
            on_follow_up=None,
            on_view_report=None,
            on_compare=self._on_compare_from_review,
            on_export=None,
            on_start_diagnosis=self._start_diagnosis_from_review,
            initial_record=initial_record,
            show_actions=False,
            show_inline_compare=True,
            show_history_tab=True,
        )
        panel.back_requested.connect(lambda: self._show_review_list())
        self._review_host_layout.addWidget(panel, 1)
        self._review_ctx = {"qid": int(qid), "pid": int(pid)}
        self._queue_stack.setCurrentIndex(1)

    def _fetch_legacy_timeline_records(self, patient_code: str) -> list[dict]:
        code = str(patient_code or "").strip()
        if not code:
            return []
        conn = None
        try:
            conn = sqlite3.connect(LEGACY_DB_FILE)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, patient_id, name, birthdate, age, sex, contact, phone, email, address, eyes,
                       diabetes_type, duration, hba1c, prev_treatment, notes,
                       result, confidence, screened_at, archived_at, archived_by,
                       archive_reason, original_screener_username, original_screener_name,
                       ai_classification, doctor_classification, decision_mode, override_justification, final_diagnosis_icdr, doctor_findings,
                       height, weight, bmi, visual_acuity_left, visual_acuity_right,
                       blood_pressure_systolic, blood_pressure_diastolic,
                       fasting_blood_sugar, random_blood_sugar,
                       diabetes_diagnosis_date, treatment_regimen, prev_dr_stage,
                       symptom_blurred_vision, symptom_floaters, symptom_flashes, symptom_vision_loss,
                       source_image_path, heatmap_image_path,
                       follow_up, followup_date, followup_label, screening_type, previous_screening_id, screening_group_id
                FROM patient_records
                WHERE patient_id = ? AND archived_at IS NULL
                ORDER BY screened_at ASC, id ASC
                """,
                (code,),
            )
            rows = cur.fetchall()
        except Exception:
            return []
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

        timeline = []
        for row in rows:
            timeline.append(
                {
                    "id": row[0],
                    "patient_id": row[1],
                    "name": row[2],
                    "birthdate": row[3],
                    "age": row[4],
                    "sex": row[5],
                    "contact": row[6],
                    "phone": row[7],
                    "email": row[8],
                    "address": row[9],
                    "eyes": row[10],
                    "diabetes_type": row[11],
                    "duration": row[12],
                    "hba1c": row[13],
                    "prev_treatment": row[14],
                    "notes": row[15],
                    "result": row[16],
                    "confidence": row[17],
                    "screened_at": row[18],
                    "archived_at": row[19],
                    "archived_by": row[20],
                    "archive_reason": row[21],
                    "original_screener_username": row[22],
                    "original_screener_name": row[23],
                    "ai_classification": row[24],
                    "doctor_classification": row[25],
                    "decision_mode": row[26],
                    "override_justification": row[27],
                    "final_diagnosis_icdr": row[28],
                    "doctor_findings": row[29],
                    "height": row[30],
                    "weight": row[31],
                    "bmi": row[32],
                    "visual_acuity_left": row[33],
                    "visual_acuity_right": row[34],
                    "blood_pressure_systolic": row[35],
                    "blood_pressure_diastolic": row[36],
                    "fasting_blood_sugar": row[37],
                    "random_blood_sugar": row[38],
                    "diabetes_diagnosis_date": row[39],
                    "treatment_regimen": row[40],
                    "prev_dr_stage": row[41],
                    "symptom_blurred_vision": row[42],
                    "symptom_floaters": row[43],
                    "symptom_flashes": row[44],
                    "symptom_vision_loss": row[45],
                    "source_image_path": row[46],
                    "heatmap_image_path": row[47],
                    "follow_up": row[48],
                    "followup_date": row[49],
                    "followup_label": row[50],
                    "screening_type": row[51],
                    "previous_screening_id": row[52],
                    "screening_group_id": row[53],
                }
            )
        return group_patient_record_rows(timeline)

    def _on_compare_from_review(self, timeline: list[dict]) -> None:
        if not timeline:
            show_warning(self, "Compare Screenings", "No screening history found to compare.")
            return
        
        # Filter for completed screenings only (ignore pending/on-going sessions)
        try:
            from .reports import ScreeningComparisonDialog
        except Exception:
            from reports import ScreeningComparisonDialog
            
        completed = ScreeningComparisonDialog.filter_completed_screenings(timeline)
        
        if len(completed) < 2:
            show_warning(self, "Compare Screenings", "At least two completed screenings are required for comparison.")
            return
        
        dialog = ScreeningComparisonDialog(completed, self.window() if hasattr(self, "window") else self)
        apply_dialog_style(dialog)
        dialog.resize(max(dialog.width(), 1320), max(dialog.height(), 960))
        dialog.exec()

    def _start_diagnosis_from_review(self) -> None:
        ctx = getattr(self, "_review_ctx", {}) or {}
        qid = ctx.get("qid")
        pid = ctx.get("pid")
        if not qid or not pid:
            return
        # Go to diagnosis flow, then return queue stack to list view.
        self._queue_stack.setCurrentIndex(0)
        self._launch_screening_from_queue(int(qid), int(pid), skip_confirm=True)

    def _open_saved_patient_screening_history(self, patient_id: int, queue_id: int) -> None:
        try:
            pid = int(patient_id)
        except (TypeError, ValueError):
            return
        qid = None
        try:
            if queue_id:
                qid = int(queue_id)
        except (TypeError, ValueError):
            qid = None
        if hasattr(self, "_emr_stack"):
            self._emr_stack.setCurrentIndex(0)
        dialog = PatientVisitDialog(
            self,
            self._app,
            patient_id=pid,
            queue_id=qid,
            username=self._username,
            can_clinical=self._is_clinical(),
            can_edit_overview=self._is_front() or self._is_clinical(),
            actor_role=self._role,
            initial_tab="history",
        )
        dialog.exec()
        self.refresh()

    def _open_selected(self) -> None:
        qid, pid = self._selected_queue_and_patient()
        if not pid:
            show_warning(self, "Visit", "Select a visit row first.")
            return
        self._prepare_clinical_visit_open(qid, int(pid))
        if self._is_clinical():
            self._launch_screening_from_queue(qid, int(pid))
        else:
            dialog = PatientVisitDialog(
                self,
                self._app,
                patient_id=pid,
                queue_id=qid,
                username=self._username,
                can_clinical=self._is_clinical(),
                can_edit_overview=self._is_front() or self._is_clinical(),
                actor_role=self._role,
            )
            dialog.exec()
        self.refresh()

    def _register_and_queue(self) -> None:
        uid = emr.get_user_id(self._username)
        if not uid:
            show_error(self, "Register", "Could not resolve current user.")
            return
        last = self.in_last.text().strip()
        first = self.in_first.text().strip()
        if not last or not first:
            show_warning(self, "Register", "Last name and first name are required.")
            return
        d = self.in_dob.date()
        if not d.isValid():
            show_warning(self, "Register", "Invalid date of birth.")
            return
        dob_s = d.toString("yyyy-MM-dd")
        last_eye_exam_raw = self.in_last_eye_exam.text().strip()
        if last_eye_exam_raw:
            parsed = QDate.fromString(last_eye_exam_raw, "yyyy-MM-dd")
            if not parsed.isValid():
                show_warning(self, "Register", "Last eye exam date must be YYYY-MM-DD or left blank.")
                return
        try:
            pid, created_new = emr.upsert_patient_by_name_dob(
                int(uid),
                last_name=last,
                first_name=first,
                middle_name=self.in_middle.text().strip(),
                date_of_birth=dob_s,
                sex=self.in_sex.currentText(),
                contact_number=self.in_phone.text().strip(),
                email=self.in_email.text().strip(),
                address=self.in_address.text().strip(),
                height_cm=self.in_height.value() or None,
                weight_kg=self.in_weight.value() or None,
                diabetes_type=self.in_dm_type.currentText(),
                dm_duration_years=self.in_dm_duration.value() or None,
                hba1c=self.in_hba1c.value() or None,
                current_medications=self.in_meds.toPlainText().strip(),
                known_allergies=self.in_allergies.toPlainText().strip(),
                other_conditions=self.in_other_conditions.toPlainText().strip(),
                current_eye_treatment=self.in_current_eye_tx.toPlainText().strip(),
                previous_eye_treatment=self.in_previous_eye_tx.toPlainText().strip(),
                last_eye_exam_date=last_eye_exam_raw,
            )
            qid = emr.assign_queue_entry(pid, uid)
        except Exception as err:
            show_error(self, "Register", f"Could not register patient: {err}")
            return
        self.refresh()
        self._select_queue_row(qid)
        p = emr.get_patient(pid) or {}
        pnm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        code = p.get("patient_code", "")
        qe = emr.get_queue_entry(qid) or {}
        qnum = qe.get("queue_number", "")
        if hasattr(self, "_toast") and self._toast:
            self._toast.show_text(f"Queue {qnum} assigned to {pnm} ({code}).", 5000)
        if created_new:
            title = "Patient registered"
            detail = f"{pnm} ({code}) has been registered and added to today's queue as {qnum}."
        else:
            title = "Patient queued"
            detail = f"{pnm} ({code}) already exists and was added to today's queue as {qnum}."
        show_success(self, title, detail)
        self.in_last.clear()
        self.in_first.clear()
        self.in_middle.clear()
        self.in_phone.clear()
        self.in_email.clear()
        self.in_address.clear()
        self.in_height.setValue(0)
        self.in_weight.setValue(0)
        self.in_dm_type.setCurrentIndex(0)
        self.in_dm_duration.setValue(0)
        self.in_hba1c.setValue(0)
        self.in_meds.clear()
        self.in_allergies.clear()
        self.in_other_conditions.clear()
        self.in_current_eye_tx.clear()
        self.in_previous_eye_tx.clear()
        self.in_last_eye_exam.clear()

    def _run_search(self) -> None:
        q = self.search_field.text().strip()
        found = emr.search_patients(q)
        self._selected_search_patient_id = None
        self.search_table.setRowCount(len(found))
        for i, p in enumerate(found):
            self.search_table.setItem(i, 0, QTableWidgetItem(str(p.get("patient_id", ""))))
            self.search_table.setItem(i, 1, QTableWidgetItem(str(p.get("patient_code", ""))))
            nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            self.search_table.setItem(i, 2, QTableWidgetItem(nm))
            self.search_table.setItem(i, 3, QTableWidgetItem(str(p.get("date_of_birth", ""))))
        self.search_table.resizeColumnsToContents()
        if found:
            self.search_table.selectRow(0)
            self._on_search_selection_changed()
        else:
            self.search_profile_label.setText("No matching patients found.")
            self.btn_edit_patient.setEnabled(False)
            self.btn_new_visit.setEnabled(False)

    def _new_visit_for_selected_patient(self) -> None:
        uid = emr.get_user_id(self._username)
        if not uid:
            show_error(self, "Queue", "Could not resolve current user.")
            return
        if self._selected_search_patient_id is None:
            show_warning(self, "Queue", "Select a patient in the search results first.")
            return
        pid = self._selected_search_patient_id

        # Guard rail: only one active visit per patient per day.
        ok, reason = emr.can_create_visit_for_patient(pid)
        if not ok:
            ex = emr.get_today_active_queue_for_patient(pid) or {}
            m = QMessageBox(self)
            apply_dialog_style(m)
            m.setWindowTitle("Active visit exists")
            m.setIcon(QMessageBox.Icon.Warning)
            m.setText(
                f"{reason}\n\n"
                "Open the existing visit instead of creating a duplicate."
            )
            b_use = m.addButton("Open existing visit", QMessageBox.ButtonRole.AcceptRole)
            b_cancel = m.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            m.setDefaultButton(b_use)
            m.exec()
            clicked = m.clickedButton()
            if clicked == b_use:
                self._select_queue_row(int(ex.get("queue_id", 0)))
                return
            return

        p = emr.get_patient(pid) or {}
        pnm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "this patient"
        if not confirm(
            self,
            "Assign queue number",
            f"Assign a new queue number to {pnm} for today?",
            yes_text="Assign queue",
            no_text="Cancel",
        ):
            return
        try:
            with loading_state([self.btn_new_visit], loading_text="Assigning…"):
                qid = emr.assign_queue_entry(pid, uid)
        except Exception as err:
            show_error(self, "Queue", f"Could not assign queue number: {err}")
            return
        self.refresh()
        self._select_queue_row(qid)
        qe = emr.get_queue_entry(qid)
        qnum = qe.get("queue_number", "") if qe else ""
        if hasattr(self, "_toast") and self._toast:
            self._toast.show_text(f"Queue {qnum} assigned to {pnm}.", 4000)
        show_success(
            self,
            "Visit created",
            f"{pnm} is now in today's queue as {qnum}. The doctor will see them in the visit list.",
        )

    def _select_queue_row(self, queue_id: int) -> None:
        for row in range(self.table.rowCount()):
            # Queue id is stored in the hidden column (index 5).
            item = self.table.item(row, 5)
            if not item:
                continue
            try:
                qid = int(item.text())
            except (TypeError, ValueError):
                continue
            if qid == int(queue_id):
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                break

    def _on_search_selection_changed(self) -> None:
        row = self.search_table.currentRow()
        if row < 0:
            self._selected_search_patient_id = None
            self.search_profile_label.setText("Select a patient from search results.")
            self.btn_edit_patient.setEnabled(False)
            self.btn_new_visit.setEnabled(False)
            return
        item = self.search_table.item(row, 0)
        if not item:
            return
        try:
            pid = int(item.text())
        except (TypeError, ValueError):
            return
        self._selected_search_patient_id = pid
        p = emr.get_patient(pid) or {}
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        profile_text = (
            f"Code: {p.get('patient_code', '-')}\n"
            f"Name: {name or '-'}\n"
            f"DOB: {p.get('date_of_birth', '-')}\n"
            f"Sex: {p.get('sex', '-')}\n"
            f"Contact: {p.get('contact_number', '-')}\n"
            f"Address: {p.get('address', '-')}"
        )
        self.search_profile_label.setText(profile_text)
        self.btn_edit_patient.setEnabled(True)
        self.btn_new_visit.setEnabled(True)

    def _edit_selected_patient(self) -> None:
        if self._selected_search_patient_id is None:
            QMessageBox.information(self, "Edit Patient", "Select a patient in the search results.")
            return
        dialog = PatientVisitDialog(
            self,
            self._app,
            patient_id=self._selected_search_patient_id,
            queue_id=None,
            username=self._username,
            can_clinical=False,
            can_edit_overview=True,
            actor_role=self._role,
        )
        dialog.exec()
        self._run_search()
        self._on_search_selection_changed()

    def _cancel_selected_visit(self) -> None:
        uid = emr.get_user_id(self._username)
        if not uid:
            show_error(self, "Queue", "Could not resolve current user.")
            return
        qid, _ = self._selected_queue_and_patient()
        if not qid:
            show_warning(self, "Queue", "Select a visit row to cancel first.")
            return
        ok, reason = emr.can_cancel_visit(qid)
        if not ok:
            show_warning(self, "Cannot cancel visit", reason)
            return
        qrow = [x for x in emr.list_queue_rows(date.today().isoformat()) if x.get("queue_id") == qid]
        pname = ""
        qnum = ""
        if qrow:
            p0 = qrow[0]
            pname = f"{p0.get('first_name', '')} {p0.get('last_name', '')}".strip() or "this patient"
            qnum = str(p0.get("queue_number") or "")
        if not confirm(
            self,
            "Cancel visit",
            (
                f"Cancel {('visit ' + qnum + ' for ') if qnum else 'visit for '}{pname or 'this patient'}?\n\n"
                "They will be removed from today's active queue. This cannot be undone."
            ),
            yes_text="Cancel visit",
            no_text="Keep visit",
        ):
            return
        with loading_state([self.btn_cancel_visit], loading_text="Cancelling…"):
            ok_set = emr.set_queue_status(qid, "cancelled", uid)
        if ok_set:
            self.refresh()
            if hasattr(self, "_toast") and self._toast:
                self._toast.show_text(f"Visit {qnum or qid} cancelled.", 4000)
        else:
            show_error(self, "Queue", "Could not cancel this visit. Please try again.")

    def _clear_today_queue(self) -> None:
        if not self._is_front():
            return
        uid = emr.get_user_id(self._username)
        if not uid:
            show_error(self, "Queue", "Could not resolve current user.")
            return
        today = date.today().isoformat()
        if not confirm(
            self,
            "Clear patient queue",
            (
                "Clear today's patient queue?\n\n"
                "This will remove all visits from today's active queue. This cannot be undone."
            ),
            yes_text="Clear queue",
            no_text="Cancel",
        ):
            return
        btns = [b for b in (getattr(self, "btn_clear_queue", None), getattr(self, "btn_cancel_visit", None)) if b is not None]
        with loading_state(btns, loading_text="Clearing…"):
            deleted = emr.clear_queue(today, user_id=uid)
        self.refresh()
        if hasattr(self, "_toast") and self._toast:
            self._toast.show_text(f"Cleared queue ({deleted} visit(s)).", 4000)


    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _go_to_new_patient_intake(self) -> None:
        """Frontdesk shortcut: jump to Assessment intake without changing flow."""
        app = getattr(self, "_app", None)
        if not app:
            return
        # Prefer existing navigation method (keeps sidebar state consistent).
        if hasattr(app, "_navigate_to"):
            try:
                app._navigate_to(1, nav_key="Screening")
            except TypeError:
                app._navigate_to(1)
        elif hasattr(app, "pages"):
            app.pages.setCurrentIndex(1)
        sp = getattr(app, "screening_page", None)
        if sp is not None and hasattr(sp, "reset_screening"):
            try:
                sp.reset_screening(confirm_unsaved=False)
            except TypeError:
                sp.reset_screening()
