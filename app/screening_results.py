"""
Results window module for EyeShield EMR application.
Contains the ResultsWindow class and clinical explanation generation.
"""

from datetime import datetime
from html import escape
import contextlib
import json
import os
from pathlib import Path
import re

from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGroupBox,
    QScrollArea, QFrame, QProgressBar, QMessageBox, QFileDialog, QStyle, QProgressDialog, QApplication, QDialog,
    QComboBox, QLineEdit, QTextEdit, QGridLayout, QSizePolicy,
)
from PySide6.QtGui import QPixmap, QFont, QPainter, QColor, QIcon, QPalette, QImage, QPdfWriter, QPageSize, QPageLayout, QTextDocument
from PySide6.QtCore import Qt, QSize, QEvent, QTimer, QByteArray, QBuffer, QIODevice, QMarginsF

try:
    from .screening_styles import DR_COLORS, DR_RECOMMENDATIONS, PROGRESSBAR_STYLE
    from .screening_widgets import ClickableImageLabel
    from .safety_runtime import can_write_directory, get_free_space_mb, write_activity
    from .auth import UserManager
    from .ui_feedback import apply_dialog_style
    from .model_inference import SYSTEM_UNCERTAIN_LABEL
    from .reports import generate_unified_patient_report
except Exception:  # pragma: no cover
    from screening_styles import DR_COLORS, DR_RECOMMENDATIONS, PROGRESSBAR_STYLE
    from screening_widgets import ClickableImageLabel
    from safety_runtime import can_write_directory, get_free_space_mb, write_activity
    from auth import UserManager
    from ui_feedback import apply_dialog_style
    from model_inference import SYSTEM_UNCERTAIN_LABEL
    from reports import generate_unified_patient_report

ICDR_OPTIONS = ["No DR", "Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"]


def _generate_explanation(
    result_class: str,
    confidence_text: str,
    patient_data: dict | None = None,
) -> str:
    """
    Build a personalised clinical explanation from the DR grade,
    model confidence, and the patient's clinical profile.
    Returns HTML-ready text (paragraphs separated by <br><br>).
    """
    pd       = patient_data or {}
    age      = int(pd.get("age",  0))
    hba1c    = float(pd.get("hba1c", 0.0))
    duration = int(pd.get("duration", 0))
    prev_tx  = bool(pd.get("prev_treatment", False))
    d_type   = str(pd.get("diabetes_type", "")).strip()
    eye      = str(pd.get("eye", "")).strip()

    eye_phrase = f"the {eye.lower()}" if eye and eye.lower() not in ("", "select") else "the screened eye"

    #  Opening sentence: finding 
    opening_map = {
        "No DR":            f"No signs of diabetic retinopathy were detected in {eye_phrase}",
        "Mild DR":          f"Early microaneurysms consistent with mild non-proliferative diabetic "
                            f"retinopathy (NPDR) were identified in {eye_phrase}",
        "Moderate DR":      f"Microaneurysms, haemorrhages, and/or hard exudates consistent with "
                            f"moderate non-proliferative diabetic retinopathy (NPDR) were detected "
                            f"in {eye_phrase}",
        "Severe DR":        f"Extensive haemorrhages, venous beading, or intraretinal microvascular "
                            f"abnormalities consistent with severe NPDR were detected in {eye_phrase}",
        "Proliferative DR": f"Neovascularisation indicative of proliferative diabetic retinopathy "
                            f"(PDR)  a sight-threatening condition  was detected in {eye_phrase}",
    }
    paragraphs = [
        opening_map.get(result_class, f"{result_class} was detected in {eye_phrase}")
        + f" ({confidence_text.lower()})."
    ]

    #  Patient context 
    ctx = []
    if age > 0:
        ctx.append(f"{age}yearold")
    if d_type and d_type.lower() not in ("select", ""):
        ctx.append(f"{d_type} diabetes")
    if duration > 0:
        ctx.append(f"{duration}year diabetes duration")
    if ctx:
        paragraphs.append("<b>Patient profile:</b> " + ", ".join(ctx) + ".")

    #  Risk factor commentary 
    risk = []
    if hba1c >= 9.0:
        risk.append(
            f"HbA1c of <b>{hba1c:.1f}%</b> indicates poor glycaemic control, which substantially "
            "increases the risk of retinopathy progression and macular oedema."
        )
    elif hba1c >= 7.5:
        risk.append(
            f"HbA1c of <b>{hba1c:.1f}%</b> is above the recommended target (7.07.5%). "
            "Tighter glycaemic management is advised to slow disease progression."
        )
    elif hba1c > 0.0:
        risk.append(
            f"HbA1c of <b>{hba1c:.1f}%</b> is within an acceptable range. "
            "Continue current glycaemic management strategy."
        )

    if duration >= 15 and result_class != "No DR":
        risk.append(
            f"A diabetes duration of <b>{duration} years</b> is a recognised risk factor for "
            "bilateral retinal involvement; bilateral screening is recommended if not already performed."
        )
    elif result_class in ("Severe DR", "Proliferative DR") and duration >= 10:
        risk.append(
            f"Diabetes duration of <b>{duration} years</b> is consistent with the advanced retinal findings observed."
        )

    if prev_tx and result_class != "No DR":
        risk.append(
            "A history of prior DR treatment requires close monitoring for recurrence, "
            "progression, or treatment-related complications."
        )

    if risk:
        paragraphs.append("<br>".join(risk))

    #  Recommendation 
    rec_map = {
        "No DR":            "Maintain optimal glycaemic and blood pressure control. "
                            "Annual retinal screening is recommended.",
        "Mild DR":          "Intensify glycaemic and blood pressure management. "
                            "Schedule a repeat retinal examination in 612 months.",
        "Moderate DR":      "Ophthalmology referral within 3 months is advised. "
                            "Reassess systemic metabolic control and consider treatment intensification.",
        "Severe DR":        "Urgent ophthalmology referral is required. "
                            "The 1-year risk of progression to proliferative disease is high without intervention.",
        "Proliferative DR": "Immediate ophthalmology referral is required. "
                            "Treatment may include laser photocoagulation, intravitreal anti-VEGF therapy, "
                            "or vitreoretinal surgery.",
    }
    paragraphs.append(
        "<b>Recommendation:</b> "
        + rec_map.get(result_class, "Consult a qualified ophthalmologist for further evaluation.")
    )

    return "<br><br>".join(paragraphs)


class ResultsWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_page = parent
        self.setWindowTitle("Screening Results")
        self.setMinimumSize(850, 500)
        self._icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

        # Report generation state  updated by set_results()
        self._current_image_path   = ""
        self._current_heatmap_path = ""
        self._current_result_class = "Pending"
        self._current_confidence   = ""
        self._current_eye_label    = ""
        self._current_patient_name = ""
        self._first_eye_context    = {}
        self._doctor_classification = "Pending"
        self._decision_mode = "pending"
        self._override_justification = ""
        self._doctor_findings = ""
        self._save_state_timer = QTimer(self)
        self._save_state_timer.setSingleShot(True)
        self._save_state_timer.timeout.connect(self._reset_save_button_default)
        self._uncertainty_pct = 0.0

        # Outer layout
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Scroll Area for responsiveness
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        outer_layout.addWidget(scroll)

        scroll_content = QWidget()
        scroll_content.setObjectName("resultsScrollContent")
        scroll_content.setStyleSheet("QWidget#resultsScrollContent { background: transparent; }")
        scroll.setWidget(scroll_content)

        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # --- Define Header Components ---
        heading_col = QVBoxLayout()
        heading_col.setSpacing(2)
        self.breadcrumb_label = QLabel("SCREENING RESULTS")
        self.breadcrumb_label.setObjectName("crumbLabel")
        heading_col.addWidget(self.breadcrumb_label)

        self.title_label = QLabel("Results")
        self.title_label.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        self.title_label.setObjectName("pageHeader")
        heading_col.addWidget(self.title_label)

        # Removed subtitle_label ("Screening complete") to save space

        pills_row = QHBoxLayout()
        pills_row.setSpacing(8)
        self.eye_badge_label = QLabel("\u2022 Right Eye")
        self.eye_badge_label.setObjectName("infoPill")
        self.eye_badge_label.setMinimumHeight(30)
        pills_row.addWidget(self.eye_badge_label)

        self.save_status_label = QLabel("Saved \u2713")
        self.save_status_label.setObjectName("savedPill")
        self.save_status_label.setMinimumHeight(30)
        self.save_status_label.hide()
        pills_row.addWidget(self.save_status_label)
        pills_row.addStretch(1)
        heading_col.addLayout(pills_row)

        layout.addLayout(heading_col)

        # Progression Summary Panel (Follow-up only)
        self.progression_panel = QFrame()
        self.progression_panel.setObjectName("progressionPanel")
        self.progression_panel.setStyleSheet("""
            QFrame#progressionPanel {
                background: #fdf2f2;
                border: 1px solid #fecaca;
                border-radius: 12px;
                margin-top: 10px;
            }
        """)
        self.progression_panel.hide()
        prog_layout = QVBoxLayout(self.progression_panel)
        prog_layout.setContentsMargins(16, 12, 16, 12)
        prog_layout.setSpacing(6)
        
        prog_header = QLabel("CLINICAL PROGRESSION SUMMARY")
        prog_header.setStyleSheet("font-size: 11px; font-weight: 700; color: #991b1b; letter-spacing: 0.5px;")
        prog_layout.addWidget(prog_header)
        
        self.progression_text = QLabel("Progression detected: Mild -> Moderate")
        self.progression_text.setStyleSheet("font-size: 14px; font-weight: 600; color: #1f2937;")
        prog_layout.addWidget(self.progression_text)
        
        self.progression_trend = QLabel("Trend: Stable")
        self.progression_trend.setStyleSheet("font-size: 13px; color: #4b5563;")
        prog_layout.addWidget(self.progression_trend)
        
        layout.addWidget(self.progression_panel)

        self._loading_bar = QProgressBar()
        self._loading_bar.setRange(0, 0)   # indeterminate / marquee
        self._loading_bar.setFixedHeight(4)
        self._loading_bar.setTextVisible(False)
        self._loading_bar.setStyleSheet("""
            QProgressBar {
                background: #e5e7eb;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: #2563eb;
                border-radius: 2px;
            }
        """)
        self._loading_bar.hide()
        layout.addWidget(self._loading_bar)

        # --- Main Split Layout ---
        main_content_layout = QHBoxLayout()
        main_content_layout.setSpacing(24)
        layout.addLayout(main_content_layout)

        # Left Column: Visual Data & Summary
        left_col = QVBoxLayout()
        left_col.setSpacing(20)
        main_content_layout.addLayout(left_col, 65)

        # Right Column: Analysis, Assessment & Actions
        right_col = QVBoxLayout()
        right_col.setSpacing(20)
        main_content_layout.addLayout(right_col, 35)

        self.btn_back = QPushButton("Back")
        self.btn_back.setObjectName("ghostAction")
        self.btn_back.setMinimumHeight(40)
        self.btn_back.setIconSize(QSize(18, 18))
        self.btn_back.clicked.connect(self.go_back)

        self.btn_save = QPushButton("Save to Patient Record")
        self.btn_save.setObjectName("ghostAction")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.setIconSize(QSize(18, 18))
        self.btn_save.clicked.connect(self.save_patient)

        self.btn_report = QPushButton("Generate Report")
        self.btn_report.setObjectName("ghostAction")
        self.btn_report.setMinimumHeight(40)
        self.btn_report.setIconSize(QSize(18, 18))
        self.btn_report.setEnabled(False)
        self.btn_report.clicked.connect(self.generate_report)

        self.btn_referral = QPushButton("Refer")
        self.btn_referral.setObjectName("ghostAction")
        self.btn_referral.setMinimumHeight(40)
        self.btn_referral.setIconSize(QSize(18, 18))
        self.btn_referral.setEnabled(False)
        self.btn_referral.clicked.connect(self._show_referral_options)

        self.btn_new = QPushButton("New Patient")
        self.btn_new.setObjectName("ghostAction")
        self.btn_new.setMinimumHeight(40)
        self.btn_new.setIconSize(QSize(18, 18))
        self.btn_new.clicked.connect(self.new_patient)
        # Removed from workflow: history replaces PDF-style navigation.
        self.btn_new.hide()





        # --- Populate Columns ---


        # -- Visual Data Cards --
        image_row = QHBoxLayout()
        image_row.setSpacing(16)

        source_card = QGroupBox("")
        source_card.setObjectName("resultGroupCard")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(16, 16, 16, 16)
        source_layout.setSpacing(10)
        source_head = QHBoxLayout()
        source_head.setSpacing(6)
        source_title = QLabel("Source Image - Fundus")
        source_title.setObjectName("cardHeaderLabel")
        source_head.addWidget(source_title)
        source_head.addStretch(1)
        source_expand = QLabel("\u2922")
        source_expand.setObjectName("expandGlyph")
        source_head.addWidget(source_expand)
        source_layout.addLayout(source_head)
        self.source_label = ClickableImageLabel("", "Source Image - Fundus")
        self.source_label.setObjectName("sourceImageSurface")
        self.source_label.setFixedHeight(390)
        self.source_label.setScaledContents(False)
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        source_layout.addWidget(self.source_label)

        heatmap_card = QGroupBox("")
        heatmap_card.setObjectName("resultGroupCard")
        heatmap_layout = QVBoxLayout(heatmap_card)
        heatmap_layout.setContentsMargins(16, 16, 16, 16)
        heatmap_layout.setSpacing(10)
        heatmap_head = QHBoxLayout()
        heatmap_head.setSpacing(6)
        heatmap_title = QLabel("Grad-CAM++ Heatmap")
        heatmap_title.setObjectName("cardHeaderLabel")
        heatmap_head.addWidget(heatmap_title)
        heatmap_head.addStretch(1)
        heatmap_expand = QLabel("\u2922")
        heatmap_expand.setObjectName("expandGlyph")
        heatmap_head.addWidget(heatmap_expand)
        heatmap_layout.addLayout(heatmap_head)
        self.heatmap_label = ClickableImageLabel("", "Grad-CAM++ Heatmap")
        self.heatmap_label.setObjectName("heatmapImageSurface")
        self.heatmap_label.setFixedHeight(390)
        self.heatmap_label.setScaledContents(False)
        self.heatmap_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heatmap_layout.addWidget(self.heatmap_label)

        image_row.addWidget(source_card, 1)
        image_row.addWidget(heatmap_card, 1)
        left_col.addLayout(image_row)

        # -- Actions Card (Right Sidebar) --
        actions_card = QGroupBox("")
        actions_card.setObjectName("resultGroupCard")
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(16, 16, 16, 16)
        actions_layout.setSpacing(10)

        actions_head = QHBoxLayout()
        actions_head.setSpacing(6)
        actions_title = QLabel("Actions")
        actions_title.setObjectName("cardHeaderLabel")
        actions_head.addWidget(actions_title)
        actions_head.addStretch(1)
        actions_layout.addLayout(actions_head)

        actions_hint = QLabel("Workflow shortcuts for save, reports, and navigation.")
        actions_hint.setObjectName("metaText")
        actions_hint.setWordWrap(True)
        actions_layout.addWidget(actions_hint)

        self.save_note_label = QLabel("")
        self.save_note_label.setObjectName("metaText")
        self.save_note_label.setWordWrap(True)
        self.save_note_label.hide()
        actions_layout.addWidget(self.save_note_label)

        actions_grid = QGridLayout()
        actions_grid.setHorizontalSpacing(0)
        actions_grid.setVerticalSpacing(10)
        actions_grid.addWidget(self.btn_save, 0, 0)
        actions_grid.addWidget(self.btn_report, 1, 0)
        actions_grid.addWidget(self.btn_referral, 2, 0)
        actions_grid.addWidget(self.btn_back, 3, 0)
        actions_grid.setColumnStretch(0, 1)
        actions_layout.addLayout(actions_grid)
        actions_layout.addStretch(1)
        actions_card.setMinimumWidth(260)

        class_card = QFrame()
        class_card.setObjectName("resultStatCard")
        class_layout = QHBoxLayout(class_card)
        class_layout.setContentsMargins(18, 12, 18, 12)
        class_layout.setSpacing(16)
        
        class_title = QLabel("AI Classification:")
        class_title.setObjectName("resultStatTitle")
        
        self.classification_value = QLabel("Pending")
        self.classification_value.setObjectName("classificationValue")
        self.classification_value.setStyleSheet("font-size: 20px; font-weight: 800;")
        
        self.confidence_value = QLabel("Confidence: ( )")
        self.confidence_value.setStyleSheet("font-weight: 700; color: #2563eb; font-size: 13px;")
        
        self.uncertainty_value = QLabel("Uncertainty: ( )")
        self.uncertainty_value.setStyleSheet("font-weight: 700; color: #b45309; font-size: 13px;")
        
        class_layout.addWidget(class_title)
        class_layout.addWidget(self.classification_value)
        class_layout.addStretch(1)
        class_layout.addWidget(self.confidence_value)
        class_layout.addWidget(self.uncertainty_value)

        decision_group = QGroupBox("Doctor Assessment")
        decision_group.setObjectName("resultGroupCard")
        decision_layout = QVBoxLayout(decision_group)
        decision_layout.setContentsMargins(14, 14, 14, 14)
        decision_layout.setSpacing(10)

        self.step1_label = QLabel("1. Review Result")
        self.step1_label.setObjectName("resultStatTitle")
        decision_layout.addWidget(self.step1_label)

        ai_row = QHBoxLayout()
        ai_row.setSpacing(8)
        ai_tag = QLabel("AI")
        ai_tag.setObjectName("decisionRoleTag")
        self.ai_classification_value = QLabel("Pending")
        self.ai_classification_value.setObjectName("resultStatValue")
        ai_row.addWidget(ai_tag)
        ai_row.addWidget(self.ai_classification_value, 1)
        decision_layout.addLayout(ai_row)

        self.step2_label = QLabel("2. Confirm")
        self.step2_label.setObjectName("resultStatTitle")
        decision_layout.addWidget(self.step2_label)

        doctor_row = QHBoxLayout()
        doctor_row.setSpacing(8)
        doctor_tag = QLabel("Doctor")
        doctor_tag.setObjectName("decisionRoleTag")
        doctor_tag.setFixedHeight(34)
        doctor_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.doctor_classification_input = QComboBox()
        self.doctor_classification_input.addItems(["Select classification...", "No DR", "Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"])
        self.doctor_classification_input.setMaximumWidth(280)
        self.doctor_classification_input.setFixedHeight(34)
        self.doctor_classification_input.currentTextChanged.connect(self._on_doctor_classification_changed)
        doctor_row.addWidget(doctor_tag, 0, Qt.AlignmentFlag.AlignVCenter)
        doctor_row.addWidget(self.doctor_classification_input, 0, Qt.AlignmentFlag.AlignVCenter)
        doctor_row.addStretch(1)
        decision_layout.addLayout(doctor_row)

        self.classification_match_label = QLabel("")
        self.classification_match_label.setObjectName("metaText")
        self.classification_match_label.setWordWrap(True)
        decision_layout.addWidget(self.classification_match_label)

        self.step3_label = QLabel("3. Decide")
        self.step3_label.setObjectName("resultStatTitle")
        decision_layout.addWidget(self.step3_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.accept_ai_btn = QPushButton("Accept AI result")
        self.accept_ai_btn.setObjectName("decisionChoiceButton")
        self.accept_ai_btn.clicked.connect(self._accept_ai_classification)
        self.override_ai_btn = QPushButton("Override AI result")
        self.override_ai_btn.setObjectName("decisionChoiceButton")
        self.override_ai_btn.clicked.connect(self._prepare_override)
        action_row.addWidget(self.accept_ai_btn)
        action_row.addWidget(self.override_ai_btn)
        decision_layout.addLayout(action_row)

        self.documentation_panel = QFrame()
        self.documentation_panel.setObjectName("decisionStepPanel")
        documentation_layout = QVBoxLayout(self.documentation_panel)
        documentation_layout.setContentsMargins(12, 10, 12, 12)
        documentation_layout.setSpacing(8)

        self.override_reason_label = QLabel("Override justification of results")
        self.override_reason_label.setObjectName("metaText")
        self.override_reason_input = QTextEdit()
        self.override_reason_input.setObjectName("overrideCommentBox")
        self.override_reason_input.setPlaceholderText("Provide concise clinical justification...")
        self.override_reason_input.setMinimumHeight(100)
        self.override_reason_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.override_reason_input.textChanged.connect(self._on_override_reason_changed)

        override_comment_col = QVBoxLayout()
        override_comment_col.setSpacing(6)
        override_comment_col.setContentsMargins(0, 0, 0, 0)
        override_comment_col.addWidget(self.override_reason_label)
        override_comment_col.addWidget(self.override_reason_input)
        documentation_layout.addLayout(override_comment_col)

        decision_layout.addWidget(self.documentation_panel)

        self.decision_hint = QLabel("AI is decision support. Doctor classification is the final authority.")
        self.decision_hint.setObjectName("metaText")
        self.decision_hint.setWordWrap(True)
        decision_layout.addWidget(self.decision_hint)

        self.optional_comment_panel = QFrame()
        self.optional_comment_panel.setObjectName("decisionStepPanel")
        optional_layout = QVBoxLayout(self.optional_comment_panel)
        optional_layout.setContentsMargins(12, 10, 12, 12)
        optional_layout.setSpacing(8)

        self.findings_label = QLabel("Optional doctor findings and comments")
        self.findings_label.setObjectName("metaText")
        self.findings_input = QTextEdit()
        self.findings_input.setObjectName("findingsCommentBox")
        self.findings_input.setPlaceholderText("")
        self.findings_input.setMinimumHeight(96)
        self.findings_input.textChanged.connect(self._on_findings_changed)
        optional_layout.addWidget(self.findings_label)
        optional_layout.addWidget(self.findings_input)

        decision_layout.addWidget(self.optional_comment_panel)

        # (Metrics cards are now placed in right_col below)
        # AI Recommendation Card
        reco_card = QGroupBox("")
        reco_card.setObjectName("resultGroupCard")
        reco_card.setMinimumHeight(200)
        reco_layout = QVBoxLayout(reco_card)
        reco_layout.setContentsMargins(14, 10, 14, 14)
        reco_layout.setSpacing(6)
        
        reco_title = QLabel("AI RECOMMENDATION")
        reco_title.setObjectName("resultStatTitle")
        
        self.treatment_suggestions_value = QLabel("- Complete analysis to view possible treatment suggestions.")
        self.treatment_suggestions_value.setObjectName("summaryBody")
        self.treatment_suggestions_value.setWordWrap(True)
        self.treatment_suggestions_value.setTextFormat(Qt.TextFormat.RichText)
        
        reco_layout.addWidget(reco_title)
        reco_layout.addWidget(self.treatment_suggestions_value)

        self.ai_disclaimer_label = QLabel(
            "This AI-generated output is provided solely as clinical decision support. "
            "Final diagnosis, treatment planning, and all medical decisions remain the exclusive "
            "responsibility of the attending licensed physician."
        )
        self.ai_disclaimer_label.setObjectName("aiDisclaimerLabel")
        self.ai_disclaimer_label.setWordWrap(True)

        # Bilateral comparison card (hidden until second eye is being reviewed)
        self.bilateral_frame = QFrame()
        self.bilateral_frame.setObjectName("resultStatCard")
        bilateral_layout = QVBoxLayout(self.bilateral_frame)
        bilateral_layout.setContentsMargins(18, 16, 18, 16)
        bilateral_layout.setSpacing(12)
        bilateral_title = QLabel("  Bilateral Screening Comparison")
        bilateral_title.setObjectName("resultStatTitle")
        bilateral_layout.addWidget(bilateral_title)
        brow = QHBoxLayout()
        brow.setSpacing(20)
        first_col = QVBoxLayout()
        first_col.setSpacing(4)
        self.bilateral_first_eye_lbl = QLabel("")
        self.bilateral_first_eye_lbl.setObjectName("resultStatTitle")
        self.bilateral_first_eye_lbl.setWordWrap(True)
        self.bilateral_first_eye_lbl.setStyleSheet("font-size:15px;font-weight:600;")
        self.bilateral_first_result_lbl = QLabel("")
        self.bilateral_first_result_lbl.setObjectName("resultStatValue")
        self.bilateral_first_result_lbl.setWordWrap(True)
        self.bilateral_first_result_lbl.setStyleSheet("font-size:15px;font-weight:600;")
        self.bilateral_first_saved_lbl = QLabel(" Saved")
        self.bilateral_first_saved_lbl.setStyleSheet("font-weight:700;font-size:13px;")
        self.bilateral_first_saved_lbl.setObjectName("successLabel")
        first_col.addWidget(self.bilateral_first_eye_lbl)
        first_col.addWidget(self.bilateral_first_result_lbl)
        first_col.addWidget(self.bilateral_first_saved_lbl)
        brow_div = QFrame()
        brow_div.setFrameShape(QFrame.Shape.VLine)
        brow_div.setFrameShadow(QFrame.Shadow.Plain)
        brow_div.setStyleSheet("color:#d9e5f2;")
        second_col = QVBoxLayout()
        second_col.setSpacing(4)
        self.bilateral_second_eye_lbl = QLabel("")
        self.bilateral_second_eye_lbl.setObjectName("resultStatTitle")
        self.bilateral_second_eye_lbl.setWordWrap(True)
        self.bilateral_second_eye_lbl.setStyleSheet("font-size:15px;font-weight:600;")
        self.bilateral_second_result_lbl = QLabel("")
        self.bilateral_second_result_lbl.setObjectName("resultStatValue")
        self.bilateral_second_result_lbl.setWordWrap(True)
        self.bilateral_second_result_lbl.setStyleSheet("font-size:15px;font-weight:600;")
        self.bilateral_second_saved_lbl = QLabel("Unsaved")
        self.bilateral_second_saved_lbl.setStyleSheet("font-weight:700;font-size:13px;")
        self.bilateral_second_saved_lbl.setObjectName("errorLabel")
        second_col.addWidget(self.bilateral_second_eye_lbl)
        second_col.addWidget(self.bilateral_second_result_lbl)
        second_col.addWidget(self.bilateral_second_saved_lbl)
        brow.addLayout(first_col)
        brow.addWidget(brow_div)
        brow.addLayout(second_col)
        bilateral_layout.addLayout(brow)
        self.bilateral_frame.hide()

        self._apply_action_icons()

        explanation_group = QGroupBox("")
        explanation_group.setObjectName("resultGroupCard")
        explanation_group.setMinimumHeight(200)
        explanation_layout = QVBoxLayout(explanation_group)
        explanation_layout.setContentsMargins(14, 10, 14, 14)
        explanation_layout.setSpacing(6)

        self.ai_summary_title = QLabel("PATIENT INFORMATION SUMMARY")
        self.ai_summary_title.setObjectName("resultStatTitle")
        explanation_layout.addWidget(self.ai_summary_title)

        self.explanation = QLabel("Awaiting clinical profile and model analysis...")
        self.explanation.setWordWrap(True)
        self.explanation.setObjectName("summaryBody")
        self.explanation.setTextFormat(Qt.TextFormat.RichText)
        explanation_layout.addWidget(self.explanation)

        self.footer_label = QLabel(
            "EyeShield_V2.0"
        )
        self.footer_label.setObjectName("footerLabel")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_label.setWordWrap(True)

        # --- Final Layout Placement ---
        left_col.addWidget(class_card)
        
        summary_reco_row = QHBoxLayout()
        summary_reco_row.setSpacing(12)
        summary_reco_row.addWidget(explanation_group, 1)
        summary_reco_row.addWidget(reco_card, 1)
        left_col.addLayout(summary_reco_row)
        
        left_col.addWidget(self.ai_disclaimer_label)
        left_col.addStretch(1)

        # --- Populate Right Column ---
        right_col.addWidget(decision_group)
        right_col.addWidget(actions_card)
        right_col.addWidget(self.bilateral_frame)
        right_col.addStretch(1)

        layout.addWidget(self.footer_label)

        self.setStyleSheet("""
            QWidget {
                background: #ffffff;
                color: #1f2937;
                font-family: "Segoe UI";
                font-size: 14px;
            }
            QScrollArea {
                background: #ffffff;
                border: none;
            }
            QLabel {
                background: transparent;
            }
            QLabel#crumbLabel {
                color: #6b7280;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1.3px;
            }
            QLabel#pageHeader {
                font-size: 24px;
                font-weight: 700;
                color: #111827;
                letter-spacing: 0.1px;
            }
            QLabel#pageSubtitle {
                color: #6b7280;
                font-size: 13px;
            }
            QLabel#infoPill {
                background: #eff6ff;
                color: #1d4ed8;
                border: 1px solid #bfdbfe;
                border-radius: 20px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#savedPill {
                background: #ecfdf3;
                color: #166534;
                border: 1px solid #86efac;
                border-radius: 20px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#aiDisclaimerLabel {
                background: #fffbeb;
                color: #7c2d12;
                border: 1px solid #fed7aa;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                line-height: 1.3;
                font-weight: 600;
            }
            QGroupBox#resultGroupCard {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 12px;
                margin-top: 0;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
            }
            QGroupBox#resultGroupCard::title {
                color: transparent;
                subcontrol-origin: margin;
                left: 0;
                padding: 0;
            }
            QLabel#cardHeaderLabel {
                color: #374151;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#expandGlyph {
                color: #6b7280;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#sourceImageSurface {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                color: #94a3b8;
                font-size: 13px;
            }
            QLabel#heatmapImageSurface {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                color: #94a3b8;
                font-size: 14px;
            }
            QFrame#resultStatCard {
                background: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 12px;
            }
            QLabel#resultStatTitle {
                color: #6b7280;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.9px;
            }
            QLabel#classificationValue {
                color: #2563eb;
                font-size: 27px;
                font-weight: 700;
            }
            QLabel#resultStatValue {
                color: #111827;
                font-size: 18px;
                font-weight: 600;
            }
            QLabel#monoValue {
                color: #1f2937;
                font-family: "Segoe UI";
                font-size: 18px;
                font-weight: 700;
            }
            QProgressBar#confidenceBar {
                border: none;
                border-radius: 4px;
                background: #e5e7eb;
                height: 6px;
            }
            QProgressBar#confidenceBar::chunk {
                background: #2563eb;
                border-radius: 4px;
            }
            QProgressBar#uncertaintyBar {
                border: none;
                border-radius: 4px;
                background: #fef3c7;
                height: 6px;
            }
            QProgressBar#uncertaintyBar::chunk {
                background: #f59e0b;
                border-radius: 4px;
            }
            QLabel#metaText {
                color: #6b7280;
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#decisionRoleTag {
                background: #f8fafc;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 700;
                min-height: 20px;
            }
            QFrame#decisionStepPanel {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
            }
            QPushButton#decisionChoiceButton {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #60a5fa;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 700;
            }
            QPushButton#decisionChoiceButton:hover {
                background: #eff6ff;
                border-color: #3b82f6;
            }
            QPushButton#decisionChoiceButton:pressed {
                background: #dbeafe;
                border-color: #2563eb;
            }
            QPushButton#decisionChoiceButton:disabled {
                background: #f8fafc;
                color: #94a3b8;
                border-color: #bfdbfe;
            }
            QPushButton#ghostAction {
                background: #ffffff;
                border: 1px solid #bfdbfe;
                color: #1a1a1a;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
                font-family: "Segoe UI";
                font-weight: 400;
            }
            QPushButton#ghostAction:hover {
                background: #eff6ff;
                border-color: #93c5fd;
            }
            QPushButton#ghostAction:disabled {
                background: #f8fafc;
                color: #94a3b8;
                border-color: #dbeafe;
            }
            QTextEdit#overrideCommentBox,
            QTextEdit#findingsCommentBox {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                padding: 10px;
                font-size: 13px;
                color: #1f2937;
            }
            QTextEdit#overrideCommentBox {
                min-height: 100px;
            }
            QTextEdit#overrideCommentBox:focus,
            QTextEdit#findingsCommentBox:focus {
                border: 1px solid #60a5fa;
            }
            QComboBox {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                padding: 4px 10px;
                min-height: 30px;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #1f2937;
                selection-background-color: #eff6ff;
                selection-color: #1d4ed8;
                outline: 0px;
                border: 1px solid #e5e7eb;
            }
            QComboBox QListView {
                background-color: #ffffff;
                color: #1f2937;
                border: 1px solid #e5e7eb;
            }
            QComboBox QListView::item {
                background-color: #ffffff;
                color: #1f2937;
                padding: 8px;
            }
            QComboBox QListView::item:selected {
                background-color: #eff6ff;
                color: #1d4ed8;
            }
            QFrame#uncertaintyPanel {
                background: #fffbeb;
                border: 1px solid #fce7b6;
                border-radius: 8px;
            }
            QLabel#uncertaintyValue {
                color: #92400e;
                font-size: 18px;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
            QLabel#okBadge {
                background: #ecfdf3;
                color: #166534;
                border: 1px solid #86efac;
                border-radius: 20px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#summaryBody {
                background: transparent;
                border: none;
                border-radius: 0;
                color: #595959;
                font-size: 13px;
                font-weight: 500;
                line-height: 1.6;
                padding: 0;
            }
            QLabel#summaryRowSuccess {
                background: transparent;
                border: none;
                border-radius: 0;
                padding: 6px 0;
                color: #166534;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#summaryRowInfo {
                background: transparent;
                border: none;
                border-radius: 0;
                padding: 6px 0;
                color: #1d4ed8;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#summaryRowWarn {
                background: transparent;
                border: none;
                border-radius: 0;
                padding: 6px 0;
                color: #b45309;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#footerLabel {
                color: #9ca3af;
                font-size: 11px;
                padding-top: 12px;
                padding-bottom: 12px;
            }
            QPushButton {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                padding: 10px 16px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #f3f4f6;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background: #e5e7eb;
            }
            QPushButton:disabled {
                background: #f9fafb;
                color: #d1d5db;
                border-color: #e5e7eb;
            }
            QPushButton#primaryAction {
                background: #2563eb;
                color: #ffffff;
                border: none;
                font-weight: 600;
            }
            QPushButton#primaryAction:hover {
                background: #1d4ed8;
            }
            QPushButton#primaryAction:pressed {
                background: #1e40af;
            }
            QPushButton#neutralAction {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d1d5db;
                font-weight: 600;
            }
            QPushButton#neutralAction:hover {
                background: #f9fafb;
                border-color: #9ca3af;
            }
            QPushButton#referAction {
                background: #ecfeff;
                color: #0f766e;
                border: 1px solid #99f6e4;
                font-weight: 700;
            }
            QPushButton#referAction:hover {
                background: #ccfbf1;
                border-color: #5eead4;
            }
            QPushButton#referAction:pressed {
                background: #99f6e4;
                border-color: #2dd4bf;
            }
            QPushButton#referAction:disabled {
                background: #f8fafc;
                color: #94a3b8;
                border-color: #e2e8f0;
            }
            QPushButton#dangerAction {
                background: #fef2f2;
                color: #b91c1c;
                border: 1px solid #fecaca;
                font-weight: 600;
            }
            QPushButton#dangerAction:hover {
                background: #fee2e2;
                border-color: #fca5a5;
            }
        """)

    def _is_dark_theme(self) -> bool:
        bg = self.palette().color(QPalette.ColorRole.Window)
        fg = self.palette().color(QPalette.ColorRole.WindowText)
        return bg.lightness() < fg.lightness()

    def _build_action_icon(self, filename: str, fallback: QStyle.StandardPixmap) -> QIcon:
        icon_path = os.path.join(self._icons_dir, filename)
        base_icon = QIcon(icon_path) if os.path.isfile(icon_path) else self.style().standardIcon(fallback)
        source = base_icon.pixmap(QSize(24, 24))
        if source.isNull():
            return base_icon

        tint = QColor("#f8fafc") if self._is_dark_theme() else QColor("#1f2937")
        tinted = QPixmap(source.size())
        tinted.fill(Qt.GlobalColor.transparent)

        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), tint)
        painter.end()

        icon = QIcon()
        icon.addPixmap(tinted, QIcon.Mode.Normal)
        icon.addPixmap(tinted, QIcon.Mode.Active)

        disabled = QPixmap(tinted)
        p2 = QPainter(disabled)
        p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p2.fillRect(disabled.rect(), QColor(tint.red(), tint.green(), tint.blue(), 110))
        p2.end()
        icon.addPixmap(disabled, QIcon.Mode.Disabled)
        return icon

    def _apply_action_icons(self):
        self.btn_save.setIcon(self._build_action_icon("save_patient.svg", QStyle.StandardPixmap.SP_DialogSaveButton))
        self.btn_report.setIcon(self._build_action_icon("generate.svg", QStyle.StandardPixmap.SP_ArrowDown))
        self.btn_referral.setIcon(self._build_action_icon("refer.svg", QStyle.StandardPixmap.SP_CommandLink))
        self.btn_back.setIcon(self._build_action_icon("back_to_screening.svg", QStyle.StandardPixmap.SP_ArrowBack))
        self.accept_ai_btn.setIcon(self._build_action_icon("accep_ai_result.svg", QStyle.StandardPixmap.SP_DialogApplyButton))
        self.override_ai_btn.setIcon(self._build_action_icon("override_ai result.svg", QStyle.StandardPixmap.SP_FileDialogDetailedView))

    def _resolve_actor_username(self) -> str:
        raw_username = str(
            os.environ.get("EYESHIELD_CURRENT_USER")
            or (getattr(self.parent_page, "username", "") if self.parent_page else "")
            or (getattr(self.window(), "username", "") if self.window() is not self else "")
        ).strip()
        return UserManager.resolve_username(raw_username)

    def changeEvent(self, event):
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._apply_action_icons()
        super().changeEvent(event)

    def _create_stat_card(self, title_text):
        card = QFrame()
        card.setObjectName("resultStatCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(4)

        title = QLabel(title_text)
        title.setObjectName("resultStatTitle")
        value = QLabel("Pending")
        value.setObjectName("resultStatValue")
        value.setWordWrap(True)

        card_layout.addWidget(title)
        card_layout.addWidget(value)
        return card, value

    @staticmethod
    def _extract_percent_value(value_text: str) -> float:
        txt = str(value_text or "")
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", txt)
        if not match:
            return 0.0
        try:
            return max(0.0, min(100.0, float(match.group(1))))
        except ValueError:
            return 0.0

    @staticmethod
    def _format_percent(value: float) -> str:
        return f"{max(0.0, min(100.0, value)):.1f}%"

    def _build_treatment_suggestions(self, result_class: str, patient_data: dict | None, uncertainty_pct: float) -> str:
        grade_map = {
            "No DR": [
                "Routine monitoring every 12 months",
                "No ocular intervention indicated at this time",
                "Continue preventive diabetes care and risk-factor control (glucose, BP, lipids)",
            ],
            "Mild DR": [
                "Monitoring every 6-12 months",
                "Usually managed with observation and metabolic optimization",
                "Reinforce glucose/BP/lipid control to prevent progression",
            ],
            "Moderate DR": [
                "Closer monitoring every 3-6 months",
                "Refer for ophthalmology assessment to evaluate progression risk",
                "Assess for macular edema and consider treatment planning if present",
                "Strengthen systemic control (glucose, BP, lipids)",
            ],
            "Severe DR": [
                "Very close monitoring (every 2-3 months)",
                "Consider early Anti-VEGF therapy",
                "Possible Panretinal Photocoagulation (PRP) in high-risk cases",
                "Aggressive systemic control (glucose, BP, lipids)",
            ],
            "Proliferative DR": [
                "Immediate retina specialist management",
                "Anti-VEGF therapy is commonly considered",
                "Panretinal Photocoagulation (PRP) is often required",
                "Evaluate for vitreoretinal surgery when tractional or non-clearing vitreous hemorrhage is suspected",
                "Aggressive systemic control (glucose, BP, lipids)",
            ],
        }
        suggestions = grade_map.get(
            result_class,
            ["Ophthalmology review for confirmation and management planning"],
        )
        content = "<b>Treatment / Management:</b><br><br>" + "<br>".join(f"&bull; {item}" for item in suggestions)
        return f"<html><div style='line-height:150%;'>{content}</div></html>"

    def _reset_save_button_default(self):
        self.btn_save.setEnabled(True)
        self.btn_save.setText("Save to Patient Record")
        self.btn_save.setObjectName("ghostAction")
        self.btn_save.setStyle(self.btn_save.style())
        self.save_note_label.hide()

    def _set_save_state(self, state: str, details: str = ""):
        if state == "writing":
            self.btn_save.setEnabled(False)
            self.btn_save.setText("Saving to Patient Record...")
            self.save_note_label.setText(details or "Writing patient record...")
            self.save_note_label.show()
            return

        if state == "success":
            self.btn_save.setEnabled(False)
            self.btn_save.setText("Saved ")
            self.save_note_label.setText(details)
            self.save_note_label.show()
            self._save_state_timer.start(4000)
            return

        if state == "unchanged":
            self.btn_save.setEnabled(True)
            self.btn_save.setText("Save to Patient Record")
            self.save_note_label.setText("No changes since last save")
            self.save_note_label.show()
            self._save_state_timer.start(4000)
            return

        if state == "failed":
            self.btn_save.setEnabled(True)
            self.btn_save.setText("Save Failed")
            self.save_note_label.setText(details)
            self.save_note_label.show()
            return

        self._reset_save_button_default()

    def is_uncertainty_blocking(self) -> bool:
        return False

    def _acknowledge_uncertainty(self):
        return

    def _accept_ai_classification(self):
        ai_value = str(self._current_result_class or "").strip()
        if ai_value:
            self.doctor_classification_input.setCurrentText(ai_value)
            self._doctor_classification = ai_value
            self._decision_mode = "accepted"
            self._override_justification = ""
            self.override_reason_input.clear()
            self._refresh_decision_ui_state()

    def _prepare_override(self):
        self._decision_mode = "override"
        self._refresh_decision_ui_state()
        self.override_reason_input.setFocus()

    def _on_doctor_classification_changed(self, value: str):
        chosen = str(value or "").strip()
        if chosen == "":
            chosen = ""
        self._doctor_classification = chosen
        ai_value = str(self._current_result_class or "").strip()
        if self._decision_mode in ("accepted", "override"):
            if self._doctor_classification == ai_value:
                if self._decision_mode == "override":
                    self._decision_mode = "accepted"
                    self.override_reason_input.clear()
                    self._override_justification = ""
            elif self._doctor_classification:
                self._decision_mode = "override"
        self._refresh_decision_ui_state()

    def _on_override_reason_changed(self, text: str = ""):
        if text:
            self._override_justification = str(text).strip()
        else:
            self._override_justification = str(self.override_reason_input.toPlainText() or "").strip()
        self._refresh_decision_ui_state()

    def _on_findings_changed(self, text: str = ""):
        if text:
            self._doctor_findings = str(text).strip()
        else:
            self._doctor_findings = str(self.findings_input.toPlainText() or "").strip()

    def _refresh_decision_ui_state(self):
        ai_value = str(self._current_result_class or "").strip()
        doctor_value = str(self.doctor_classification_input.currentText() or self._doctor_classification or "").strip()
        if doctor_value == "":
            doctor_value = ""
        requires_override = bool(doctor_value and doctor_value != ai_value)

        show_documentation = self._decision_mode == "override" or requires_override
        show_optional_comment = self._decision_mode in ("accepted", "override") or requires_override
        show_override = self._decision_mode == "override" or requires_override

        self.documentation_panel.setVisible(show_documentation)
        self.optional_comment_panel.setVisible(show_optional_comment)
        self.override_reason_label.setVisible(show_override)
        self.override_reason_input.setVisible(show_override)

        if not doctor_value:
            self.classification_match_label.setText("Enter your classification to continue.")
        elif doctor_value == ai_value:
            self.classification_match_label.setText("Your current classification matches the AI")
        else:
            self.classification_match_label.setText("Your classification differs from AI. Override documentation is required.")

        if requires_override:
            self.decision_hint.setText("Override selected. Provide clinical justification before saving.")
        elif self._decision_mode == "accepted":
            self.decision_hint.setText("AI accepted. Optional doctor comments can be added below.")
        else:
            self.decision_hint.setText("Choose Accept AI or Override AI to reveal the required documentation fields.")

    def get_decision_payload(self) -> dict:
        ai_value = str(self._current_result_class or "").strip()
        doctor_value = str(self.doctor_classification_input.currentText() or self._doctor_classification or "").strip()
        if doctor_value == "Select classification...":
            doctor_value = ""
        requires_override = bool(doctor_value and ai_value and doctor_value != ai_value)
        mode = self._decision_mode if self._decision_mode in ("accepted", "override") else "pending"
        if mode == "accepted" and requires_override:
            mode = "override"
        override_text = str(self.override_reason_input.toPlainText() or self._override_justification or "").strip()
        findings_text = str(self.findings_input.toPlainText() or self._doctor_findings or "").strip()

        # Keep cached state aligned with latest UI before downstream save/report logic runs.
        self._doctor_classification = doctor_value
        self._override_justification = override_text
        self._doctor_findings = findings_text

        return {
            "ai_classification": ai_value,
            "doctor_classification": doctor_value,
            "decision_mode": mode,
            "override_justification": override_text,
            "final_diagnosis_icdr": doctor_value,
            "doctor_findings": findings_text,
        }

    def validate_decision_before_save(self) -> tuple[bool, str]:
        if self._decision_mode not in ("accepted", "override"):
            return False, "Please choose Accept AI result or Override AI result before saving."

        payload = self.get_decision_payload()
        doctor_value_raw = str(payload.get("doctor_classification") or "").strip()
        if not doctor_value_raw:
            return False, "Please enter doctor classification."
        doctor_value = self._normalize_dr_label(doctor_value_raw)
        if not doctor_value:
            return False, "Please enter a valid DR grade (No DR, Mild DR, Moderate DR, Severe DR, Proliferative DR)."
        findings = str(payload.get("doctor_findings") or "").strip()
        if payload.get("decision_mode") == "override":
            justification = str(payload.get("override_justification") or "").strip()
            if len(justification) < 8:
                return False, "Override requires a brief clinical justification (at least 8 characters)."
        elif not findings:
            # Auto-fill a concise default note for accepted AI decisions to avoid hard save failures.
            default_note = f"Clinician reviewed and accepted AI classification: {doctor_value}."
            self._doctor_findings = default_note
            self.findings_input.setText(default_note)
        return True, ""

    @staticmethod
    def _normalize_dr_label(value: str) -> str:
        t = str(value or "").strip()
        if not t:
            return ""
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
        if t in {"No DR", "Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"}:
            return t
        return ""

    def set_results(self, patient_name, image_path, result_class="Pending", confidence_text="Pending", eye_label="", first_eye_result=None, heatmap_path="", patient_data=None, heatmap_pending=False):
        is_loading = result_class in ("Analyzing", "Pending")
        is_system_uncertain = result_class == SYSTEM_UNCERTAIN_LABEL
        is_busy = is_loading or heatmap_pending

        if patient_name:
            self.title_label.setText(f"Results for {patient_name}")
        else:
            self.title_label.setText("Results")
        self.eye_badge_label.setText(f" {eye_label or 'Screened Eye'}")

        # Loading bar
        if is_busy:
            self._loading_bar.show()
        else:
            self._loading_bar.hide()

        # Reset save feedback state
        self.save_status_label.hide()
        self.save_status_label.setText("Saved ")
        self.btn_save.setEnabled(not is_busy)
        self.btn_save.setText("Save to Patient Record")
        self.btn_save.setObjectName("ghostAction")
        self.btn_save.setStyle(self.btn_save.style())

        # Bilateral comparison
        if first_eye_result:
            self._first_eye_context = dict(first_eye_result)
            self.bilateral_first_eye_lbl.setText(first_eye_result.get("eye", ""))
            self.bilateral_first_result_lbl.setText(first_eye_result.get("result", ""))
            self.bilateral_second_eye_lbl.setText(eye_label or "Current Eye")
            self.bilateral_second_result_lbl.setText(result_class)
            self.bilateral_second_saved_lbl.setText("Unsaved")
            self.bilateral_second_saved_lbl.setStyleSheet("font-weight:700;font-size:13px;")
            self.bilateral_second_saved_lbl.setObjectName("errorLabel")
            self.bilateral_frame.show()
        else:
            self._first_eye_context = {}
            # Make sure trend classification is blank if no second screening yet
            self.bilateral_first_eye_lbl.setText("")
            self.bilateral_first_result_lbl.setText("")
            self.bilateral_second_eye_lbl.setText("")
            self.bilateral_second_result_lbl.setText("")
            self.bilateral_second_saved_lbl.setText("")
            self.bilateral_frame.hide()

        # Classification with severity colour
        self.classification_value.setText(result_class)
        self.ai_classification_value.setText(result_class)
        if is_system_uncertain:
            grade_color = "#b45309"
        else:
            grade_color = DR_COLORS.get(result_class, "#1f2937")
        self.classification_value.setStyleSheet(f"color:{grade_color};font-size:20px;font-weight:800;")

        # Classification subtitle removed to save space
        pass

        if is_system_uncertain:
            self.confidence_value.setText("Confidence: —")
            self.uncertainty_value.setText("Uncertainty: —")
            self._uncertainty_pct = 0.0
        else:
            confidence_pct = self._extract_percent_value(confidence_text)
            confidence_display = self._format_percent(confidence_pct)
            self.confidence_value.setText(f"Confidence: {confidence_display}")

            uncertainty_match = re.search(r"uncertainty\s*:?\s*(\d+(?:\.\d+)?)\s*%", str(confidence_text or ""), re.IGNORECASE)
            if uncertainty_match:
                uncertainty_pct = max(0.0, min(100.0, float(uncertainty_match.group(1))))
            else:
                uncertainty_pct = max(0.0, min(100.0, 100.0 - confidence_pct))
            self._uncertainty_pct = uncertainty_pct
            self.uncertainty_value.setText(f"Uncertainty: {self._format_percent(uncertainty_pct)}")

        # Severity-based possible treatment suggestions
        if is_loading:
            self.treatment_suggestions_value.setText("- Complete analysis to view possible treatment suggestions.")
        elif is_system_uncertain:
            self.treatment_suggestions_value.setText(
                "<html><div style='line-height:150%;'><b>Treatment / Management:</b><br><br>"
                "&bull; No automated DR grade is available for this image.<br>"
                "&bull; Specialist review is required before any clinical decisions.</div></html>"
            )
        else:
            self.treatment_suggestions_value.setText(
                self._build_treatment_suggestions(result_class, patient_data, uncertainty_pct)
            )

        # Subtitle label removed per user request
        pass

        # Image and heatmap panels
        if image_path:
            source_pixmap = QPixmap(image_path)
            self.source_label.set_viewable_pixmap(source_pixmap, 500, 380)
            if is_loading:
                self.heatmap_label.setText("Analyzing image...")
            elif is_system_uncertain:
                self.heatmap_label.setText("No heatmap — specialist review required")
            elif heatmap_path:
                heatmap_pixmap = QPixmap(heatmap_path)
                self.heatmap_label.set_viewable_pixmap(heatmap_pixmap, 500, 380)
            elif heatmap_pending:
                self.heatmap_label.setText("Generating heatmap...")
            else:
                self.heatmap_label.setText("No heatmap available")
        if is_loading:
            self.explanation.setText("Analysis in progress  awaiting clinical summary...")
        elif is_system_uncertain:
            elab = (eye_label or "screened eye").lower()
            summary_paragraph = (
                f"The AI model did not reach sufficient confidence to assign a DR grade for this {elab}. "
                "Specialist review is required. No heatmap is generated or recorded for this analysis."
            )
            self.explanation.setTextFormat(Qt.TextFormat.RichText)
            self.explanation.setText(f"<html><div style='line-height:150%;'>{summary_paragraph}</div></html>")
        else:
            pd = patient_data or {}
            # Gather profile details
            p_name = patient_name or "Unknown Patient"
            p_age = pd.get("age", 0)
            p_sex = pd.get("sex", "")
            p_height = pd.get("height", 0)
            p_weight = pd.get("weight", 0)
            p_type = pd.get("diabetes_type", "")
            p_duration = pd.get("duration", 0)
            p_hba1c = pd.get("hba1c", 0.0)
            p_family_hx = pd.get("prev_dr_stage", "")
            
            p_treatment = pd.get("treatment_regimen", "")
            
            profile_parts = [
                f"<b>{p_name}</b>",
                f"{p_age} years old" if p_age else "",
                f"{p_sex}" if p_sex and p_sex != "" else "",
            ]
            profile_txt = ", ".join(filter(None, profile_parts))
            
            vitals = []
            if p_height: vitals.append(f"Height: {p_height}cm")
            if p_weight: vitals.append(f"Weight: {p_weight}kg")
            vitals_txt = f" ({', '.join(vitals)})" if vitals else ""
            
            history = []
            if p_type and p_type != "Select": history.append(f"{p_type} diabetes")
            if p_duration:
                y = p_duration // 12
                m = p_duration % 12
                if y > 0:
                    dur_str = f"{y}y {m}m" if m > 0 else f"{y} years"
                else:
                    dur_str = f"{m} months"
                history.append(f"{dur_str} duration")
            if p_hba1c: history.append(f"HbA1c {p_hba1c}%")
            
            history_txt = f" with a history of {', '.join(history)}" if history else " with no recorded diabetic history"
            
            if p_treatment and p_treatment not in ("", "Select", "None"):
                history_txt += f", currently managed via {p_treatment.lower()}"
            
            if p_family_hx == "Yes":
                history_txt += " and a positive family history of diabetes"
            elif p_family_hx == "No":
                history_txt += " and no known family history of diabetes"
            
            severity_desc = {
                "No DR": "shows no signs of diabetic retinopathy",
                "Mild DR": "indicates mild non-proliferative changes",
                "Moderate DR": "shows moderate non-proliferative retinopathy",
                "Severe DR": "presents with severe non-proliferative retinopathy",
                "Proliferative DR": "shows signs of sight-threatening proliferative retinopathy",
            }.get(result_class, f"resulted in a classification of {result_class}")
            
            summary_paragraph = (
                f"Patient {profile_txt}{vitals_txt}{history_txt}. "
                f"The current AI analysis of the {eye_label.lower()} {severity_desc}. "
                f"Clinical correlation with a specialist review is recommended (Uncertainty: {self._format_percent(uncertainty_pct)})."
            )
            self.explanation.setTextFormat(Qt.TextFormat.RichText)
            self.explanation.setText(f"<html><div style='line-height:150%;'>{summary_paragraph}</div></html>")

        # Keep state current so generate_report always has the latest values
        self._current_image_path   = image_path or ""
        self._current_heatmap_path = heatmap_path or ""
        self._current_result_class = result_class
        self._current_confidence   = confidence_text
        self._current_eye_label    = eye_label
        self._current_patient_name = patient_name or ""
        if result_class in ICDR_OPTIONS:
            self.doctor_classification_input.setCurrentText(result_class)
            self._doctor_classification = result_class
            self._decision_mode = "accepted"
            self._override_justification = ""
            self.override_reason_input.clear()
            self._doctor_findings = ""
            self.findings_input.clear()
        self._refresh_decision_ui_state()
        _report_ready = (
            not is_busy
            and bool(image_path)
            and result_class not in ("Analyzing", "Pending")
        )
        self.btn_report.setEnabled(_report_ready)
        self.btn_referral.setEnabled(_report_ready)

    def mark_saved(self, name, eye_label, result_class):
        """Called by ScreeningPage after a successful save to update this panel."""
        self.save_status_label.setText("Saved ")
        self.save_status_label.show()
        self.btn_save.setText("Saved ")
        self.btn_save.setEnabled(False)
        if self.bilateral_frame.isVisible():
            self.bilateral_second_saved_lbl.setText(" Saved")
            self.bilateral_second_saved_lbl.setStyleSheet("font-weight:700;font-size:13px;")
            self.bilateral_second_saved_lbl.setObjectName("successLabel")

    def reset_ui(self):
        """Thoroughly reset the results interface to a clean, pending state."""
        self.classification_value.setText("Pending")
        self.confidence_value.setText("Confidence: ( )")
        self.uncertainty_value.setText("Uncertainty: ( )")
        self.ai_classification_value.setText("Pending")
        self.doctor_classification_input.setCurrentIndex(0)
        self.classification_match_label.setText("Enter your classification to continue.")
        self.explanation.setText("Analysis in progress  awaiting clinical summary...")
        self.treatment_suggestions_value.setText("- Complete analysis to view possible treatment suggestions.")
        self.source_label.clear_view("Source image")
        self.heatmap_label.clear_view("Heatmap Image")
        self.override_reason_input.clear()
        self.findings_input.clear()

        self._current_image_path = ""
        self._current_heatmap_path = ""
        self._current_result_class = "Pending"
        self._doctor_classification = "Pending"
        self._decision_mode = "pending"
        self._override_justification = ""
        self._doctor_findings = ""
        self._uncertainty_pct = 0.0

        self.btn_save.setEnabled(True)
        self.btn_save.setText("Save to Patient Record")
        self.btn_save.setObjectName("ghostAction")
        self.save_status_label.hide()
        self.progression_panel.hide()
        self.bilateral_frame.hide()
        self._refresh_decision_ui_state()

    def go_back(self):
        """Prompt user for navigation choice: back to form or back to queue."""
        if not self.parent_page:
            return
        page = self.parent_page

        box = QMessageBox(self)
        apply_dialog_style(box)
        box.setWindowTitle("Navigation")
        box.setText("<b>Where would you like to go?</b>")
        box.setInformativeText("Returning to the queue will reset current results.")
        
        diag_btn = box.addButton("Diagnosis", QMessageBox.ButtonRole.ActionRole)
        queue_btn = box.addButton("Patient Queue", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        
        box.exec()
        choice = box.clickedButton()
        
        if choice == diag_btn:
            # Go back to intake form (index 0)
            if hasattr(page, "stacked_widget"):
                page.stacked_widget.setCurrentIndex(0)
                write_activity("INFO", "NAV_BACK_TO_DIAGNOSIS", "User went back to diagnosis form")
        elif choice == queue_btn:
            # Reset everything and go to queue
            self.reset_ui()
            if hasattr(page, "reset_screening"):
                page.reset_screening(confirm_unsaved=False)
            
            main_win = self.window()
            if hasattr(main_win, "_navigate_to"):
                main_win._navigate_to(10, nav_key="Patient Queue")
            elif hasattr(main_win, "pages"):
                main_win.pages.setCurrentIndex(10)
            write_activity("INFO", "NAV_BACK_TO_QUEUE", "User went back to patient queue")

    def set_progression_info(self, prev_result, current_result):
        """Show progression summary for follow-up screenings."""
        if not prev_result or not current_result or current_result in ("Pending", "Analyzing", "Ungradable"):
            self.progression_panel.hide()
            return

        severity_levels = ["No DR", "Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"]
        
        try:
            # Clean up result strings if they contain confidence
            p_res = str(prev_result).split('(')[0].strip()
            c_res = str(current_result).split('(')[0].strip()
            
            if p_res in severity_levels and c_res in severity_levels:
                prev_idx = severity_levels.index(p_res)
                curr_idx = severity_levels.index(c_res)
                
                if curr_idx > prev_idx:
                    trend = "Worsened (Rapid deterioration)" if curr_idx - prev_idx > 1 else "Worsened"
                    trend_color = "#991b1b"
                elif curr_idx < prev_idx:
                    trend = "Improved"
                    trend_color = "#166534"
                else:
                    trend = "Stable"
                    trend_color = "#4b5563"
                    
                self.progression_text.setText(f"Progression detected: {p_res} \u2192 {c_res}")
                self.progression_trend.setText(f"Trend: {trend}")
                self.progression_trend.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {trend_color};")
                self.progression_trend.show()
            else:
                self.progression_text.setText(f"Follow-up: {p_res} to {c_res}")
                self.progression_trend.setText("") # Blank if not a standard DR stage
                self.progression_trend.hide()
                
            self.progression_panel.show()
                
        except (ValueError, TypeError):
            self.progression_panel.hide()

    def save_patient(self):
        if not self.parent_page or not hasattr(self.parent_page, "save_screening"):
            return

        # Formal clinical finalization prompt
        confirm_box = QMessageBox(self)
        apply_dialog_style(confirm_box)
        confirm_box.setWindowTitle("Finalize Assessment")
        confirm_box.setIcon(QMessageBox.Icon.Question)
        confirm_box.setText("<b>Confirm Clinical Finalization</b>")
        confirm_box.setInformativeText(
            "Are you certain you wish to finalize and save this clinical screening result? "
            "Once saved, the diagnostic data will be committed to the patient's permanent record."
        )
        confirm_btn = confirm_box.addButton("Finalize and Save", QMessageBox.ButtonRole.AcceptRole)
        confirm_box.addButton("Review Assessment", QMessageBox.ButtonRole.RejectRole)
        confirm_box.setDefaultButton(confirm_btn)
        
        confirm_box.exec()
        if confirm_box.clickedButton() != confirm_btn:
            return

        # Pre-save decision (doctor UX): prompt to screen other eye on the *first-eye* save.
        # Do NOT use historical record existence; use current session state instead.
        pp = self.parent_page
        current_eye = str(pp.p_eye.currentText() or "").strip() if hasattr(pp, "p_eye") else ""
        opposite_eye = "Left Eye" if current_eye == "Right Eye" else "Right Eye"

        is_second_eye_flow = bool(getattr(pp, "_is_second_eye_flow", False))

        # Robust session check: if both eyes have been analyzed in this session, 
        # then we are definitely saving the second eye.
        analyzed_count = 0
        with contextlib.suppress(Exception):
            analyzed_count = len(getattr(pp, "_analyzed_eyes", set()))
        if analyzed_count >= 2:
            is_second_eye_flow = True

        go_screen_other_after_save = False
        if not is_second_eye_flow:
            box = QMessageBox(self)
            box.setWindowTitle("Save Screening")
            box.setIcon(QMessageBox.Icon.Question)
            if current_eye and opposite_eye:
                box.setText(
                    f"Save this <b>{current_eye}</b> result.\n\n"
                    f"Do you need to screen the <b>{opposite_eye}</b> after saving?"
                )
            else:
                box.setText("Do you need to screen the other eye after saving this result?")
            other_btn = box.addButton("Screen Another Eye", QMessageBox.ButtonRole.AcceptRole)
            just_btn = box.addButton("Just This Eye", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            chosen = box.clickedButton()
            if chosen is None or chosen == box.button(QMessageBox.StandardButton.Cancel):
                self._set_save_state("idle")
                return
            go_screen_other_after_save = chosen == other_btn

        self._set_save_state("writing", "Saving to local records...")
        QApplication.processEvents()
        result = self.parent_page.save_screening(reset_after=False)  # Changed to False - don't auto-reset

        if not isinstance(result, dict):
            self._set_save_state("failed", "Save failed due to an unexpected response.")
            return

        status = result.get("status")
        if status in ("saved", "replaced"):
            saved_path = str(result.get("path") or "")
            details = f"Saved  {saved_path}" if saved_path else "Saved "
            self._set_save_state("success", details)

            # Second-eye save should FINISH the bilateral session without navigating away.
            # Do not bounce back to diagnosis/intake. Just confirm completion and stay on results.
            # Second-eye completion detection:
            # - `_is_second_eye_flow` is set when the user taps "Screen Other Eye"
            # - `_second_eye_result` is set by `save_screening()` when the other eye is saved
            if is_second_eye_flow or bool(getattr(pp, "_second_eye_result", None)):
                box = QMessageBox(self)
                apply_dialog_style(box)
                box.setWindowTitle("Session Completed")
                box.setIcon(QMessageBox.Icon.Information)
                box.setText("<b>Both eyes have been successfully screened and saved.</b><br><br>The screening session for this patient is now complete.")

                ok_btn = box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
                refer_btn = box.addButton("Create Referral", QMessageBox.ButtonRole.ActionRole)
                queue_btn = box.addButton("Back to Patient Queue", QMessageBox.ButtonRole.ActionRole)
                box.setDefaultButton(ok_btn)

                box.exec()
                choice = box.clickedButton()

                # Reset state.
                with contextlib.suppress(Exception):
                    pp._is_second_eye_flow = False
                    if hasattr(pp, "p_eye"):
                        pp.p_eye.setEnabled(True)

                if choice == refer_btn:
                    self.generate_referral()
                elif choice == queue_btn:
                    # Clear results before navigating
                    self.reset_ui()
                    if hasattr(pp, "reset_screening"):
                        pp.reset_screening(confirm_unsaved=False)

                    main_win = self.window()
                    if hasattr(main_win, "_navigate_to"):
                        main_win._navigate_to(10, nav_key="Patient Queue")
                    elif hasattr(main_win, "pages"):
                        main_win.pages.setCurrentIndex(10)
                return

            # If user opted to screen the other eye, switch back to the upload/intake view
            # and auto-select the opposite eye.
            if go_screen_other_after_save and hasattr(pp, "screen_other_eye"):
                QTimer.singleShot(0, pp.screen_other_eye)
                return

            # If user opted for 'Just This Eye', show completion prompt
            if not go_screen_other_after_save:
                box = QMessageBox(self)
                box.setWindowTitle("Saved")
                box.setText("Patient was successfully saved.")
                ok_btn = box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
                refer_btn = box.addButton("Create Referral", QMessageBox.ButtonRole.ActionRole)
                queue_btn = box.addButton("Back to Patient Queue List", QMessageBox.ButtonRole.ActionRole)
                box.exec()
                choice = box.clickedButton()

                if choice == refer_btn:
                    success = self.generate_referral()
                    if success:
                        q_box = QMessageBox.question(
                            self,
                            "Patient Queue",
                            "Would you like to go back to patient queue list?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.Yes,
                        )
                        if q_box == QMessageBox.StandardButton.Yes:
                            self.reset_ui()
                            if hasattr(pp, "reset_screening"):
                                pp.reset_screening(confirm_unsaved=False)
                            main_win = self.window()
                            if hasattr(main_win, "_navigate_to"):
                                main_win._navigate_to(10, nav_key="Patient Queue")
                            elif hasattr(main_win, "pages"):
                                main_win.pages.setCurrentIndex(10)
                elif choice == queue_btn:
                    self.reset_ui()
                    if hasattr(pp, "reset_screening"):
                        pp.reset_screening(confirm_unsaved=False)
                    main_win = self.window()
                    if hasattr(main_win, "_navigate_to"):
                        main_win._navigate_to(10, nav_key="Patient Queue")
                    elif hasattr(main_win, "pages"):
                        main_win.pages.setCurrentIndex(10)
                return

            # Otherwise (screen other eye), logic handled above.
            return

        if status == "unchanged":
            self._set_save_state("unchanged")
            return

        if status == "invalid":
            self._set_save_state("failed", "Please complete required fields before saving.")
            return

        if status == "cancelled":
            self._set_save_state("idle")
            return

        if status in ("error", "blocked"):
            self._set_save_state("failed", str(result.get("error") or "Save failed"))
            box = QMessageBox(self)
            box.setWindowTitle("Save Failed")
            box.setIcon(QMessageBox.Icon.Critical)
            box.setText(str(result.get("error") or "Save failed"))
            retry_btn = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
            change_btn = box.addButton("Change Save Location", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() == retry_btn:
                self.save_patient()
                return
            if box.clickedButton() == change_btn:
                folder = QFileDialog.getExistingDirectory(self, "Choose Save Location")
                if folder:
                    self.parent_page._custom_storage_root = folder
                    self.save_patient()
            return

        self._set_save_state("failed", "Save was not completed.")

    def new_patient(self):
        if not self.parent_page:
            return
        page = self.parent_page
        if not getattr(page, "_current_eye_saved", True):
            current_eye = page.p_eye.currentText() if hasattr(page, "p_eye") else "screening"
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved Screening Result")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                f"This <b>{current_eye}</b> screening result has not been saved. Starting a new patient will permanently discard it."
            )
            save_first_btn = box.addButton("Save First", QMessageBox.ButtonRole.AcceptRole)
            discard_btn = box.addButton("Discard and Continue", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel_btn)
            box.exec()
            choice = box.clickedButton()
            if choice == save_first_btn:
                self.save_patient()
                if getattr(page, "_current_eye_saved", False):
                    write_activity("INFO", "DIALOG_NEW_PATIENT", "Save First")
                    page.reset_screening()
                return
            if choice != discard_btn:
                write_activity("INFO", "DIALOG_NEW_PATIENT", "Cancel")
                return
            write_activity("WARNING", "DIALOG_NEW_PATIENT", "Discard and Continue")

        has_visible_result = bool(str(getattr(self, "_current_image_path", "") or "").strip())
        if has_visible_result:
            confirm_clear = QMessageBox.question(
                self,
                "Clear Current Results",
                "Starting a new patient will clear the current results area. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm_clear != QMessageBox.StandardButton.Yes:
                write_activity("INFO", "DIALOG_NEW_PATIENT", "Cancel Clear Results")
                return

        if hasattr(page, "reset_screening"):
            page.reset_screening()



    #  Report generation 

    def generate_report(self):
        """Generate a PDF screening report for the current patient."""
        if self._current_result_class in ("Pending", "Analyzing") or not self._current_image_path:
            QMessageBox.information(self, "Generate Report", "No completed screening results to report.")
            return

        pp = self.parent_page
        
        # Prepare patient_record (Identity + History)
        patient_record = {
            "name": self._current_patient_name or "Patient",
            "patient_id": pp.p_id.text().strip() if pp and hasattr(pp, "p_id") else "",
            "birthdate": pp.p_dob.text() if pp and hasattr(pp, "p_dob") and hasattr(pp.p_dob, "text") else "",
            "age": str(pp.p_age.value()) if pp and hasattr(pp, "p_age") else "",
            "sex": pp.p_sex.currentText() if pp and hasattr(pp, "p_sex") else "",
            "phone": (pp.p_phone.text().strip() if hasattr(pp, "p_phone") else "") or (pp.p_contact.text().strip() if hasattr(pp, "p_contact") else ""),
            "email": pp.p_email.text().strip() if pp and hasattr(pp, "p_email") else "",
            "address": pp.p_address.text().strip() if pp and hasattr(pp, "p_address") else "",
            "diabetes_type": pp.diabetes_type.currentText() if pp and hasattr(pp, "diabetes_type") else "",
            "diag_date": pp._get_diagnosis_date().toString("MMMM d, yyyy") if pp and hasattr(pp, "_get_diagnosis_date") and pp._get_diagnosis_date().isValid() else "",
            "duration": str(pp.diabetes_duration.value()) if pp and hasattr(pp, "diabetes_duration") else "",
            "treatment_regimen": pp.treatment_regimen.currentText() if pp and hasattr(pp, "treatment_regimen") else "",
        }

        # Prepare eye_records
        eye_records = []
        decision = self.get_decision_payload()
        
        # Current eye (second eye if bilateral, or only eye if single)
        current_eye = {
            "eyes": self._current_eye_label or "Eye",
            "result": self._current_result_class or "No DR",
            "confidence": self._current_confidence or "—",
            "source_image_path": self._current_image_path,
            "heatmap_image_path": self._current_heatmap_path,
            "final_diagnosis_icdr": decision.get("final_diagnosis_icdr"),
            "doctor_findings": decision.get("doctor_findings"),
            "screened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # If bilateral, add the first eye
        if hasattr(self, "_first_eye_context") and self._first_eye_context:
            eye_records.append(self._first_eye_context)
        
        eye_records.append(current_eye)

        # Call unified generator
        username = os.environ.get("EYESHIELD_CURRENT_NAME", os.environ.get("EYESHIELD_CURRENT_USER", "Staff"))
        success = generate_unified_patient_report(self, patient_record, eye_records, username)
        
        if success:
            write_activity("INFO", "REPORT_GENERATED", f"patient_id={patient_record['patient_id']}")

    def _show_referral_options(self):
        """Start referral letter generation flow."""
        if self._current_result_class in ("Pending", "Analyzing") or not self._current_image_path:
            QMessageBox.information(self, "Referral", "No completed screening result available for referral.")
            return

        if self.parent_page and not getattr(self.parent_page, "_current_eye_saved", False) and str(
            self._current_result_class or ""
        ).strip() != SYSTEM_UNCERTAIN_LABEL:
            QMessageBox.warning(self, "Referral", "Please save the result before creating a referral letter.")
            return
        self.generate_referral()

    def generate_referral(self) -> bool:
        """Generate a referral letter PDF from screening results. Returns True if successful."""
        if self._current_result_class in ("Pending", "Analyzing") or not self._current_image_path:
            QMessageBox.information(self, "Generate Referral", "No completed screening results to generate referral.")
            return False

        _ai_uncertain = str(self._current_result_class or "").strip() == SYSTEM_UNCERTAIN_LABEL
        if self.parent_page and not getattr(self.parent_page, "_current_eye_saved", False) and not _ai_uncertain:
            QMessageBox.warning(self, "Generate Referral", "Please save the result before generating a referral")
            return False

        destination = self._prompt_referral_destination()
        if not destination:
            return False
        if destination.get("_action") == "back":
            return False

        # Get patient data from parent page
        patient_name_raw = str(self._current_patient_name or "Patient").strip()
        default_name = f"EyeShield_Referral_{patient_name_raw}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save Referral Letter", default_name, "PDF Files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path = f"{path}.pdf"

        try:
            from PySide6.QtGui import QPdfWriter, QPageSize, QPageLayout, QTextDocument
            from PySide6.QtCore import QMarginsF
        except ImportError:
            QMessageBox.warning(self, "Generate Referral", "PDF generation requires PySide6 PDF support.")
            return False

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

        username = self._resolve_actor_username()
        if not username:
            QMessageBox.warning(self, "Generate Referral", "Current logged-in user could not be resolved. Please sign in again.")
            return False

        # Fetch doctor's profile
        profile = UserManager.get_user_profile(username) or {}
        screened_by_name = str(profile.get("full_name") or profile.get("display_name") or username).strip()
        screened_by_title = str(profile.get("specialization") or "").strip()
        screened_by_raw = (
            f"{screened_by_name} ({screened_by_title})"
            if screened_by_name and screened_by_title
            else screened_by_name
        )
        screened_by_label = screened_by_raw if screened_by_raw.lower().startswith("dr.") else f"Dr. {screened_by_raw}"
        doctor_contact = str(profile.get("contact") or "").strip()

        # Referral mapping
        referral_map = {
            "No DR": ("Routine", "Annual follow-up and routine retinal screening."),
            "Mild DR": ("Routine", "Repeat retinal assessment in 6-12 months is advised."),
            "Moderate DR": ("Priority", "Refer to ophthalmology within 3 months for specialist evaluation."),
            "Severe DR": ("Urgent", "Urgent ophthalmology review is advised due to high progression risk."),
            "Proliferative DR": ("Immediate", "Immediate specialist referral is required for potential sight-threatening disease."),
            SYSTEM_UNCERTAIN_LABEL: (
                "Priority",
                "Automated DR screening produced an indeterminate grade; specialist examination is requested for diagnosis and management.",
            ),
        }
        final_dx = self.get_decision_payload().get("final_diagnosis_icdr") or self._current_result_class
        is_uncertain_referral = str(self._current_result_class or "").strip() == SYSTEM_UNCERTAIN_LABEL
        if is_uncertain_referral:
            urgency, rationale = referral_map[SYSTEM_UNCERTAIN_LABEL]
        else:
            urgency, rationale = referral_map.get(final_dx, ("Clinical Review", "Please evaluate for diabetic retinopathy management."))

        report_date = datetime.now().strftime("%B %d, %Y")
        screen_date_text = esc(_to_long_date(datetime.now().strftime("%B %d, %Y")))

        # Get patient data - try to get from parent page
        patient_data = {}
        if self.parent_page and hasattr(self.parent_page, "_patient_data"):
            patient_data = self.parent_page._patient_data or {}

        patient_dob = esc(patient_data.get("birthdate") or patient_data.get("dob") or "")
        patient_age = esc(patient_data.get("age") or "")
        patient_sex = esc(patient_data.get("sex") or "")
        patient_hba1c = esc(patient_data.get("hba1c") or "")
        patient_diabetes_type = esc(patient_data.get("diabetes_type") or "")
        patient_height = esc(patient_data.get("height") or "")
        patient_weight = esc(patient_data.get("weight") or "")
        patient_bmi = esc(patient_data.get("bmi") or "")
        patient_visual_acuity_left = esc(patient_data.get("visual_acuity_left") or "")
        patient_visual_acuity_right = esc(patient_data.get("visual_acuity_right") or "")
        patient_notes_raw = str(patient_data.get("notes") or "").strip()
        if len(patient_notes_raw) > 220:
            patient_notes_raw = f"{patient_notes_raw[:217].rstrip()}..."
        patient_notes = esc(patient_notes_raw)

        doctor_full = str(destination.get("contact_person") or "").strip()
        hosp_name = str(destination.get("hospital_name") or "").strip()
        hosp_addr = str(destination.get("address") or "").strip()
        
        parts = doctor_full.split()
        surname = parts[-1] if parts else ""
        
        destination_name = esc(hosp_name)
        doctor_label = esc(doctor_full)
        destination_addr = esc(hosp_addr)
        current_source_uri = ""
        current_heatmap_uri = ""
        image_path = str(self._current_image_path or "").strip()
        heatmap_path = str(self._current_heatmap_path or "").strip()
        if image_path and os.path.exists(image_path):
            current_source_uri = Path(image_path).resolve().as_uri()
        if heatmap_path and os.path.exists(heatmap_path):
            current_heatmap_uri = Path(heatmap_path).resolve().as_uri()

        first_eye_ctx = dict(getattr(self, "_first_eye_context", {}) or {})
        first_eye_label = str(first_eye_ctx.get("eye") or "").strip()
        first_source_uri = ""
        first_heatmap_uri = ""
        first_source_path = str(first_eye_ctx.get("image_path") or "").strip()
        first_heatmap_path = str(first_eye_ctx.get("heatmap_path") or "").strip()
        if first_source_path and os.path.exists(first_source_path):
            first_source_uri = Path(first_source_path).resolve().as_uri()
        if first_heatmap_path and os.path.exists(first_heatmap_path):
            first_heatmap_uri = Path(first_heatmap_path).resolve().as_uri()

        second_eye_label = str(self._current_eye_label or "").strip() or "Current Eye"
        is_bilateral_referral = bool(first_eye_label)

        def _scaled_referral_image(uri: str, file_path: str, missing_text: str) -> str:
            if not uri:
                return f'<div style="padding:26px 14px;color:#9ca3af;font-style:italic;">{missing_text}</div>'

            max_w, max_h = 380, 280
            width, height = max_w, max_h
            if file_path and os.path.exists(file_path):
                image = QImage(file_path)
                if not image.isNull() and image.width() > 0 and image.height() > 0:
                    ratio = min(max_w / image.width(), max_h / image.height())
                    ratio = min(ratio, 1.0)
                    width = max(1, int(image.width() * ratio))
                    height = max(1, int(image.height() * ratio))

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
        <div style="text-align:center;background:#ffffff;padding:8px;border:1px solid #e5e7eb;min-height:230px;">{source_html}</div>
    </div>
"""

        if is_bilateral_referral:
            referral_images_html = (
                "<div class=\"subject\">Bilateral Fundus Images Captured</div>"
                "<div class=\"paragraph\">"
                "The following retinal fundus images from both screened eyes are attached for specialist reference."
                "</div>"
                + _referral_eye_block(first_eye_label, first_source_uri, first_source_path)
                + _referral_eye_block(second_eye_label, current_source_uri, image_path)
            )
        else:
            referral_images_html = (
                "<div class=\"subject\">Fundus Image Captured</div>"
                "<div class=\"paragraph\">"
                "The following retinal fundus image was captured during this screening encounter and is attached for specialist reference."
                "</div>"
                + _referral_eye_block(second_eye_label, current_source_uri, image_path)
            )

        # Build professional 2-page HTML
        style = """
        <style>
            @page { margin: 8mm; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #1e293b; line-height: 1.3; font-size: 10.5pt; margin: 0; padding: 0; }
            .page { width: 100%; }
            .header { text-align: center; margin-bottom: 10px; }
            .header h1 { font-size: 18pt; color: #0f172a; text-transform: uppercase; border-bottom: 2px solid #0f172a; padding-bottom: 3px; margin: 0; }
            .meta-row { margin-bottom: 2px; }
            .subject { font-weight: bold; margin-top: 10px; margin-bottom: 10px; text-decoration: underline; }
            .section-title { font-weight: bold; margin-top: 10px; margin-bottom: 3px; color: #334155; text-transform: uppercase; font-size: 10pt; }
            .findings-list { margin-left: 20px; margin-top: 3px; margin-bottom: 5px; }
            .findings-list li { margin-bottom: 1px; }
            .footer { margin-top: 15px; }
            .page-break { display: none; }
            .image-row { text-align: center; margin-top: 10px; }
            .image-container { display: inline-block; text-align: center; margin: 5px; border: 1px solid #e2e8f0; padding: 5px; border-radius: 4px; }
            .image-container img { max-width: 280px; max-height: 200px; object-fit: contain; }
            .eye-label { font-size: 10pt; font-weight: bold; color: #1e40af; margin-bottom: 3px; }
            p { margin: 0 0 6px 0; }
            .referral-uncertain-panel { background: #fffbeb; border-left: 4px solid #d97706; padding: 8px 10px; margin: 8px 0; font-size: 9.5pt; }
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
        html += f"<div class='subject'>Subject: Clinical Referral for Patient: {esc(patient_name_raw)}</div>"
        
        html += f"<p>Dear Dr. {esc(surname)},</p>"
        html += "<p>I am writing to formally refer the above-mentioned patient to your specialized care for further evaluation and management.</p>"

        referral_eyes = []
        if first_eye_label and first_source_path:
            referral_eyes.append({
                "label": first_eye_label.upper(),
                "diagnosis": esc(first_eye_ctx.get("result") or "N/A"),
                "path": first_source_path,
            })

        display_dx_second = SYSTEM_UNCERTAIN_LABEL if is_uncertain_referral else final_dx
        referral_eyes.append({
            "label": second_eye_label.upper(),
            "diagnosis": esc(display_dx_second),
            "path": image_path,
        })

        findings_items = "".join(
            f"<li><strong>{eye['label']}:</strong> {eye['diagnosis']}</li>" for eye in referral_eyes
        )

        if is_uncertain_referral:
            html += "<div class='referral-uncertain-panel'>"
            html += "<p><strong>Screening system outcome:</strong> Automated diabetic retinopathy (DR) grading did not reach sufficient confidence to assign a reliable ICDR severity score for this encounter. "
            html += "This result does <em>not</em> exclude retinopathy or other pathology; it indicates that specialist interpretation is required.</p>"
            html += f"<p><strong>Clinical note:</strong> {esc(rationale)}</p>"
            html += "</div>"
            html += "<div class='section-title'>Summary by eye (screening documentation)</div>"
            html += "<ul class='findings-list'>"
            html += findings_items
            html += "</ul>"
            html += (
                "<p>I would appreciate your expert assessment and management recommendations. "
                "Thank you for accepting this referral.</p>"
            )
        else:
            html += "<div class='section-title'>Clinical Findings:</div>"
            html += f"<p>Based on the Diabetic Retinopathy (DR) screening conducted today ({screen_date_text}), the following status has been identified:</p>"
            html += "<ul class='findings-list'>"
            html += findings_items
            html += "</ul>"
            html += "<p>I would appreciate your expert consultation and any necessary intervention or specialized care that the patient may require. "
            html += "Screening reports and fundus images have been provided to the patient for your reference.</p>"
            html += "<p>Thank you for your collaboration in providing comprehensive care for this patient.</p>"

        html += "<div class='footer'>"
        html += "<p>Sincerely,</p><br>"
        html += f"<strong>{screened_by_label}</strong><br>"
        html += "EyeShield DR Screening System"
        html += "</div>"
        html += "</div>" # End Page 1

        # Images section (on the same page)
        html += "<div class='section-title'>Retinal Fundus Images</div>"
        html += "<div class='image-row'>"
        for eye in referral_eyes:
            img_url = Path(eye['path']).resolve().as_uri()
            html += "<div class='image-container'>"
            html += f"<div class='eye-label'>{eye['label']}</div>"
            html += f"<img src='{img_url}'>"
            html += "</div>"
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
            return False
        write_activity("INFO", "REFERRAL_GENERATED", f"path={path}")
        referral_id = f"REF-{datetime.now().strftime('%Y%m%d%H%M%S')}-LETTER"
        UserManager.log_external_referral_letter(
            referral_id=referral_id,
            actor_username=username,
            patient_name=patient_name_raw,
            destination_name=hosp_name,
            destination_department="",
            destination_contact="",
            urgency=urgency,
            pdf_path=path,
        )
        QMessageBox.information(self, "Referral Saved", f"Referral letter saved to:\n{path}")
        return True

    def _prompt_referral_destination(self) -> dict | None:
        hospitals = UserManager.list_referral_hospitals(active_only=True)

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Medical Partner")
        dialog.setFixedSize(650, 240)

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
        cancel_btn = QPushButton("Cancel")
        continue_btn = QPushButton("Continue")
        continue_btn.setObjectName("primaryAction")
        action_row.addStretch(1)
        action_row.addWidget(cancel_btn)
        action_row.addWidget(continue_btn)
        layout.addLayout(action_row)

        cancel_btn.clicked.connect(dialog.reject)
        continue_btn.clicked.connect(dialog.accept)

        def _prompt_manual_destination() -> dict | None:
            manual_dialog = QDialog(dialog)
            manual_dialog.setWindowTitle("Manual Medical Partner Entry")
            manual_dialog.setFixedSize(600, 380)

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
            display = hospital_name if not department else f"{hospital_name} ({department})"
            return {
                "hospital_name": hospital_name,
                "department": department,
                "contact_person": contact,
                "address": address,
                "display": display,
            }
