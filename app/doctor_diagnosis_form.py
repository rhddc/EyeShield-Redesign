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
    QSizePolicy,
    QGridLayout,
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


# ── Shared style tokens ────────────────────────────────────────────────────────
_CARD_BG   = "background:#ffffff;border:none;border-radius:8px;"
_LBL_KEY   = "font-size:9px;color:#94a3b8;font-weight:600;letter-spacing:0.4px;text-transform:uppercase;"
_LBL_VAL   = "font-size:11px;color:#0f172a;font-weight:700;"
_CARD_TTL  = "font-size:11px;font-weight:800;color:#0f172a;letter-spacing:-0.2px;"
_DIVIDER   = "background:#f1f5f9;max-height:1px;min-height:1px;border:none;"


def _key_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_LBL_KEY)
    return lbl


def _val_label(text: str = "—") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_LBL_VAL)
    lbl.setWordWrap(True)
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setStyleSheet(_DIVIDER)
    line.setFixedHeight(1)
    return line


class DoctorDiagnosisForm(QWidget):
    """
    Doctor-focused diagnosis form — compact redesign.

    Implementation note:
    We intentionally host a *fresh* ScreeningPage instance here so a doctor starting
    diagnosis from the queue never inherits unsaved state from prior flows.
    """

    back_requested = Signal()
    screening_history_requested = Signal(int, int)
    patient_record_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.username: str = ""
        self.role: str = ""
        self.display_name: str = ""

        self._emr_patient: dict = {}
        self._queue_entry_id: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Compact page header ────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet("QFrame{background:transparent;}")
        header.setFixedHeight(48)
        self._header = header
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.setSpacing(8)

        title = QLabel("Diagnosis")
        title.setStyleSheet("font-size:16px;font-weight:800;color:#0f172a;")
        subtitle = QLabel("Upload a fundus image to start analysis.")
        subtitle.setStyleSheet("font-size:11px;color:#94a3b8;font-weight:500;")
        hl.addWidget(title)
        hl.addWidget(subtitle, 1, Qt.AlignVCenter)

        root.addWidget(header, 0)

        # Thin separator under header
        root.addWidget(_divider())

        # ── Content row ────────────────────────────────────────────────────────
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        content_row = QHBoxLayout(content)
        content_row.setContentsMargins(16, 10, 16, 10)
        content_row.setSpacing(10)
        self._content_row = content_row

        # Left panel: patient info + clinical history
        left = QWidget()
        left.setStyleSheet("background:transparent;")
        left.setMinimumWidth(420)
        left.setMaximumWidth(600)
        self._left_panel = left
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(6)
        left_l.addWidget(self._build_patient_info_card())
        left_l.addWidget(self._build_clinical_history_card())
        # No addStretch — cards should fill exactly what they need

        # ── Screening page (fundus upload + results) ───────────────────────────
        self.screening = ScreeningPage()
        self.screening.p_eye.currentTextChanged.connect(self._sync_eye_combo_from_screening)
        self._bind_screening_summary_refresh()
        self.screening._post_save_history_handler = self._on_queue_save_completed
        if hasattr(self.screening, "stacked_widget"):
            self.screening.stacked_widget.currentChanged.connect(self._update_results_focus_mode)

        if hasattr(self.screening, "set_embedded_compact"):
            try:
                self.screening.set_embedded_compact(True, max_width=480)
            except Exception:
                pass
        self.screening.setMinimumWidth(300)
        self.screening.setMaximumWidth(480)
        self.screening.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        if hasattr(self.screening, "_upload_card"):
            try:
                self.screening._upload_card.setMaximumWidth(480)
            except Exception:
                pass

        content_row.addWidget(left, 6)
        content_row.addWidget(self.screening, 5)

        root.addWidget(content, 1)
        self._update_results_focus_mode(0)

    # ── Style helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _edit_button_stylesheet() -> str:
        return (
            "QPushButton{background:#f1f5f9;border:none;border-radius:6px;"
            "color:#475569;font-size:10px;font-weight:700;padding:0 8px;}"
            "QPushButton:hover{background:#e2e8f0;color:#0f172a;}"
        )

    def _card_frame(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame{{{_CARD_BG}}}")
        return card

    def _add_card_header(
        self, layout: QVBoxLayout, title_text: str, *, with_edit: bool = False
    ) -> None:
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(6)
        title = QLabel(title_text)
        title.setStyleSheet(_CARD_TTL)
        hdr.addWidget(title)
        hdr.addStretch(1)
        if with_edit:
            btn = QPushButton("Edit")
            btn.setFixedSize(44, 22)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._edit_button_stylesheet())
            btn.clicked.connect(self._open_edit_dialog)
            hdr.addWidget(btn)
        layout.addLayout(hdr)

    # ── Signal bindings ────────────────────────────────────────────────────────

    def _bind_screening_summary_refresh(self) -> None:
        for name, signal_name in (
            ("diabetes_type", "currentTextChanged"),
            ("diabetes_duration", "valueChanged"),
            ("diabetes_diagnosis_date", "textChanged"),
            ("treatment_regimen", "currentTextChanged"),
            ("prev_dr_stage", "currentTextChanged"),
            ("prev_treatment", "toggled"),
        ):
            widget = getattr(self.screening, name, None)
            signal = getattr(widget, signal_name, None) if widget is not None else None
            if signal is not None:
                signal.connect(lambda *args: self._refresh_patient_card())

    # ── Patient info card ──────────────────────────────────────────────────────

    def _build_patient_info_card(self) -> QWidget:
        card = self._card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)
        self._add_card_header(v, "Patient Information", with_edit=True)
        v.addWidget(_divider())

        self._pi = {}

        # 3-column grid: Name/ID/DoB on left block, Age/Sex/Contact on right
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(3)

        fields_left  = [("name", "Patient Name"), ("code", "Patient ID"), ("dob", "Date of Birth")]
        fields_right = [("age", "Age"), ("sex", "Sex"), ("contact", "Contact")]

        for row_i, (key, label) in enumerate(fields_left):
            grid.addWidget(_key_label(label), row_i * 2,     0)
            self._pi[key] = _val_label()
            grid.addWidget(self._pi[key],     row_i * 2 + 1, 0)

        for row_i, (key, label) in enumerate(fields_right):
            grid.addWidget(_key_label(label), row_i * 2,     1)
            self._pi[key] = _val_label()
            grid.addWidget(self._pi[key],     row_i * 2 + 1, 1)

        v.addLayout(grid)

        # Anthropometrics row — single horizontal strip
        anthro = QHBoxLayout()
        anthro.setContentsMargins(0, 2, 0, 0)
        anthro.setSpacing(0)
        for key, label in [("height", "Height"), ("weight", "Weight"), ("bmi", "BMI")]:
            blk = QVBoxLayout()
            blk.setSpacing(2)
            blk.addWidget(_key_label(label))
            self._pi[key] = _val_label()
            blk.addWidget(self._pi[key])
            anthro.addLayout(blk, 1)
        v.addLayout(anthro)

        # Eye selector — compact inline row
        v.addWidget(_divider())
        eye_row = QHBoxLayout()
        eye_row.setContentsMargins(0, 0, 0, 0)
        eye_row.setSpacing(8)
        eye_lbl = QLabel("Eye to Screen")
        eye_lbl.setStyleSheet(_LBL_KEY)
        self.eye_combo = QComboBox()
        # Start blank so clinician explicitly selects an eye.
        self.eye_combo.addItems(["", "Right Eye", "Left Eye"])
        self.eye_combo.setCursor(Qt.PointingHandCursor)
        self.eye_combo.setFixedHeight(26)
        self.eye_combo.setMaximumWidth(130)
        self.eye_combo.setStyleSheet(
            "QComboBox{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;"
            "padding:2px 8px;font-size:10px;font-weight:700;color:#0f172a;}"
            "QComboBox::drop-down{border:none;width:18px;}"
        )
        self.eye_combo.currentTextChanged.connect(self._on_eye_changed)
        eye_row.addWidget(eye_lbl, 0, Qt.AlignVCenter)
        eye_row.addWidget(self.eye_combo)
        eye_row.addStretch(1)
        v.addLayout(eye_row)

        return card

    # ── Clinical history card ──────────────────────────────────────────────────

    def _build_clinical_history_card(self) -> QWidget:
        card = self._card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)
        self._add_card_header(v, "Clinical History", with_edit=True)
        v.addWidget(_divider())

        self._ch = {}

        # Two-column compact grid
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(3)

        left_fields = [
            ("diabetes_type",    "DM Type"),
            ("diagnosis_date",   "Dx Date"),
            ("dm_duration_years","DM Duration"),
        ]
        right_fields = [
            ("treatment_regimen","Regimen"),
            ("prev_dr_stage",    "Prev DR Stage"),
            ("prev_treatment",   "Prev Treatment"),
        ]

        for row_i, (key, label) in enumerate(left_fields):
            grid.addWidget(_key_label(label),   row_i * 2,     0)
            self._ch[key] = _val_label()
            grid.addWidget(self._ch[key],       row_i * 2 + 1, 0)

        for row_i, (key, label) in enumerate(right_fields):
            grid.addWidget(_key_label(label),   row_i * 2,     1)
            self._ch[key] = _val_label()
            grid.addWidget(self._ch[key],       row_i * 2 + 1, 1)

        v.addLayout(grid)
        return card

    # ── Eye sync ───────────────────────────────────────────────────────────────

    def _on_eye_changed(self, label: str) -> None:
        if not hasattr(self, "screening") or not hasattr(self.screening, "p_eye"):
            return
        selected = str(label or "").strip()
        if not selected:
            # Keep ScreeningPage in "no eye selected" state until the user chooses.
            with contextlib.suppress(Exception):
                self.screening.p_eye.setCurrentIndex(0)
            return
        if hasattr(self.screening, "_suspend_eye_guard"):
            self.screening._suspend_eye_guard = True
        try:
            self.screening.p_eye.setCurrentText(selected)
        finally:
            if hasattr(self.screening, "_suspend_eye_guard"):
                self.screening._suspend_eye_guard = False

    def _sync_eye_combo_from_screening(self, text: str) -> None:
        """Keep the diagnosis header eye selector aligned when screening switches eyes."""
        if not hasattr(self, "eye_combo"):
            return
        t = str(text or "").strip()
        if not t or self.eye_combo.currentText() == t:
            return
        self.eye_combo.blockSignals(True)
        self.eye_combo.setCurrentText(t)
        self.eye_combo.blockSignals(False)

    # ── Queue / visit lifecycle ────────────────────────────────────────────────

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

        box = QMessageBox(self)
        box.setWindowTitle("Visit Completed")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Visit is complete. The patient was removed from the queue.")
        view_btn = box.addButton("View Patient Record", QMessageBox.ButtonRole.AcceptRole)
        back_btn = box.addButton("Back to Queue", QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(view_btn)
        box.exec()

        if box.clickedButton() == view_btn:
            code = str((self._emr_patient or {}).get("patient_code") or "").strip()
            if code:
                self.patient_record_requested.emit(code)
                return
        self.back_requested.emit()

    def _update_results_focus_mode(self, index: int) -> None:
        """When results are shown, give the results page the full diagnosis width."""
        on_results = int(index) == 1
        if hasattr(self, "_left_panel"):
            self._left_panel.setVisible(not on_results)
        if hasattr(self, "_header"):
            self._header.setVisible(not on_results)
        # The embedded ScreeningPage is constrained for the upload column, but results
        # should use the full available width.
        if hasattr(self, "screening") and self.screening is not None:
            try:
                if on_results:
                    if hasattr(self.screening, "set_embedded_compact"):
                        # Keep embedded mode, but remove the narrow max width.
                        self.screening.set_embedded_compact(True, max_width=2000)
                    self.screening.setMaximumWidth(16777215)
                    self.screening.setMinimumWidth(0)
                    self.screening.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                else:
                    if hasattr(self.screening, "set_embedded_compact"):
                        self.screening.set_embedded_compact(True, max_width=480)
                    self.screening.setMinimumWidth(300)
                    self.screening.setMaximumWidth(480)
                    self.screening.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            except Exception:
                pass
        if hasattr(self, "_content_row"):
            self._content_row.setContentsMargins(
                0 if on_results else 16,
                0,
                0 if on_results else 16,
                0 if on_results else 10,
            )
            self._content_row.setSpacing(0 if on_results else 10)

    # ── Static helpers ─────────────────────────────────────────────────────────

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
        return f"{max(age, 0)} yrs"

    @staticmethod
    def _fmt_num(v, suffix: str) -> str:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return "—"
        if fv <= 0:
            return "—"
        return f"{round(fv, 1)}{suffix}"

    @staticmethod
    def _screening_choice_text(widget, *, blank_values: set[str] | None = None) -> str:
        blank_values = blank_values or set()
        text = str(getattr(widget, "currentText", lambda: "")() or "").strip()
        if not text or text in blank_values:
            return "-"
        return text

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

    # ── Screening field sync ───────────────────────────────────────────────────

    def _refresh_screening_fields_from_dialog(self, values: dict) -> None:
        self._set_combo_value(getattr(self.screening, "diabetes_type", None), values.get("diabetes_type") or "", blank_values={"Select"})
        self._set_spinbox_value(getattr(self.screening, "diabetes_duration", None), int(values.get("dm_duration_years") or 0))
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

    # ── Card data refresh ──────────────────────────────────────────────────────

    def _refresh_patient_card(self) -> None:
        p = self._emr_patient or {}
        fn = str(p.get("first_name") or "").strip()
        ln = str(p.get("last_name") or "").strip()
        name    = f"{fn} {ln}".strip() or "—"
        code    = str(p.get("patient_code") or "—").strip() or "—"
        dob     = str(p.get("date_of_birth") or "")[:10] or "—"
        age     = self._compute_age(dob)
        sex     = str(p.get("sex") or "—").strip() or "—"
        contact = str(p.get("contact_number") or "—").strip() or "—"

        for key, val in [("name", name), ("code", code), ("dob", dob),
                         ("age", age), ("sex", sex), ("contact", contact)]:
            if key in self._pi:
                self._pi[key].setText(val)

        h, w, bmi_val = p.get("height_cm"), p.get("weight_kg"), None
        try:
            qid = int(self._queue_entry_id or 0)
        except (TypeError, ValueError):
            qid = 0
        vd = {}
        if not qid:
            # Fallback: resolve today's active visit for this patient (diagnosis should be visit-scoped).
            with contextlib.suppress(Exception):
                pid_pk = int(p.get("patient_id") or 0)
                if pid_pk:
                    active = emr.get_today_active_queue_for_patient(pid_pk) or {}
                    qid = int(active.get("queue_id") or 0)
                    if qid and not self._queue_entry_id:
                        self._queue_entry_id = qid
        if qid:
            vd = emr.get_visit_details(qid) or {}
            if vd:
                if vd.get("height_cm") is not None:
                    h = vd.get("height_cm")
                if vd.get("weight_kg") is not None:
                    w = vd.get("weight_kg")
                bmi_val = vd.get("bmi")

        h_txt   = self._fmt_num(h, " cm")
        w_txt   = self._fmt_num(w, " kg")
        bmi_txt = self._fmt_num(bmi_val, "") if bmi_val not in (None, "", 0, "0") else "—"
        try:
            hv, wv = float(h) if h is not None else 0.0, float(w) if w is not None else 0.0
            if hv > 0 and wv > 0 and bmi_txt == "—":
                bmi_txt = f"{round(wv / ((hv / 100.0) ** 2), 1)}"
        except (TypeError, ValueError, ZeroDivisionError):
            pass

        for key, val in [("height", h_txt), ("weight", w_txt), ("bmi", bmi_txt)]:
            if key in self._pi:
                self._pi[key].setText(val)

        if hasattr(self, "_ch") and isinstance(self._ch, dict) and self._ch:
            diabetes_type = str(
                (vd.get("diabetes_type") if vd else None) or p.get("diabetes_type") or "—"
            ).strip() or "—"
            if diabetes_type.lower() == "select":
                diabetes_type = "—"

            diagnosis_date = str(
                (vd.get("diabetes_diagnosis_date") if vd else None)
                or p.get("diabetes_diagnosis_date") or "—"
            ).strip() or "—"

            dm_txt = "—"
            if diagnosis_date and diagnosis_date != "—":
                try:
                    diag_date = QDate.fromString(diagnosis_date, "dd/MM/yyyy")
                    if diag_date.isValid():
                        today = QDate.currentDate()
                        years = today.year() - diag_date.year()
                        if (today.month(), today.day()) < (diag_date.month(), diag_date.day()):
                            years -= 1
                        years = max(0, years)
                        dm_txt = f"{years} yrs" if years > 0 else "—"
                except Exception:
                    pass

            if dm_txt == "—":
                dm = (vd.get("dm_duration_years") if vd else None)
                if dm in (None, "", 0, "0"):
                    dm = p.get("dm_duration_years")
                try:
                    fv = float(dm)
                    dm_txt = f"{int(fv) if fv.is_integer() else round(fv, 1)} yrs" if fv > 0 else "—"
                except (TypeError, ValueError):
                    dm_txt = "—"

            treatment_regimen = str(
                (vd.get("treatment_regimen") if vd else None) or p.get("treatment_regimen") or "—"
            ).strip() or "—"
            prev_dr_stage = str(
                (vd.get("prev_dr_stage") if vd else None) or p.get("prev_dr_stage") or "—"
            ).strip() or "—"

            prev_val = (vd.get("prev_treatment") if vd else None)
            if prev_val in (None, ""):
                prev_val = p.get("previous_eye_treatment")
            prev_txt = str(prev_val or "—").strip() or "—"

            updates = {
                "diabetes_type":    diabetes_type,
                "diagnosis_date":   diagnosis_date,
                "dm_duration_years":dm_txt,
                "treatment_regimen":treatment_regimen,
                "prev_dr_stage":    prev_dr_stage,
                "prev_treatment":   prev_txt,
            }
            for key, val in updates.items():
                if key in self._ch:
                    self._ch[key].setText(val)

    # ── Edit dialog ────────────────────────────────────────────────────────────

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

        in_first   = QLineEdit(str(p.get("first_name") or ""))
        in_last    = QLineEdit(str(p.get("last_name") or ""))
        in_contact = QLineEdit(str(p.get("contact_number") or ""))
        in_sex     = QComboBox()
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

        in_dm_dur = QSpinBox()
        in_dm_dur.setRange(0, 80)
        try:
            in_dm_dur.setValue(int(float(p.get("dm_duration_years") or 0)))
        except (TypeError, ValueError):
            in_dm_dur.setValue(0)

        form.addRow("Height", in_height)
        form.addRow("Weight", in_weight)
        form.addRow(QLabel("Clinical History"))

        in_dm_type = QComboBox()
        in_dm_type.addItems(["Select", "Type 1", "Type 2", "Gestational", "Other"])
        current_dm_type = str(p.get("diabetes_type") or "")
        if current_dm_type:
            idx = in_dm_type.findText(current_dm_type)
            if idx >= 0:
                in_dm_type.setCurrentIndex(idx)

        form.addRow("DM duration (years)", in_dm_dur)

        in_diagnosis_date = QLineEdit(
            str(getattr(getattr(self.screening, "diabetes_diagnosis_date", None), "text", lambda: "")() or "")
        )
        in_diagnosis_date.setPlaceholderText("dd/mm/yyyy")
        in_diagnosis_date.textChanged.connect(
            lambda: self._update_duration_from_diagnosis_date_in_dialog(in_diagnosis_date, in_dm_dur)
        )

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
        in_prev_treatment.setChecked(
            bool(getattr(getattr(self.screening, "prev_treatment", None), "isChecked", lambda: False)())
        )

        form.addRow("Diabetes type", in_dm_type)
        form.addRow("Diagnosis date", in_diagnosis_date)
        form.addRow("Treatment regimen", in_treatment_regimen)
        form.addRow("Previous DR stage", in_prev_dr_stage)
        form.addRow("", in_prev_treatment)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        b_cancel = QPushButton("Cancel")
        b_save   = QPushButton("Save")
        b_save.setDefault(True)
        btn_row.addWidget(b_cancel)
        btn_row.addWidget(b_save)
        lay.addLayout(btn_row)

        def _save():
            fields = {
                "first_name":            in_first.text().strip() or None,
                "last_name":             in_last.text().strip() or None,
                "date_of_birth":         in_dob.date().toString("yyyy-MM-dd") if in_dob.date().isValid() else None,
                "sex":                   in_sex.currentText().strip() or None,
                "contact_number":        in_contact.text().strip() or None,
                "previous_eye_treatment":("Laser/Injection" if in_prev_treatment.isChecked() else None),
            }
            original_emr = {
                "first_name":            str(p.get("first_name") or "").strip() or None,
                "last_name":             str(p.get("last_name") or "").strip() or None,
                "date_of_birth":         str(p.get("date_of_birth") or "").strip() or None,
                "sex":                   str(p.get("sex") or "").strip() or None,
                "contact_number":        str(p.get("contact_number") or "").strip() or None,
                "previous_eye_treatment":str(p.get("previous_eye_treatment") or "").strip() or None,
            }
            emr_dirty = original_emr != fields
            if emr_dirty:
                ok = emr.update_patient_fields(
                    int(pid_pk), fields, int(uid),
                    action="DOCTOR_UPDATE_PATIENT", target_type="patient",
                )
                if not ok:
                    QMessageBox.warning(dlg, "Save", "Could not update patient information.")
                    return

            self._emr_patient = emr.get_patient(int(pid_pk)) or self._emr_patient
            if hasattr(self.screening, "apply_emr_context"):
                self.screening.apply_emr_context(self._emr_patient, queue_entry_id=self._queue_entry_id)

            try:
                qid = int(self._queue_entry_id or 0)
            except (TypeError, ValueError):
                qid = 0
            if qid:
                visit_details = {
                    "diabetes_type":           (in_dm_type.currentText().strip()
                                                if in_dm_type.currentText().strip() != "Select" else None),
                    "dm_duration_years":        float(in_dm_dur.value()) if in_dm_dur.value() > 0 else None,
                    "hba1c":                    None,
                    "diabetes_diagnosis_date":  in_diagnosis_date.text().strip() or None,
                    "treatment_regimen":        in_treatment_regimen.currentText().strip() or None,
                    "prev_dr_stage":            in_prev_dr_stage.currentText().strip() or None,
                    "prev_treatment":           "Yes" if in_prev_treatment.isChecked() else "No",
                    "height_cm":                float(in_height.value()) if in_height.value() > 0 else None,
                    "weight_kg":                float(in_weight.value()) if in_weight.value() > 0 else None,
                }
                emr.upsert_visit_details(
                    queue_id=qid, patient_id=int(pid_pk),
                    captured_by=int(uid), details=visit_details,
                )

            self._refresh_screening_fields_from_dialog(
                {
                    "diabetes_type":    in_dm_type.currentText().strip(),
                    "dm_duration_years":in_dm_dur.value(),
                    "diagnosis_date":   in_diagnosis_date.text().strip(),
                    "treatment_regimen":in_treatment_regimen.currentText().strip(),
                    "prev_dr_stage":    in_prev_dr_stage.currentText().strip(),
                    "prev_treatment":   in_prev_treatment.isChecked(),
                }
            )
            self._refresh_patient_card()
            dlg.accept()

        b_cancel.clicked.connect(dlg.reject)
        b_save.clicked.connect(_save)
        dlg.exec()

    @staticmethod
    def _update_duration_from_diagnosis_date_in_dialog(
        diag_date_edit: QLineEdit, duration_spin: QSpinBox
    ) -> None:
        """Auto-calculate and set duration from diagnosis date in edit dialog."""
        diag_date = QDate.fromString(str(diag_date_edit.text().strip()), "dd/MM/yyyy")
        if not diag_date.isValid():
            return
        today = QDate.currentDate()
        years = today.year() - diag_date.year()
        if (today.month(), today.day()) < (diag_date.month(), diag_date.day()):
            years -= 1
        duration_spin.setValue(max(0, years))

    # ── Public API ─────────────────────────────────────────────────────────────

    def start_for_patient(self, emr_patient: dict, *, queue_entry_id: int | None = None) -> None:
        self._emr_patient = dict(emr_patient or {})
        # Ensure we always carry patient_code (used for navigation to Patient Records).
        if not str(self._emr_patient.get("patient_code") or "").strip():
            with contextlib.suppress(Exception):
                pid_pk = int(self._emr_patient.get("patient_id") or 0)
                if pid_pk:
                    full = emr.get_patient(pid_pk) or {}
                    if str(full.get("patient_code") or "").strip():
                        self._emr_patient["patient_code"] = str(full.get("patient_code") or "").strip()
        self._queue_entry_id = int(queue_entry_id) if queue_entry_id is not None else None
        self._refresh_patient_card()

        if self.username:
            setattr(self.screening, "username", self.username)
        if self.display_name:
            setattr(self.screening, "display_name", self.display_name)
        if self.role:
            setattr(self.screening, "role", self.role)
            if hasattr(self.screening, "configure_role_permissions"):
                self.screening.configure_role_permissions(self.role)

        if hasattr(self.screening, "reset_screening"):
            self.screening.reset_screening(confirm_unsaved=False)

        if hasattr(self.screening, "apply_emr_context"):
            self.screening.apply_emr_context(emr_patient, queue_entry_id=queue_entry_id)
        setattr(self.screening, "_doctor_queue_mode", True)

        try:
            qid = int(queue_entry_id) if queue_entry_id is not None else 0
        except (TypeError, ValueError):
            qid = 0
        if qid:
            vd = emr.get_visit_details(qid) or {}
            if vd:
                self._refresh_screening_fields_from_dialog(
                    {
                        "diabetes_type":    vd.get("diabetes_type") or "",
                        "dm_duration_years":vd.get("dm_duration_years") or 0,
                        "diagnosis_date":   vd.get("diabetes_diagnosis_date") or "",
                        "treatment_regimen":vd.get("treatment_regimen") or "",
                        "prev_dr_stage":    vd.get("prev_dr_stage") or "",
                        "prev_treatment":   str(vd.get("prev_treatment") or "").strip().lower()
                                            in {"1", "true", "yes", "y"},
                        "height_cm":        vd.get("height_cm") or 0,
                        "weight_kg":        vd.get("weight_kg") or 0,
                    }
                )
                self._refresh_patient_card()

        if hasattr(self, "eye_combo"):
            current = ""
            if hasattr(self.screening, "p_eye"):
                current = str(self.screening.p_eye.currentText() or "").strip()
            if current not in {"Right Eye", "Left Eye"}:
                current = ""
            self.eye_combo.blockSignals(True)
            self.eye_combo.setCurrentText(current)
            self.eye_combo.blockSignals(False)
            if current:
                self._on_eye_changed(current)

        if hasattr(self.screening, "_set_patient_context_locked"):
            with contextlib.suppress(Exception):
                self.screening._set_patient_context_locked(True)

        splitter = getattr(self.screening, "_intake_splitter", None)
        if splitter is not None and hasattr(splitter, "widget") and splitter.count() >= 2:
            with contextlib.suppress(Exception):
                left = splitter.widget(0)
                if left is not None:
                    left.setVisible(False)
                splitter.setSizes([0, 1000])

        for wname in (
            "_patient_info_group", "_clinical_history_group",
            "_patient_info_card", "_clinical_history_card",
            "patient_info_group", "clinical_history_group",
        ):
            w = getattr(self.screening, wname, None)
            with contextlib.suppress(Exception):
                if w is not None and hasattr(w, "setVisible"):
                    w.setVisible(False)

        self._refresh_patient_card()

        if hasattr(self.screening, "btn_upload"):
            self.screening.btn_upload.setFocus()

    def is_busy(self) -> bool:
        if hasattr(self.screening, "is_navigation_locked") and self.screening.is_navigation_locked():
            return True
        worker = getattr(self.screening, "_worker", None)
        if worker is not None and hasattr(worker, "isRunning") and worker.isRunning():
            return True
        return False