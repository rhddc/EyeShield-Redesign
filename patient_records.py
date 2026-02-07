"""
Patient Records module for EyeShield EMR application.
Handles patient records display and management.
"""

from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QTableWidget, QTableWidgetItem


class PatientRecordsPage(QWidget):
    """Patient records page"""

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        title = QLabel("Patients / Screenings")
        title.setStyleSheet("font-size:20px;font-weight:bold;")

        self.patient_table = QTableWidget(0, 6)
        self.patient_table.setHorizontalHeaderLabels([
            "Patient ID",
            "Name",
            "Age",
            "Sex",
            "Eye",
            "Result"
        ])

        layout.addWidget(title)
        layout.addWidget(self.patient_table)

    def add_patient_record(self, patient_data):
        """Add a patient record to the table"""
        row = self.patient_table.rowCount()
        self.patient_table.insertRow(row)

        for col, value in enumerate(patient_data):
            self.patient_table.setItem(row, col, QTableWidgetItem(str(value)))
