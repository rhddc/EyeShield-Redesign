"""
Users management module for EyeShield EMR application.
Provides a GUI for creating, listing, updating and deleting users.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QPushButton, QLineEdit, QComboBox, QMessageBox,
    QGroupBox, QFormLayout, QSizePolicy, QAbstractItemView
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

from auth import UserManager


class UsersPage(QWidget):
    """User management page with improved layout and spacing"""

    def __init__(self):
        super().__init__()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(12)

        # Header: title + refresh
        header_layout = QHBoxLayout()
        title = QLabel("User Management")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedHeight(28)
        refresh_btn.clicked.connect(self.refresh_users)
        header_layout.addWidget(refresh_btn)

        root_layout.addLayout(header_layout)

        # Table group with subtle card style
        table_group = QGroupBox()
        table_group.setTitle("")
        table_group.setStyleSheet("QGroupBox { border: 1px solid #e6e6e6; border-radius: 8px; padding: 8px; }")
        table_layout = QVBoxLayout(table_group)
        table_layout.setContentsMargins(8, 8, 8, 8)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Username", "Role"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMinimumHeight(220)

        table_layout.addWidget(self.table)
        root_layout.addWidget(table_group)

        # Activity log display
        self.activity_log = QTableWidget(0, 3)
        self.activity_log.setHorizontalHeaderLabels(["User", "Action", "Timestamp"])
        self.activity_log.setMinimumHeight(120)
        self.activity_log.setEditTriggers(QTableWidget.NoEditTriggers)
        self.activity_log.setSelectionMode(QAbstractItemView.NoSelection)
        root_layout.addWidget(QLabel("User Activity Log (session only):"))
        root_layout.addWidget(self.activity_log)

        # Form group: Add user
        form_group = QGroupBox("Add New User")
        form_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        form_layout = QFormLayout(form_group)
        form_layout.setLabelAlignment(Qt.AlignRight)
        form_layout.setFormAlignment(Qt.AlignLeft)
        form_layout.setHorizontalSpacing(12)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter username")
        self.username_input.setFixedHeight(28)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setFixedHeight(28)

        self.role_input = QComboBox()
        self.role_input.addItems(["clinician", "admin", "viewer"])
        self.role_input.setFixedHeight(28)

        add_btn = QPushButton("Add User")
        add_btn.setFixedHeight(30)
        add_btn.clicked.connect(self.add_user)

        form_layout.addRow("Username:", self.username_input)
        form_layout.addRow("Password:", self.password_input)
        form_layout.addRow("Role:", self.role_input)
        form_layout.addRow("", add_btn)

        root_layout.addWidget(form_group)

        # Actions row: update role / delete / reset password
        actions_group = QGroupBox()
        actions_group.setTitle("")
        actions_layout = QHBoxLayout(actions_group)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)

        self.new_role_input = QComboBox()
        self.new_role_input.addItems(["clinician", "admin", "viewer"])
        self.new_role_input.setFixedHeight(28)

        update_btn = QPushButton("Update Role")
        update_btn.setFixedHeight(30)
        update_btn.clicked.connect(self.update_role)

        delete_btn = QPushButton("Delete User")
        delete_btn.setFixedHeight(30)
        delete_btn.clicked.connect(self.delete_user)

        reset_pw_btn = QPushButton("Reset Password")
        reset_pw_btn.setFixedHeight(30)
        reset_pw_btn.clicked.connect(self.reset_password)

        actions_layout.addWidget(QLabel("Selected user:"))
        actions_layout.addStretch()
        actions_layout.addWidget(self.new_role_input)
        actions_layout.addWidget(update_btn)
        actions_layout.addWidget(delete_btn)
        actions_layout.addWidget(reset_pw_btn)

        root_layout.addWidget(actions_group)

        # Make UI expand nicely
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.refresh_users()

    def refresh_users(self):
        """Reload users from the database into the table"""
        self.table.setRowCount(0)
        users = UserManager.get_all_users()

        for username, role in users:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(username))
            self.table.setItem(row, 1, QTableWidgetItem(role))

    def log_activity(self, user, action):
        """Add an entry to the activity log (session only)"""
        from datetime import datetime
        row = self.activity_log.rowCount()
        self.activity_log.insertRow(row)
        self.activity_log.setItem(row, 0, QTableWidgetItem(user))
        self.activity_log.setItem(row, 1, QTableWidgetItem(action))
        self.activity_log.setItem(row, 2, QTableWidgetItem(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    def add_user(self):
        """Create a new user using UserManager.create_user"""
        username = self.username_input.text().strip()
        password = self.password_input.text()
        role = self.role_input.currentText()

        if not username or not password:
            QMessageBox.warning(self, "Missing", "Username and password are required.")
            return

        success = UserManager.create_user(username, password, role)
        if success:
            QMessageBox.information(self, "Created", f"User '{username}' created.")
            self.username_input.clear()
            self.password_input.clear()
            self.refresh_users()
            self.log_activity(username, f"Created as {role}")
        else:
            QMessageBox.warning(self, "Error", "Could not create user (may already exist).")

    def delete_user(self):
        """Delete the selected user"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select", "Select a user to delete.")
            return

        username = self.table.item(row, 0).text()

        confirm = QMessageBox.question(self, "Delete", f"Delete user '{username}'?", QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        success = UserManager.delete_user(username)
        if success:
            QMessageBox.information(self, "Deleted", f"User '{username}' deleted.")
            self.refresh_users()
            self.log_activity(username, "Deleted")
        else:
            QMessageBox.warning(self, "Error", "Could not delete user.")

    def update_role(self):
        """Update role for the selected user"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select", "Select a user to update.")
            return

        username = self.table.item(row, 0).text()
        new_role = self.new_role_input.currentText()

        success = UserManager.update_user_role(username, new_role)
        if success:
            QMessageBox.information(self, "Updated", f"Role updated for '{username}'.")
            self.refresh_users()
            self.log_activity(username, f"Role changed to {new_role}")
        else:
            QMessageBox.warning(self, "Error", "Could not update role.")

    def reset_password(self):
        """Reset password for the selected user"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select", "Select a user to reset password.")
            return

        username = self.table.item(row, 0).text()
        from PySide6.QtWidgets import QInputDialog
        new_pw, ok = QInputDialog.getText(self, "Reset Password", f"Enter new password for '{username}':")
        if not ok or not new_pw:
            return
        # Use UserManager to update password (delete and recreate for demo)
        # In production, add a dedicated update_password method
        role = self.table.item(row, 1).text()
        UserManager.delete_user(username)
        UserManager.create_user(username, new_pw, role)
        self.refresh_users()
        self.log_activity(username, "Password reset")
        QMessageBox.information(self, "Reset", f"Password reset for '{username}'.")
