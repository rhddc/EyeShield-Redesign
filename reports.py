"""
Reports module for EyeShield EMR application.
Placeholder for reports functionality.
"""

from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt


class ReportsPage(QWidget):
    """Reports page"""

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        title = QLabel("Reports")
        title.setStyleSheet("font-size:20px;font-weight:bold;")
        
        content = QLabel("Reports – coming soon")
        content.setAlignment(Qt.AlignCenter)
        content.setStyleSheet("font-size:16px;color:#6c757d;")

        layout.addWidget(title)
        layout.addWidget(content)
        layout.addStretch()
