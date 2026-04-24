from __future__ import annotations

import contextlib
from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QDialog,
    QFormLayout,
    QLineEdit,
    QDateEdit,
    QComboBox,
    QMessageBox,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
)
from PySide6.QtCore import Qt, QDate

try:
    from .screening_form import ScreeningPage
except Exception:
    from screening_form import ScreeningPage

try:
    import emr_service as emr
except Exception:
    from . import emr_service as emr


class DoctorDiagnosisForm(QWidget):
    """
    Doctor-focused diagnosis form.

    Implementation note:
    We intentionally host a *fresh* ScreeningPage instance here so a doctor starting
    diagnosis from the queue never inherits unsaved state from prior flows.
    """

    back_requested = Signal()
    screening_history_requested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.username: str = ""
        self.role: str = ""
        self.display_name: str = ""

        self._emr_patient: dict = {}
        self._queue_entry_id: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QFrame()
        header.setStyleSheet("QFrame{background:transparent;}")
        self._header = header
        hl = QVBoxLayout(header)
        hl.setContentsMargins(18, 0, 18, 0)
        hl.setSpacing(4)

        title = QLabel("Diagnosis")
        title.setStyleSheet("font-size:20px;font-weight:800;color:#0f172a;")
        subtitle = QLabel("Upload a fundus image to start analysis. Results will appear in the next step.")
        subtitle.setStyleSheet("font-size:12px;color:#64748b;font-weight:500;")
        hl.addWidget(title)
        hl.addWidget(subtitle)

        root.addWidget(header, 0)

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        content_row = QHBoxLayout(content)
        content_row.setContentsMargins(12, 0, 12, 12)
        content_row.setSpacing(10)

        left = QWidget()
        left.setStyleSheet("background:transparent;")
        left.setMinimumWidth(360)
        left.setMaximumWidth(440)
        self._left_panel = left
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)
        left_l.addWidget(self._build_patient_info_card())
        left_l.addWidget(self._build_vital_signs_card())
        left_l.addWidget(self._build_clinical_history_card())
        left_l.addStretch(1)

        self.screening = ScreeningPage()
        self.screening.p_eye.currentTextChanged.connect(self._sync_eye_combo_from_screening)
        self._bind_screening_summary_refresh()
        # After a queue diagnosis is saved, we mark visit completed and return to queue.
        self.screening._post_save_history_handler = self._on_queue_save_completed
        if hasattr(self.screening, "stacked_widget"):
            self.screening.stacked_widget.currentChanged.connect(self._update_results_focus_mode)

        content_row.addWidget(left, 4)
        content_row.addWidget(self.screening, 8)
        self._content_row = content_row
        root.addWidget(content, 1)
        self._update_results_focus_mode(0)

    @staticmethod
    def _edit_button_stylesheet() -> str:
        return (
            "QPushButton{background:#f1f5f9;border:none;border-radius:8px;color:#334155;font-weight:700;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )

    def _add_card_header(self, layout: QVBoxLayout, title_text: str, *, with_edit: bool = False) -> None:
        hdr = QHBoxLayout()
        title = QLabel(title_text)
        title.setStyleSheet("font-size:12px;font-weight:700;color:#0f172a;")
        hdr.addWidget(title)
        hdr.addStretch(1)
        if with_edit:
            btn = QPushButton("Edit")
            btn.setFixedSize(56, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._edit_button_stylesheet())
            btn.clicked.connect(self._open_edit_dialog)
            hdr.addWidget(btn)
        layout.addLayout(hdr)

    def _bind_screening_summary_refresh(self) -> None:
        for name, signal_name in (
            ("va_left", "textChanged"),
            ("va_right", "textChanged"),
            ("bp_systolic", "valueChanged"),
            ("bp_diastolic", "valueChanged"),
            ("fbs", "valueChanged"),
            ("rbs", "valueChanged"),
            ("diabetes_type", "currentTextChanged"),
            ("diabetes_duration", "valueChanged"),
            ("hba1c", "valueChanged"),
            ("diabetes_diagnosis_date", "textChanged"),
            ("treatment_regimen", "currentTextChanged"),
            ("prev_dr_stage", "currentTextChanged"),
            ("symptom_other", "textChanged"),
            ("prev_treatment", "toggled"),
            ("symptom_blurred", "toggled"),
            ("symptom_floaters", "toggled"),
            ("symptom_flashes", "toggled"),
            ("symptom_vision_loss", "toggled"),
        ):
            widget = getattr(self.screening, name, None)
            signal = getattr(widget, signal_name, None) if widget is not None else None
            if signal is not None:
                signal.connect(lambda *args: self._refresh_patient_card())

    def _build_patient_info_card(self) -> QWidget:
        card = QFrame()
        card.setStyleSheet("QFrame{background:#ffffff;border:none;border-radius:12px;}")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        self._add_card_header(v, "Patient Information", with_edit=True)

        grid = QHBoxLayout()
        grid.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(6)
        right = QVBoxLayout()
        right.setSpacing(6)

        def row(label: str) -> QLabel:
            r = QLabel(label)
            r.setStyleSheet("font-size:10px;color:#64748b;font-weight:600;")
            return r

        def val() -> QLabel:
            r = QLabel("-")
            r.setStyleSheet("font-size:11px;color:#0f172a;font-weight:700;")
            r.setWordWrap(True)
            return r

        self._pi = {}
        for key, label in [("name", "Patient Name"), ("code", "Patient ID"), ("dob", "Date of Birth")]:
            left.addWidget(row(label))
            self._pi[key] = val()
            left.addWidget(self._pi[key])

        for key, label in [("age", "Age"), ("sex", "Sex"), ("contact", "Contact")]:
            right.addWidget(row(label))
            self._pi[key] = val()
            right.addWidget(self._pi[key])

        grid.addLayout(left, 1)
        grid.addLayout(right, 1)
        v.addLayout(grid)

        # Extra demographics row (matches request to include other demographics).
        extra = QHBoxLayout()
        extra.setSpacing(18)

        def col_block(key: str, label: str) -> QVBoxLayout:
            c = QVBoxLayout()
            c.setSpacing(4)
            c.addWidget(row(label))
            self._pi[key] = val()
            c.addWidget(self._pi[key])
            return c

        extra.addLayout(col_block("height", "Height"), 1)
        extra.addLayout(col_block("weight", "Weight"), 1)
        extra.addLayout(col_block("bmi", "BMI"), 1)
        v.addLayout(extra)

        eye_row = QHBoxLayout()
        eye_row.setSpacing(10)
        eye_lbl = QLabel("Eye to be screened")
        eye_lbl.setStyleSheet("font-size:10px;color:#64748b;font-weight:700;")
        self.eye_combo = QComboBox()
        self.eye_combo.addItems(["Right Eye", "Left Eye"])
        self.eye_combo.setCursor(Qt.PointingHandCursor)
        self.eye_combo.setFixedHeight(30)
        self.eye_combo.setStyleSheet(
            "QComboBox{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:4px 10px;"
            "font-size:11px;font-weight:700;color:#0f172a;}"
            "QComboBox::drop-down{border:none;width:22px;}"
        )
        self.eye_combo.currentTextChanged.connect(self._on_eye_changed)
        eye_row.addWidget(eye_lbl, 2)
        eye_row.addWidget(self.eye_combo, 5)
        v.addLayout(eye_row)
        return card

    def _on_eye_changed(self, label: str) -> None:
        if not hasattr(self, "screening") or not hasattr(self.screening, "p_eye"):
            return
        if hasattr(self.screening, "_suspend_eye_guard"):
            self.screening._suspend_eye_guard = True
        try:
            self.screening.p_eye.setCurrentText(str(label or "").strip())
        finally:
            if hasattr(self.screening, "_suspend_eye_guard"):
                self.screening._suspend_eye_guard = False

    def _sync_eye_combo_from_screening(self, text: str) -> None:
        """Keep the diagnosis header eye selector aligned when screening switches eyes (e.g. bilateral flow)."""
        if not hasattr(self, "eye_combo"):
            return
        t = str(text or "").strip()
        if not t or self.eye_combo.currentText() == t:
            return
        self.eye_combo.blockSignals(True)
        self.eye_combo.setCurrentText(t)
        self.eye_combo.blockSignals(False)

    def _on_queue_save_completed(self) -> None:
        """Doctor queue flow: finish visit on save and remove from active queue."""
        qid = int(self._queue_entry_id or 0)
        if qid <= 0:
            self.back_requested.emit()
            return

        username = str(getattr(self, "username", "") or "").strip()
        uid = emr.get_user_id(username) if username else None
        if uid:
            ok, _reason = emr.can_complete_visit(qid)
            if ok:
                emr.set_queue_status(qid, "completed", int(uid))

        QMessageBox.information(
            self,
            "Visit Completed",
            "Visit is complete. The patient was removed from the queue.",
        )
        self.back_requested.emit()

    def _update_results_focus_mode(self, index: int) -> None:
        """When results are shown, give the results page the full diagnosis width."""
        on_results = int(index) == 1
        if hasattr(self, "_left_panel"):
            self._left_panel.setVisible(not on_results)
        if hasattr(self, "_header"):
            self._header.setVisible(not on_results)
        if hasattr(self, "_content_row"):
            self._content_row.setContentsMargins(0 if on_results else 12, 0, 0 if on_results else 12, 12)
            self._content_row.setSpacing(0 if on_results else 10)

    def _build_vital_signs_card(self) -> QWidget:
        card = QFrame()
        card.setStyleSheet("QFrame{background:#ffffff;border:none;border-radius:12px;}")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        self._add_card_header(v, "Vital Signs", with_edit=True)

        self._vs = {}

        def kv(label: str, key: str):
            row = QHBoxLayout()
            row.setSpacing(10)
            k = QLabel(label)
            k.setStyleSheet("font-size:10px;color:#64748b;font-weight:600;")
            val = QLabel("-")
            val.setStyleSheet("font-size:11px;color:#0f172a;font-weight:700;")
            val.setWordWrap(True)
            self._vs[key] = val
            row.addWidget(k, 2)
            row.addWidget(val, 5)
            v.addLayout(row)

        kv("Visual Acuity", "va")
        kv("Blood Pressure", "bp")
        kv("Blood Glucose", "bg")
        kv("Symptoms", "symptoms")
        return card

    def _build_clinical_history_card(self) -> QWidget:
        card = QFrame()
        card.setStyleSheet("QFrame{background:#ffffff;border:none;border-radius:12px;}")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        self._add_card_header(v, "Clinical History", with_edit=True)

        self._ch = {}

        def kv(label: str, key: str):
            row = QHBoxLayout()
            row.setSpacing(10)
            k = QLabel(label)
            k.setStyleSheet("font-size:10px;color:#64748b;font-weight:600;")
            val = QLabel("-")
            val.setStyleSheet("font-size:11px;color:#0f172a;font-weight:700;")
            val.setWordWrap(True)
            self._ch[key] = val
            row.addWidget(k, 2)
            row.addWidget(val, 5)
            v.addLayout(row)

        kv("Diabetes Type", "diabetes_type")
        kv("Diagnosis Date", "diagnosis_date")
        kv("DM Duration", "dm_duration_years")
        kv("HbA1c", "hba1c")
        kv("Treatment Regimen", "treatment_regimen")
        kv("Previous DR Stage", "prev_dr_stage")
        kv("Prev. Treatment", "prev_treatment")
        return card

    @staticmethod
    def _compute_age(dob_iso: str) -> str:
        dob = str(dob_iso or "").strip()[:10]
        if not dob:
            return "-"
        try:
            born = datetime.strptime(dob, "%Y-%m-%d").date()
        except ValueError:
            return "-"
        today = datetime.now().date()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return f"{max(age, 0)} years"

    @staticmethod
    def _fmt_num(v, suffix: str) -> str:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return "-"
        if fv <= 0:
            return "-"
        return f"{round(fv, 1)}{suffix}"

    @staticmethod
    def _screening_choice_text(widget, *, blank_values: set[str] | None = None) -> str:
        blank_values = blank_values or set()
        text = str(getattr(widget, "currentText", lambda: "")() or "").strip()
        if not text or text in blank_values:
            return "-"
        return text

    def _symptom_summary(self) -> str:
        symptoms: list[str] = []
        for attr, label in (
            ("symptom_blurred", "Blurred vision"),
            ("symptom_floaters", "Floaters"),
            ("symptom_flashes", "Flashes"),
            ("symptom_vision_loss", "Vision loss"),
        ):
            widget = getattr(self.screening, attr, None)
            if widget is not None and hasattr(widget, "isChecked") and widget.isChecked():
                symptoms.append(label)
        other = str(getattr(getattr(self.screening, "symptom_other", None), "text", lambda: "")() or "").strip()
        if other:
            symptoms.append(other)
        return ", ".join(symptoms) if symptoms else "None noted"

    def _set_line_edit_text(self, widget, value: str) -> None:
        if widget is None or not hasattr(widget, "setText"):
            return
        widget.setText(str(value or ""))

    def _set_spinbox_value(self, widget, value) -> None:
        if widget is None or not hasattr(widget, "setValue"):
            return
        try:
            widget.setValue(value)
        except Exception:
            pass

    def _set_combo_value(self, widget, value: str, *, blank_values: set[str] | None = None) -> None:
        if widget is None or not hasattr(widget, "currentText"):
            return
        blank_values = blank_values or set()
        text = str(value or "").strip()
        if not text or text in blank_values:
            if hasattr(widget, "setCurrentIndex"):
                widget.setCurrentIndex(0)
            return
        idx = widget.findText(text) if hasattr(widget, "findText") else -1
        if idx >= 0 and hasattr(widget, "setCurrentIndex"):
            widget.setCurrentIndex(idx)
        elif hasattr(widget, "setCurrentText"):
            widget.setCurrentText(text)

    def _set_checked(self, widget, checked: bool) -> None:
        if widget is None or not hasattr(widget, "setChecked"):
            return
        widget.setChecked(bool(checked))

    def _refresh_screening_fields_from_dialog(self, values: dict) -> None:
        va_left = str(values.get("va_left") or "").strip()
        va_right = str(values.get("va_right") or "").strip()
        if hasattr(self.screening, "_normalize_visual_acuity"):
            va_left, _ = self.screening._normalize_visual_acuity(va_left)
            va_right, _ = self.screening._normalize_visual_acuity(va_right)

        self._set_line_edit_text(getattr(self.screening, "va_left", None), va_left)
        self._set_line_edit_text(getattr(self.screening, "va_right", None), va_right)
        self._set_spinbox_value(getattr(self.screening, "bp_systolic", None), int(values.get("bp_systolic") or 0))
        self._set_spinbox_value(getattr(self.screening, "bp_diastolic", None), int(values.get("bp_diastolic") or 0))
        self._set_spinbox_value(getattr(self.screening, "fbs", None), int(values.get("fbs") or 0))
        self._set_spinbox_value(getattr(self.screening, "rbs", None), int(values.get("rbs") or 0))
        self._set_combo_value(getattr(self.screening, "diabetes_type", None), values.get("diabetes_type") or "", blank_values={"Select"})
        self._set_spinbox_value(getattr(self.screening, "diabetes_duration", None), int(values.get("dm_duration_years") or 0))
        self._set_spinbox_value(getattr(self.screening, "hba1c", None), float(values.get("hba1c") or 0))
        self._set_line_edit_text(getattr(self.screening, "diabetes_diagnosis_date", None), values.get("diagnosis_date") or "")
        self._set_combo_value(
            getattr(self.screening, "treatment_regimen", None),
            values.get("treatment_regimen") or "",
            blank_values={"Select"},
        )
        self._set_combo_value(
            getattr(self.screening, "prev_dr_stage", None),
            values.get("prev_dr_stage") or "",
            blank_values={"Select"},
        )
        self._set_checked(getattr(self.screening, "prev_treatment", None), bool(values.get("prev_treatment")))
        self._set_checked(getattr(self.screening, "symptom_blurred", None), bool(values.get("symptom_blurred")))
        self._set_checked(getattr(self.screening, "symptom_floaters", None), bool(values.get("symptom_floaters")))
        self._set_checked(getattr(self.screening, "symptom_flashes", None), bool(values.get("symptom_flashes")))
        self._set_checked(getattr(self.screening, "symptom_vision_loss", None), bool(values.get("symptom_vision_loss")))
        self._set_line_edit_text(getattr(self.screening, "symptom_other", None), values.get("symptom_other") or "")

        # Anthropometrics (visit-scoped)
        try:
            h = float(values.get("height_cm") or 0)
        except (TypeError, ValueError):
            h = 0.0
        try:
            w = float(values.get("weight_kg") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if h > 0:
            self._set_spinbox_value(getattr(self.screening, "height", None), h)
        if w > 0:
            self._set_spinbox_value(getattr(self.screening, "weight", None), w)
        if hasattr(self.screening, "_calculate_bmi"):
            with contextlib.suppress(Exception):
                self.screening._calculate_bmi()

    def _refresh_patient_card(self) -> None:
        p = self._emr_patient or {}
        fn = str(p.get("first_name") or "").strip()
        ln = str(p.get("last_name") or "").strip()
        name = f"{fn} {ln}".strip() or "-"
        code = str(p.get("patient_code") or "-").strip() or "-"
        dob = str(p.get("date_of_birth") or "")[:10] or "-"
        age = self._compute_age(dob)
        sex = str(p.get("sex") or "-").strip() or "-"
        contact = str(p.get("contact_number") or "-").strip() or "-"

        self._pi.get("name") and self._pi["name"].setText(name)
        self._pi.get("code") and self._pi["code"].setText(code)
        self._pi.get("dob") and self._pi["dob"].setText(dob)
        self._pi.get("age") and self._pi["age"].setText(age)
        self._pi.get("sex") and self._pi["sex"].setText(sex)
        self._pi.get("contact") and self._pi["contact"].setText(contact)

        # Prefer visit-scoped height/weight/bmi from emr_visit_details when available.
        h = p.get("height_cm")
        w = p.get("weight_kg")
        bmi_val = None
        try:
            qid = int(self._queue_entry_id or 0)
        except (TypeError, ValueError):
            qid = 0
        if qid:
            vd = emr.get_visit_details(qid) or {}
            if vd:
                if vd.get("height_cm") is not None:
                    h = vd.get("height_cm")
                if vd.get("weight_kg") is not None:
                    w = vd.get("weight_kg")
                bmi_val = vd.get("bmi")
        h_txt = self._fmt_num(h, " cm")
        w_txt = self._fmt_num(w, " kg")
        bmi_txt = self._fmt_num(bmi_val, "") if bmi_val not in (None, "", 0, "0") else "-"
        try:
            hv = float(h) if h is not None else 0.0
            wv = float(w) if w is not None else 0.0
            if hv > 0 and wv > 0:
                bmi = wv / ((hv / 100.0) ** 2)
                if bmi_txt == "-":
                    bmi_txt = f"{round(bmi, 1)}"
        except (TypeError, ValueError, ZeroDivisionError):
            bmi_txt = "-"

        self._pi.get("height") and self._pi["height"].setText(h_txt)
        self._pi.get("weight") and self._pi["weight"].setText(w_txt)
        self._pi.get("bmi") and self._pi["bmi"].setText(bmi_txt)

        # Vital Signs (if available on the embedded ScreeningPage; otherwise show "-").
        if hasattr(self, "_vs") and isinstance(self._vs, dict):
            va_l = getattr(getattr(self.screening, "va_left", None), "text", lambda: "")()
            va_r = getattr(getattr(self.screening, "va_right", None), "text", lambda: "")()
            va_l = str(va_l or "").strip()
            va_r = str(va_r or "").strip()
            va = " | ".join([x for x in [f"L: {va_l}" if va_l else "", f"R: {va_r}" if va_r else ""] if x]) or "-"
            bp_s = getattr(getattr(self.screening, "bp_systolic", None), "value", lambda: 0)()
            bp_d = getattr(getattr(self.screening, "bp_diastolic", None), "value", lambda: 0)()
            bp = f"{bp_s}/{bp_d} mmHg" if int(bp_s or 0) and int(bp_d or 0) else "-"
            fbs = getattr(getattr(self.screening, "fbs", None), "value", lambda: 0)()
            rbs = getattr(getattr(self.screening, "rbs", None), "value", lambda: 0)()
            bg_parts = []
            if int(fbs or 0):
                bg_parts.append(f"Fasting: {int(fbs)}")
            if int(rbs or 0):
                bg_parts.append(f"Random: {int(rbs)}")
            bg = " | ".join(bg_parts) or "-"
            self._vs.get("va") and self._vs["va"].setText(va)
            self._vs.get("bp") and self._vs["bp"].setText(bp)
            self._vs.get("bg") and self._vs["bg"].setText(bg)
            self._vs.get("symptoms") and self._vs["symptoms"].setText(self._symptom_summary())

        # Clinical history from EMR patient fields
        if hasattr(self, "_ch") and isinstance(self._ch, dict):
            diabetes_type = self._screening_choice_text(getattr(self.screening, "diabetes_type", None), blank_values={"Select"})
            if diabetes_type == "-":
                diabetes_type = str(p.get("diabetes_type") or "-").strip() or "-"
            self._ch.get("diabetes_type") and self._ch["diabetes_type"].setText(diabetes_type)
            diagnosis_date = str(getattr(getattr(self.screening, "diabetes_diagnosis_date", None), "text", lambda: "")() or "").strip() or "-"
            self._ch.get("diagnosis_date") and self._ch["diagnosis_date"].setText(diagnosis_date)
            dm = getattr(getattr(self.screening, "diabetes_duration", None), "value", lambda: p.get("dm_duration_years") or 0)()
            self._ch.get("dm_duration_years") and self._ch["dm_duration_years"].setText(
                f"Duration: {dm} years" if dm not in (None, "", 0, "0") else "-"
            )
            ha = getattr(getattr(self.screening, "hba1c", None), "value", lambda: p.get("hba1c") or 0)()
            self._ch.get("hba1c") and self._ch["hba1c"].setText(str(ha) if ha not in (None, "") else "-")
            treatment_regimen = self._screening_choice_text(
                getattr(self.screening, "treatment_regimen", None),
                blank_values={"Select"},
            )
            self._ch.get("treatment_regimen") and self._ch["treatment_regimen"].setText(treatment_regimen)
            prev_dr_stage = self._screening_choice_text(
                getattr(self.screening, "prev_dr_stage", None),
                blank_values={"Select"},
            )
            self._ch.get("prev_dr_stage") and self._ch["prev_dr_stage"].setText(prev_dr_stage)
            prev = "Yes" if getattr(getattr(self.screening, "prev_treatment", None), "isChecked", lambda: False)() else (
                str(p.get("previous_eye_treatment") or "-").strip() or "-"
            )
            self._ch.get("prev_treatment") and self._ch["prev_treatment"].setText(
                str(prev or "-").strip() or "-"
            )

    def _open_edit_dialog(self) -> None:
        p = self._emr_patient or {}
        pid_pk = p.get("patient_id")
        if pid_pk is None:
            QMessageBox.warning(self, "Edit", "Missing EMR patient id.")
            return
        uid = emr.get_user_id(self.username) if self.username else None
        if not uid:
            QMessageBox.warning(self, "Edit", "Could not resolve current user.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit patient info")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()

        in_first = QLineEdit(str(p.get("first_name") or ""))
        in_last = QLineEdit(str(p.get("last_name") or ""))
        in_contact = QLineEdit(str(p.get("contact_number") or ""))
        in_sex = QComboBox()
        in_sex.addItems(["", "Male", "Female", "Other"])
        sx = str(p.get("sex") or "")
        if sx:
            idx = in_sex.findText(sx)
            if idx >= 0:
                in_sex.setCurrentIndex(idx)

        in_dob = QDateEdit()
        in_dob.setCalendarPopup(True)
        in_dob.setDisplayFormat("yyyy-MM-dd")
        dob_s = str(p.get("date_of_birth") or "")[:10]
        qd = QDate.fromString(dob_s, "yyyy-MM-dd")
        if qd.isValid():
            in_dob.setDate(qd)

        form.addRow("First name", in_first)
        form.addRow("Last name", in_last)
        form.addRow("Date of birth", in_dob)
        form.addRow("Sex", in_sex)
        form.addRow("Contact", in_contact)

        in_height = QDoubleSpinBox()
        in_height.setRange(0, 300)
        in_height.setDecimals(1)
        in_height.setSuffix(" cm")
        try:
            in_height.setValue(float(p.get("height_cm") or 0))
        except (TypeError, ValueError):
            in_height.setValue(0)
        in_weight = QDoubleSpinBox()
        in_weight.setRange(0, 500)
        in_weight.setDecimals(1)
        in_weight.setSuffix(" kg")
        try:
            in_weight.setValue(float(p.get("weight_kg") or 0))
        except (TypeError, ValueError):
            in_weight.setValue(0)
        in_dm_type = QLineEdit(str(p.get("diabetes_type") or ""))
        in_dm_dur = QSpinBox()
        in_dm_dur.setRange(0, 80)
        try:
            in_dm_dur.setValue(int(float(p.get("dm_duration_years") or 0)))
        except (TypeError, ValueError):
            in_dm_dur.setValue(0)
        in_hba1c = QDoubleSpinBox()
        in_hba1c.setRange(0, 20)
        in_hba1c.setDecimals(1)
        try:
            in_hba1c.setValue(float(p.get("hba1c") or 0))
        except (TypeError, ValueError):
            in_hba1c.setValue(0)
        form.addRow("Height", in_height)
        form.addRow("Weight", in_weight)
        form.addRow(QLabel("Vital Signs"))
        in_va_left = QLineEdit(str(getattr(getattr(self.screening, "va_left", None), "text", lambda: "")() or ""))
        in_va_right = QLineEdit(str(getattr(getattr(self.screening, "va_right", None), "text", lambda: "")() or ""))
        in_bp_systolic = QSpinBox()
        in_bp_systolic.setRange(0, 300)
        in_bp_systolic.setValue(int(getattr(getattr(self.screening, "bp_systolic", None), "value", lambda: 0)() or 0))
        in_bp_diastolic = QSpinBox()
        in_bp_diastolic.setRange(0, 200)
        in_bp_diastolic.setValue(int(getattr(getattr(self.screening, "bp_diastolic", None), "value", lambda: 0)() or 0))
        in_fbs = QSpinBox()
        in_fbs.setRange(0, 600)
        in_fbs.setValue(int(getattr(getattr(self.screening, "fbs", None), "value", lambda: 0)() or 0))
        in_rbs = QSpinBox()
        in_rbs.setRange(0, 600)
        in_rbs.setValue(int(getattr(getattr(self.screening, "rbs", None), "value", lambda: 0)() or 0))
        in_symptom_blurred = QCheckBox("Blurred vision")
        in_symptom_blurred.setChecked(bool(getattr(getattr(self.screening, "symptom_blurred", None), "isChecked", lambda: False)()))
        in_symptom_floaters = QCheckBox("Floaters")
        in_symptom_floaters.setChecked(bool(getattr(getattr(self.screening, "symptom_floaters", None), "isChecked", lambda: False)()))
        in_symptom_flashes = QCheckBox("Flashes")
        in_symptom_flashes.setChecked(bool(getattr(getattr(self.screening, "symptom_flashes", None), "isChecked", lambda: False)()))
        in_symptom_vision_loss = QCheckBox("Vision loss")
        in_symptom_vision_loss.setChecked(bool(getattr(getattr(self.screening, "symptom_vision_loss", None), "isChecked", lambda: False)()))
        in_symptom_other = QLineEdit(str(getattr(getattr(self.screening, "symptom_other", None), "text", lambda: "")() or ""))
        symptoms_row = QWidget()
        symptoms_layout = QVBoxLayout(symptoms_row)
        symptoms_layout.setContentsMargins(0, 0, 0, 0)
        symptoms_layout.setSpacing(6)
        for checkbox in (in_symptom_blurred, in_symptom_floaters, in_symptom_flashes, in_symptom_vision_loss):
            symptoms_layout.addWidget(checkbox)
        symptoms_layout.addWidget(in_symptom_other)
        form.addRow("Visual acuity - Left", in_va_left)
        form.addRow("Visual acuity - Right", in_va_right)
        form.addRow("BP systolic", in_bp_systolic)
        form.addRow("BP diastolic", in_bp_diastolic)
        form.addRow("Fasting blood sugar", in_fbs)
        form.addRow("Random blood sugar", in_rbs)
        form.addRow("Symptoms", symptoms_row)

        form.addRow(QLabel("Clinical History"))
        in_dm_type = QComboBox()
        in_dm_type.addItems(["Select", "Type 1", "Type 2", "Gestational", "Other"])
        current_dm_type = str(p.get("diabetes_type") or "")
        if current_dm_type:
            idx = in_dm_type.findText(current_dm_type)
            if idx >= 0:
                in_dm_type.setCurrentIndex(idx)
        form.addRow("DM duration (years)", in_dm_dur)
        form.addRow("HbA1c", in_hba1c)
        in_diagnosis_date = QLineEdit(str(getattr(getattr(self.screening, "diabetes_diagnosis_date", None), "text", lambda: "")() or ""))
        in_diagnosis_date.setPlaceholderText("dd/mm/yyyy")
        in_treatment_regimen = QComboBox()
        if hasattr(self.screening, "treatment_regimen") and hasattr(self.screening.treatment_regimen, "itemText"):
            for i in range(self.screening.treatment_regimen.count()):
                in_treatment_regimen.addItem(self.screening.treatment_regimen.itemText(i))
            self._set_combo_value(
                in_treatment_regimen,
                self._screening_choice_text(getattr(self.screening, "treatment_regimen", None), blank_values={"Select"}),
                blank_values={"Select"},
            )
        in_prev_dr_stage = QComboBox()
        if hasattr(self.screening, "prev_dr_stage") and hasattr(self.screening.prev_dr_stage, "itemText"):
            for i in range(self.screening.prev_dr_stage.count()):
                in_prev_dr_stage.addItem(self.screening.prev_dr_stage.itemText(i))
            self._set_combo_value(
                in_prev_dr_stage,
                self._screening_choice_text(getattr(self.screening, "prev_dr_stage", None), blank_values={"Select"}),
                blank_values={"Select"},
            )
        in_prev_treatment = QCheckBox("Previous DR Treatment")
        in_prev_treatment.setChecked(bool(getattr(getattr(self.screening, "prev_treatment", None), "isChecked", lambda: False)()))
        form.addRow("Diabetes type", in_dm_type)
        form.addRow("Diagnosis date", in_diagnosis_date)
        form.addRow("Treatment regimen", in_treatment_regimen)
        form.addRow("Previous DR stage", in_prev_dr_stage)
        form.addRow("", in_prev_treatment)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        b_cancel = QPushButton("Cancel")
        b_save = QPushButton("Save")
        b_save.setDefault(True)
        btn_row.addWidget(b_cancel)
        btn_row.addWidget(b_save)
        lay.addLayout(btn_row)

        def _save():
            if in_bp_systolic.value() and in_bp_diastolic.value() and in_bp_diastolic.value() >= in_bp_systolic.value():
                QMessageBox.warning(dlg, "Vital Signs", "Diastolic pressure must be lower than systolic pressure.")
                return
            fields = {
                "first_name": in_first.text().strip() or None,
                "last_name": in_last.text().strip() or None,
                "date_of_birth": in_dob.date().toString("yyyy-MM-dd") if in_dob.date().isValid() else None,
                "sex": in_sex.currentText().strip() or None,
                "contact_number": in_contact.text().strip() or None,
                "previous_eye_treatment": "Laser/Injection" if in_prev_treatment.isChecked() else None,
            }
            original_emr = {
                "first_name": str(p.get("first_name") or "").strip() or None,
                "last_name": str(p.get("last_name") or "").strip() or None,
                "date_of_birth": str(p.get("date_of_birth") or "").strip() or None,
                "sex": str(p.get("sex") or "").strip() or None,
                "contact_number": str(p.get("contact_number") or "").strip() or None,
                "previous_eye_treatment": str(p.get("previous_eye_treatment") or "").strip() or None,
            }
            emr_dirty = original_emr != fields
            if emr_dirty:
                ok = emr.update_patient_fields(int(pid_pk), fields, int(uid), action="DOCTOR_UPDATE_PATIENT", target_type="patient")
                if not ok:
                    QMessageBox.warning(dlg, "Save", "Could not update patient information.")
                    return
            self._emr_patient = emr.get_patient(int(pid_pk)) or self._emr_patient
            # Re-apply to screening context (even if hidden) for consistency.
            if hasattr(self.screening, "apply_emr_context"):
                self.screening.apply_emr_context(self._emr_patient, queue_entry_id=self._queue_entry_id)

            # Persist visit-scoped data to emr_visit_details (so it shows on doctor cards & stays date-specific).
            try:
                qid = int(self._queue_entry_id or 0)
            except (TypeError, ValueError):
                qid = 0
            if qid:
                visit_details = {
                    "visual_acuity_left": in_va_left.text().strip(),
                    "visual_acuity_right": in_va_right.text().strip(),
                    "blood_pressure_systolic": int(in_bp_systolic.value()) if in_bp_systolic.value() > 0 else None,
                    "blood_pressure_diastolic": int(in_bp_diastolic.value()) if in_bp_diastolic.value() > 0 else None,
                    "fasting_blood_sugar": float(in_fbs.value()) if in_fbs.value() > 0 else None,
                    "random_blood_sugar": float(in_rbs.value()) if in_rbs.value() > 0 else None,
                    "diabetes_type": (in_dm_type.currentText().strip() if in_dm_type.currentText().strip() != "Select" else None),
                    "dm_duration_years": float(in_dm_dur.value()) if in_dm_dur.value() > 0 else None,
                    "hba1c": float(in_hba1c.value()) if in_hba1c.value() > 0 else None,
                    "diabetes_diagnosis_date": in_diagnosis_date.text().strip() or None,
                    "treatment_regimen": in_treatment_regimen.currentText().strip() or None,
                    "prev_dr_stage": in_prev_dr_stage.currentText().strip() or None,
                    "prev_treatment": "Yes" if in_prev_treatment.isChecked() else "No",
                    "symptom_blurred_vision": 1 if in_symptom_blurred.isChecked() else 0,
                    "symptom_floaters": 1 if in_symptom_floaters.isChecked() else 0,
                    "symptom_flashes": 1 if in_symptom_flashes.isChecked() else 0,
                    "symptom_vision_loss": 1 if in_symptom_vision_loss.isChecked() else 0,
                    "symptom_other": in_symptom_other.text().strip() or None,
                    "height_cm": float(in_height.value()) if in_height.value() > 0 else None,
                    "weight_kg": float(in_weight.value()) if in_weight.value() > 0 else None,
                }
                emr.upsert_visit_details(queue_id=qid, patient_id=int(pid_pk), captured_by=int(uid), details=visit_details)
            self._refresh_screening_fields_from_dialog(
                {
                    "va_left": in_va_left.text().strip(),
                    "va_right": in_va_right.text().strip(),
                    "bp_systolic": in_bp_systolic.value(),
                    "bp_diastolic": in_bp_diastolic.value(),
                    "fbs": in_fbs.value(),
                    "rbs": in_rbs.value(),
                    "symptom_blurred": in_symptom_blurred.isChecked(),
                    "symptom_floaters": in_symptom_floaters.isChecked(),
                    "symptom_flashes": in_symptom_flashes.isChecked(),
                    "symptom_vision_loss": in_symptom_vision_loss.isChecked(),
                    "symptom_other": in_symptom_other.text().strip(),
                    "diabetes_type": in_dm_type.currentText().strip(),
                    "dm_duration_years": in_dm_dur.value(),
                    "hba1c": in_hba1c.value(),
                    "diagnosis_date": in_diagnosis_date.text().strip(),
                    "treatment_regimen": in_treatment_regimen.currentText().strip(),
                    "prev_dr_stage": in_prev_dr_stage.currentText().strip(),
                    "prev_treatment": in_prev_treatment.isChecked(),
                }
            )
            self._refresh_patient_card()
            dlg.accept()

        b_cancel.clicked.connect(dlg.reject)
        b_save.clicked.connect(_save)
        dlg.exec()

    def start_for_patient(self, emr_patient: dict, *, queue_entry_id: int | None = None) -> None:
        self._emr_patient = dict(emr_patient or {})
        self._queue_entry_id = int(queue_entry_id) if queue_entry_id is not None else None
        self._refresh_patient_card()

        # Ensure the hosted screening page has session context.
        if self.username:
            setattr(self.screening, "username", self.username)
        if self.display_name:
            setattr(self.screening, "display_name", self.display_name)
        if self.role:
            setattr(self.screening, "role", self.role)
            if hasattr(self.screening, "configure_role_permissions"):
                self.screening.configure_role_permissions(self.role)

        # Always reset without confirmation to avoid the unsaved popup.
        if hasattr(self.screening, "reset_screening"):
            self.screening.reset_screening(confirm_unsaved=False)

        if hasattr(self.screening, "apply_emr_context"):
            self.screening.apply_emr_context(emr_patient, queue_entry_id=queue_entry_id)
        # Enable doctor diagnosis mode to bypass intake validation prompts on Analyze.
        setattr(self.screening, "_doctor_queue_mode", True)

        # Populate saved visit-scoped details (captured by front desk) into ScreeningPage widgets.
        try:
            qid = int(queue_entry_id) if queue_entry_id is not None else 0
        except (TypeError, ValueError):
            qid = 0
        if qid:
            vd = emr.get_visit_details(qid) or {}
            if vd:
                self._refresh_screening_fields_from_dialog(
                    {
                        "va_left": vd.get("visual_acuity_left") or "",
                        "va_right": vd.get("visual_acuity_right") or "",
                        "bp_systolic": vd.get("blood_pressure_systolic") or 0,
                        "bp_diastolic": vd.get("blood_pressure_diastolic") or 0,
                        "fbs": vd.get("fasting_blood_sugar") or 0,
                        "rbs": vd.get("random_blood_sugar") or 0,
                        "symptom_blurred": bool(vd.get("symptom_blurred_vision")),
                        "symptom_floaters": bool(vd.get("symptom_floaters")),
                        "symptom_flashes": bool(vd.get("symptom_flashes")),
                        "symptom_vision_loss": bool(vd.get("symptom_vision_loss")),
                        "symptom_other": vd.get("symptom_other") or "",
                        "diabetes_type": vd.get("diabetes_type") or "",
                        "dm_duration_years": vd.get("dm_duration_years") or 0,
                        "hba1c": vd.get("hba1c") or 0,
                        "diagnosis_date": vd.get("diabetes_diagnosis_date") or "",
                        "treatment_regimen": vd.get("treatment_regimen") or "",
                        "prev_dr_stage": vd.get("prev_dr_stage") or "",
                        "prev_treatment": str(vd.get("prev_treatment") or "").strip().lower() in {"1", "true", "yes", "y"},
                        "height_cm": vd.get("height_cm") or 0,
                        "weight_kg": vd.get("weight_kg") or 0,
                    }
                )
                self._refresh_patient_card()

        # Initialize eye selection (default Right Eye) and push into ScreeningPage state.
        if hasattr(self, "eye_combo"):
            current = ""
            if hasattr(self.screening, "p_eye"):
                current = str(self.screening.p_eye.currentText() or "").strip()
            if current not in {"Right Eye", "Left Eye"}:
                current = "Right Eye"
            self.eye_combo.blockSignals(True)
            self.eye_combo.setCurrentText(current)
            self.eye_combo.blockSignals(False)
            self._on_eye_changed(current)

        # Lock patient context inside ScreeningPage (we provide edits via the card above).
        if hasattr(self.screening, "_set_patient_context_locked"):
            try:
                self.screening._set_patient_context_locked(True)
            except Exception:
                pass

        # Hide the full intake panels; this screen should only show fundus upload + results.
        splitter = getattr(self.screening, "_intake_splitter", None)
        if splitter is not None and hasattr(splitter, "widget") and splitter.count() >= 2:
            try:
                left = splitter.widget(0)
                if left is not None:
                    left.setVisible(False)
                splitter.setSizes([0, 1000])
            except Exception:
                pass

        # Recompute the left-side cards now that ScreeningPage has been populated.
        self._refresh_patient_card()

        # Put focus on upload.
        if hasattr(self.screening, "btn_upload"):
            self.screening.btn_upload.setFocus()

    def is_busy(self) -> bool:
        if hasattr(self.screening, "is_navigation_locked") and self.screening.is_navigation_locked():
            return True
        worker = getattr(self.screening, "_worker", None)
        if worker is not None and hasattr(worker, "isRunning") and worker.isRunning():
            return True
        return False

