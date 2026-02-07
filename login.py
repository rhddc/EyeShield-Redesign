"""
Login module for EyeShield EMR application.
Handles user authentication and login window.
"""

from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QLineEdit, QVBoxLayout, QMessageBox
from PySide6.QtCore import Qt

from auth import verify_user


class LoginWindow(QWidget):
    """Login window for user authentication"""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("EyeShield EMR – Login")
        self.setFixedSize(500, 420)
        self.setStyleSheet("""
            QWidget {
                background: #f8f9fa;
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        title = QLabel("EyeShield EMR")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            font-size: 28px;
            font-weight: bold;
            color: #007bff;
            margin-bottom: 10px;
        """)

        subtitle = QLabel("Electronic Medical Records System")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("""
            font-size: 14px;
            color: #6c757d;
            margin-bottom: 30px;
        """)

        # Login form container
        form_widget = QWidget()
        form_widget.setStyleSheet("""
            QWidget {
                background: white;
                border-radius: 8px;
                border: 1px solid #dee2e6;
                padding: 20px;
            }
        """)
        form_layout = QVBoxLayout(form_widget)
        form_layout.setSpacing(15)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        self.username_input.setMinimumHeight(40)
        self.username_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 14px;
                background: white;
                min-width: 250px;
            }
            QLineEdit:focus {
                border-color: #007bff;
            }
        """)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumHeight(40)
        self.password_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 14px;
                background: white;
                min-width: 250px;
            }
            QLineEdit:focus {
                border-color: #007bff;
            }
        """)

        btn = QPushButton("Sign In")
        btn.setMinimumHeight(40)
        btn.setStyleSheet("""
            QPushButton {
                background: #007bff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #0056b3;
            }
        """)
        btn.clicked.connect(self.handle_login)

        form_layout.addWidget(self.username_input)
        form_layout.addWidget(self.password_input)
        form_layout.addWidget(btn)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(form_widget)

    def handle_login(self):
        """Handle login button click"""
        from dashboard import EyeShieldApp
        
        role = verify_user(
            self.username_input.text(),
            self.password_input.text()
        )

        if role:
            self.main = EyeShieldApp(self.username_input.text(), role)
            self.main.show()
            self.close()
        else:
            QMessageBox.warning(self, "Login Failed", "Invalid credentials.")
