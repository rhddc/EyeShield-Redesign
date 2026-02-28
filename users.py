"""
Users management module for EyeShield EMR application.
Provides a GUI for creating, listing, updating and deleting users.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QPushButton, QLineEdit, QComboBox, QMessageBox,
    QGroupBox, QFormLayout, QSizePolicy, QAbstractItemView, QDialog,
    QSplitter, QFrame, QHeaderView, QGridLayout
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
import user_store

class UserManager:
    """User management class."""
    @staticmethod
    def create_user(username, password, role):
        """Create a new user."""
        return user_store.add_user(username, password, role)

    @staticmethod
    def get_all_users():
        """Get all users."""
        users = user_store.get_all_users()
        return [(user["username"], user["role"]) for user in users]

    @staticmethod
    def delete_user(username):
        """Delete a user."""
        return user_store.delete_user(username)

    @staticmethod
    def update_user_role(username, new_role):
        """Update role for a user."""
        return False

    @staticmethod
    def reset_password(username, new_password):
        """Reset password for a user."""
        return False

class NewUserDialog(QDialog):
    """Modal dialog for creating a new user."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New User")
        self.setModal(True)
        layout = QVBoxLayout(self)

        form_layout = QFormLayout()
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter username")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.role_input = QComboBox()
        self.role_input.addItems(["clinician", "admin", "viewer"])

        form_layout.addRow("Username:", self.username_input)
        form_layout.addRow("Password:", self.password_input)
        form_layout.addRow("Role:", self.role_input)
        layout.addLayout(form_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        create_btn = QPushButton("Create")
        cancel_btn = QPushButton("Cancel")
        create_btn.setAutoDefault(True)
        create_btn.setDefault(True)
        create_btn.clicked.connect(self._create_user)
        cancel_btn.clicked.connect(self.reject)
        self.password_input.returnPressed.connect(self._create_user)
        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _create_user(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        role = self.role_input.currentText()

        if not username or not password:
            QMessageBox.warning(self, "Missing", "Username and password are required.")
            return

        success = UserManager.create_user(username, password, role)
        if success:
            parent = self.parent()
            if hasattr(parent, 'refresh_users'):
                parent.refresh_users()
            if hasattr(parent, 'log_activity'):
                parent.log_activity(username, f"Created as {role}")
            if hasattr(parent, 'status_label'):
                parent.status_label.setText(f"User '{username}' created")
            self.accept()
        else:
            QMessageBox.warning(self, "Error", "Could not create user (may already exist).")


class UsersPage(QWidget):
    """Completely redesigned User Management page."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet("""
            QWidget {
                background: #f8f9fa;
                color: #212529;
                font-family: 'Segoe UI', 'Inter', 'Arial';
            }
            QLineEdit, QComboBox {
                background: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 8px;
                padding: 8px;
            }
            QLineEdit:focus, QComboBox:focus, QTableWidget:focus, QPushButton:focus {
                border: 1px solid #0d6efd;
            }
            QPushButton#primaryAction {
                background: #0d6efd;
                color: #ffffff;
                border: 1px solid #0b5ed7;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QLabel#statusLabel {
                color: #495057;
                font-size: 12px;
            }
        """)

        # Initialize UserManager instance
        self.user_manager = UserManager()

        # Store users in memory for demo (replace with persistent storage in real app)
        self._users = []

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("User Management")
        title_label.setStyleSheet("font-size:24px;font-weight:700;color:#007bff;font-family:'Segoe UI','Inter','Arial';")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        # Initialize count_label
        self.count_label = QLabel("0 users")
        self.count_label.setStyleSheet("color: #6c757d; margin-left: 8px;")
        header_layout.addWidget(self.count_label)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_users)
        header_layout.addWidget(refresh_button)
        main_layout.addLayout(header_layout)

        # Grid layout for main content
        grid_layout = QGridLayout()
        grid_layout.setSpacing(16)

        # Users Table
        self.users_table = QTableWidget(0, 3)
        self.users_table.setHorizontalHeaderLabels(["Username", "Role", "Status"])
        self.users_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.users_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.users_table.setAlternatingRowColors(True)
        self.users_table.setStyleSheet(
            "QTableWidget { background: #ffffff; gridline-color: #dcdcdc; }"
            "QHeaderView::section { background: #f0f0f0; padding: 8px; border: none; font-weight: 600; }"
            "QTableWidget::item { padding: 8px; }")
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # --- Place delete button inside users list area at bottom right ---
        users_list_box = QWidget()
        users_list_layout = QVBoxLayout(users_list_box)
        users_list_layout.setContentsMargins(0, 0, 0, 0)
        users_list_layout.setSpacing(4)
        users_list_layout.addWidget(self.users_table)
        delete_user_button = QPushButton("Delete Selected User")
        delete_user_button.clicked.connect(self.delete_user)
        delete_row = QHBoxLayout()
        delete_row.addStretch()
        delete_row.addWidget(delete_user_button, alignment=Qt.AlignRight)
        users_list_layout.addLayout(delete_row)
        grid_layout.addWidget(QLabel("Users List"), 0, 0)
        grid_layout.addWidget(users_list_box, 1, 0)
        # --- End users list area ---

        # --- Place activity log in a group box with similar design ---
        activity_log_box = QWidget()
        activity_log_layout = QVBoxLayout(activity_log_box)
        activity_log_layout.setContentsMargins(0, 0, 0, 0)
        activity_log_layout.setSpacing(4)
        self.activity_log = QTableWidget(0, 3)
        self.activity_log.setHorizontalHeaderLabels(["User", "Action", "Timestamp"])
        self.activity_log.setSelectionMode(QAbstractItemView.NoSelection)
        self.activity_log.setAlternatingRowColors(True)
        self.activity_log.setEditTriggers(QTableWidget.NoEditTriggers)
        self.activity_log.setStyleSheet(
            "QTableWidget { background: #ffffff; gridline-color: #dcdcdc; }"
            "QHeaderView::section { background: #f0f0f0; padding: 8px; border: none; font-weight: 600; }"
            "QTableWidget::item { padding: 8px; }")
        self.activity_log.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        activity_log_layout.addWidget(self.activity_log)
        grid_layout.addWidget(QLabel("Activity Log"), 0, 1)
        grid_layout.addWidget(activity_log_box, 1, 1)
        # --- End activity log area ---

        # Add User Form
        form_group = QGroupBox("Add New User")
        form_group.setStyleSheet("QGroupBox { font-weight: 600; background: #ffffff; border: 1px solid #dcdcdc; border-radius: 8px; padding: 16px; }")
        form_layout = QFormLayout(form_group)
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter username")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.role_input = QComboBox()
        self.role_input.addItems(["clinician", "admin", "viewer"])
        self.add_user_button = QPushButton("Add User")
        self.add_user_button.setObjectName("primaryAction")
        self.add_user_button.setAutoDefault(True)
        self.add_user_button.setDefault(True)
        self.add_user_button.setEnabled(False)
        self.add_user_button.clicked.connect(self.add_user)

        form_layout.addRow("Username:", self.username_input)
        form_layout.addRow("Password:", self.password_input)
        form_layout.addRow("Role:", self.role_input)
        form_layout.addRow(self.add_user_button)
        grid_layout.addWidget(form_group, 2, 0, 1, 2)

        # Add delete user button below the users table
        # delete_user_button = QPushButton("Delete Selected User")
        # delete_user_button.setStyleSheet("background:#dc3545;color:white;border-radius:4px;")
        # delete_user_button.clicked.connect(self.delete_user)
        # main_layout.addWidget(delete_user_button)

        main_layout.addLayout(grid_layout)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")
        main_layout.addWidget(self.status_label)

        # Connect input validation
        self.username_input.textChanged.connect(self._toggle_add_button)
        self.password_input.textChanged.connect(self._toggle_add_button)
        self.password_input.returnPressed.connect(self.add_user)

        self.setTabOrder(self.username_input, self.password_input)
        self.setTabOrder(self.password_input, self.role_input)
        self.setTabOrder(self.role_input, self.add_user_button)
        self.setTabOrder(self.add_user_button, self.users_table)

        self.refresh_users()

    def refresh_users(self):
        """Refresh the users table to show all users from the user_store file."""
        self.users_table.setRowCount(0)
        users = user_store.get_all_users()
        self.count_label.setText(f"{len(users)} users")
        for user in users:
            row_position = self.users_table.rowCount()
            self.users_table.insertRow(row_position)
            self.users_table.setItem(row_position, 0, QTableWidgetItem(user["username"]))
            self.users_table.setItem(row_position, 1, QTableWidgetItem(user["role"]))
            self.users_table.setItem(row_position, 2, QTableWidgetItem("Active"))

    def add_user(self):
        """Add a new user to the user_store file and log the action."""
        username = self.username_input.text().strip()
        password = self.password_input.text()
        role = self.role_input.currentText()

        if not username or not password:
            QMessageBox.warning(self, "Error", "Username and password are required.")
            return

        success = user_store.add_user(username, password, role)
        if success:
            self.status_label.setText(f"User '{username}' created")
            self.log_activity(username, f"Created as {role}")
            self.refresh_users()
            self.username_input.clear()
            self.password_input.clear()
            self.username_input.setFocus()
        else:
            QMessageBox.warning(self, "Error", "Failed to create user. User may already exist.")

    def _toggle_add_button(self):
        """Enable or disable the Add User button based on input validation."""
        self.add_user_button.setEnabled(bool(self.username_input.text().strip()) and bool(self.password_input.text().strip()))

    def delete_user(self):
        """Delete the selected user from the user_store file and log the action. Admins cannot delete other admins."""
        selected_row = self.users_table.currentRow()
        if selected_row == -1:
            QMessageBox.warning(self, "No Selection", "Please select a user to delete.")
            return

        username_item = self.users_table.item(selected_row, 0)
        role_item = self.users_table.item(selected_row, 1)
        if not username_item or not role_item:
            QMessageBox.warning(self, "Error", "Could not retrieve selected user.")
            return

        username = username_item.text()
        role = role_item.text()

        # Get current user's username and role from parent_app (EyeShieldApp)
        parent_app = getattr(self, 'parent_app', None)
        current_username = getattr(parent_app, 'username', None)
        current_role = getattr(parent_app, 'role', None)

        # Prevent any admin from deleting any admin except themselves (no warning, just do nothing)
        if role == "admin" and (current_role == "admin" and username != current_username):
            return

        confirm = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete user '{username}'?",
            QMessageBox.Yes | QMessageBox.No
        )

        if confirm == QMessageBox.Yes:
            success = user_store.delete_user(username)
            if success:
                self.status_label.setText(f"User '{username}' deleted")
                self.log_activity(username, "Deleted")
                self.refresh_users()
            else:
                QMessageBox.warning(self, "Error", f"Failed to delete user '{username}'.")

    def log_activity(self, user, action):
        """Add an entry to the activity log."""
        from datetime import datetime
        row = self.activity_log.rowCount()
        self.activity_log.insertRow(row)
        self.activity_log.setItem(row, 0, QTableWidgetItem(user))
        self.activity_log.setItem(row, 1, QTableWidgetItem(action))
        self.activity_log.setItem(row, 2, QTableWidgetItem(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
