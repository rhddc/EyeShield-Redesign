"""
Patient Records module for EyeShield EMR application.
Handles patient records display and management.
"""

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QTableWidget, QTableWidgetItem, QLineEdit, QHBoxLayout, QPushButton, QFileDialog, QDialog, QTextEdit
)
from PySide6.QtGui import QFont
import sqlite3
from auth import DB_FILE

class PatientRecordsPage(QWidget):
    """Patient records page with search, export, and detail view"""

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Patients / Screenings")
        title.setStyleSheet("font-size:22px;font-weight:700;color:#007bff;font-family:'Segoe UI','Inter','Arial';margin-bottom:4px;")
        layout.addWidget(title)

        # Search bar and export button
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by name, ID, or result...")
        self.search_input.setFixedWidth(260)
        self.search_input.setStyleSheet("padding: 5px 10px; border-radius: 5px; font-size: 14px;")
        self.search_input.textChanged.connect(self.filter_table)
        top_bar.addWidget(self.search_input)

        top_bar.addStretch(1)

        export_btn = QPushButton("Export to CSV")
        export_btn.setStyleSheet("padding: 6px 16px; font-size: 14px; border-radius: 5px;")
        export_btn.clicked.connect(self.export_to_csv)
        top_bar.addWidget(export_btn)

        layout.addLayout(top_bar)

        # Patient table setup
        self.patient_table = QTableWidget(0, 14)
        self.patient_table.setHorizontalHeaderLabels([
            "Patient ID",
            "Name",
            "Birthdate",
            "Age",
            "Sex",
            "Contact",
            "Eye(s)",
            "Diabetes Type",
            "Duration (yrs)",
            "HbA1c",
            "Prev Treatment",
            "Notes",
            "Result",
            "Confidence"
        ])
        self.patient_table.cellDoubleClicked.connect(self.show_details_dialog)
        self.patient_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.patient_table.setAlternatingRowColors(True)
        self.patient_table.setStyleSheet(
            "QTableWidget { font-size: 14px; background: #fafbfc; } "
            "QHeaderView::section { background: #f5f5f5; color: #222; font-weight: bold; font-size: 14px; padding: 6px 0; border: none; } "
            "QTableWidget::item:selected { background: #e3f2fd; } "
        )
        self.patient_table.verticalHeader().setVisible(False)
        self.patient_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.patient_table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.patient_table)

        self._all_records = []
        self.load_records_from_db()

    def add_patient_record(self, patient_data):
        """Add a patient record to the table, store for filtering/export, and save to DB"""
        self._all_records.append(patient_data)
        self.save_record_to_db(patient_data)
        self._refresh_table()

    def save_record_to_db(self, patient_data):
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO patient_records (
                    patient_id, name, birthdate, age, sex, contact, eyes, diabetes_type, duration, hba1c, prev_treatment, notes, result, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, patient_data)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Failed to save patient record: {e}")

    def load_records_from_db(self):
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("SELECT patient_id, name, birthdate, age, sex, contact, eyes, diabetes_type, duration, hba1c, prev_treatment, notes, result, confidence FROM patient_records")
            rows = cur.fetchall()
            conn.close()
            self._all_records = [list(row) for row in rows]
            self._refresh_table()
        except Exception as e:
            print(f"Failed to load patient records: {e}")

    def _refresh_table(self, filter_text=""):
        """Refresh the table, optionally filtering by text"""
        self.patient_table.setRowCount(0)
        records = getattr(self, '_all_records', [])
        filter_text = (filter_text or "").strip().lower()
        for patient_data in records:
            # If search is empty, show all
            if filter_text == "":
                show = True
            else:
                show = any(filter_text in str(val).strip().lower() for val in patient_data if val is not None)
            if not show:
                continue
            row = self.patient_table.rowCount()
            self.patient_table.insertRow(row)
            for col, value in enumerate(patient_data):
                self.patient_table.setItem(row, col, QTableWidgetItem(str(value)))
        try:
            self.patient_table.resizeColumnsToContents()
        except Exception:
            pass
        # Notify parent/dashboard to refresh stats if available
        try:
            if hasattr(self, 'parent_app') and self.parent_app:
                self.parent_app.refresh_dashboard()
        except Exception:
            pass

    def filter_table(self, text):
        self._refresh_table(text)

    def export_to_csv(self):
        import csv
        path, _ = QFileDialog.getSaveFileName(self, "Export Patient Records", "", "CSV Files (*.csv)")
        if not path:
            return
        records = getattr(self, '_all_records', [])
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([self.patient_table.horizontalHeaderItem(i).text() for i in range(self.patient_table.columnCount())])
                for row in records:
                    writer.writerow(row)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Exported", f"Patient records exported to {path}")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Error", f"Failed to export: {e}")

    def show_details_dialog(self, row, col):
        """Show a dialog with detailed patient info"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Patient Details")
        layout = QVBoxLayout(dialog)
        details = ""
        for i in range(self.patient_table.columnCount()):
            header = self.patient_table.horizontalHeaderItem(i).text()
            value = self.patient_table.item(row, i).text() if self.patient_table.item(row, i) else ""
            details += f"<b>{header}:</b> {value}<br>"
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(details)
        layout.addWidget(text)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.setMinimumWidth(400)
        dialog.exec()
