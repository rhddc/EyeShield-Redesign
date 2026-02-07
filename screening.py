"""
Screening module for EyeShield EMR application.
Handles patient screening functionality and image analysis.
"""

import uuid
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QLineEdit, QVBoxLayout, QHBoxLayout,
    QFileDialog, QFormLayout, QGroupBox, QComboBox, QDateEdit, QMessageBox
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QDate


class ScreeningPage(QWidget):
    """Patient screening page"""

    def __init__(self):
        super().__init__()

        page = self
        root = QHBoxLayout(page)

        left = QWidget()
        l = QVBoxLayout(left)

        patient_box = QGroupBox("Patient Information")
        form = QFormLayout(patient_box)

        self.p_id = QLineEdit()
        self.p_id.setReadOnly(True)

        self.generate_patient_id()

        self.p_name = QLineEdit()

        self.p_sex = QComboBox()
        self.p_sex.addItems(["","Male", "Female", "Other"])

        self.p_age = QLineEdit()

        self.p_eye = QComboBox()
        self.p_eye.addItems(["","Left", "Right"])

        self.p_date = QDateEdit()
        self.p_date.setDate(QDate.currentDate())
        self.p_date.setCalendarPopup(True)

        form.addRow("Patient ID:", self.p_id)
        form.addRow("Full Name:", self.p_name)
        form.addRow("Sex:", self.p_sex)
        form.addRow("Age:", self.p_age)
        form.addRow("Eye:", self.p_eye)
        form.addRow("Exam Date:", self.p_date)

        l.addWidget(patient_box)

        # ---------- BUTTONS ---------- #

        img_box = QGroupBox("Controls")
        il = QVBoxLayout(img_box)

        self.btn_upload = QPushButton("Upload Image")
        self.btn_upload.clicked.connect(self.upload_image)

        self.btn_analyze = QPushButton("Analyze")
        self.btn_analyze.clicked.connect(self.analyze_image)
        self.btn_analyze.setEnabled(False)

        self.btn_save = QPushButton("Save Screening")
        self.btn_save.clicked.connect(self.save_screening)
        self.btn_save.setEnabled(False)

        il.addWidget(self.btn_upload)
        il.addWidget(self.btn_analyze)
        il.addWidget(self.btn_save)

        l.addWidget(img_box)

        new_btn = QPushButton("New Patient")
        new_btn.clicked.connect(self.reset_screening)

        l.addWidget(new_btn)
        l.addStretch()

        # ---------- RIGHT ---------- #

        right = QWidget()
        r = QVBoxLayout(right)

        self.image_label = QLabel("No image loaded")
        self.image_label.setMinimumSize(500, 380)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("border:2px dashed #AAA;")

        results = QGroupBox("Results")
        rf = QFormLayout(results)

        self.r_class = QLabel("-")
        self.r_conf = QLabel("-")

        rf.addRow("Classification:", self.r_class)
        rf.addRow("Confidence:", self.r_conf)

        r.addWidget(self.image_label)
        r.addWidget(results)

        root.addWidget(left)
        root.addWidget(right)

    def generate_patient_id(self):
        """Generate unique patient ID"""
        pid = f"P-{uuid.uuid4().hex[:8].upper()}"
        self.p_id.setText(pid)

    def upload_image(self):
        """Handle image upload"""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "", "Images (*.jpg *.png)"
        )

        if path:
            self.current_image = Path(path)
            pix = QPixmap(path)
            self.image_label.setPixmap(
                pix.scaled(500, 380, Qt.KeepAspectRatio)
            )
            self.btn_analyze.setEnabled(True)

    def analyze_image(self):
        """Analyze uploaded image"""
        if not self.p_name.text():
            QMessageBox.warning(self, "Missing", "Patient name required.")
            return

        if not self.p_age.text().isdigit():
            QMessageBox.warning(self, "Invalid", "Age must be numeric.")
            return

        self.r_class.setText("No DR Detected")
        self.r_conf.setText("94%")

        self.btn_save.setEnabled(True)

    def save_screening(self):
        """Save screening results"""
        # This should add to patient records table
        QMessageBox.information(self, "Saved", "Screening saved locally.")
        self.reset_screening()

    def reset_screening(self):
        """Reset screening form"""
        self.generate_patient_id()
        self.p_name.clear()
        self.p_age.clear()

        self.image_label.setText("No image loaded")
        self.image_label.setPixmap(QPixmap())

        self.r_class.setText("-")
        self.r_conf.setText("-")

        self.btn_save.setEnabled(False)
        self.btn_analyze.setEnabled(False)
