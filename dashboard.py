"""
Dashboard module for EyeShield EMR application.
Contains main application window and dashboard functionality.
"""

import random
import sqlite3
from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QGroupBox, QMessageBox, QGridLayout
)
from PySide6.QtCore import Qt

from screening import ScreeningPage
from reports import ReportsPage
from users import UsersPage
from settings import SettingsPage
from help_support import HelpSupportPage
from camera import CameraPage
from auth import DB_FILE


class EyeShieldApp(QMainWindow):
    """Main application window"""

    def __init__(self, username, role):
        super().__init__()

        self.username = username
        self.role = role

        self.setWindowTitle("EyeShield – DR Screening")
        self.setMinimumSize(1350, 850)

        root = QWidget()
        root_layout = QVBoxLayout(root)

        # Create top navigation bar
        nav_bar = QWidget()
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(8, 8, 8, 8)
        nav_layout.setSpacing(8)
        nav_bar.setStyleSheet("background: #f8f9fa; border-bottom: 1px solid #dee2e6;")


        # App title
        title_label = QLabel("EyeShield EMR")
        title_label.setStyleSheet("color: #007bff; font-size: 24px; font-weight: 700; margin-right: 24px;")
        nav_layout.addWidget(title_label)

        # Navigation buttons with icons and small text labels below
        def nav_button_with_label(icon, text):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            btn = QPushButton(icon)
            btn.setStyleSheet(self.get_nav_button_style(icon_only=True))
            btn.setFixedSize(40, 40)
            label = QLabel(text)
            label.setAlignment(Qt.AlignHCenter)
            label.setStyleSheet("font-size: 11px; color: #495057; margin-top: 0px;")
            v.addWidget(btn)
            v.addWidget(label)
            return w, btn, label

        navs = [
            ("📊", "Dashboard"),
            ("🩺", "Screening"),
            ("📷", "Camera"),
            ("📄", "Reports"),
            ("👥", "Users"),
            ("⚙️", "Settings"),
            ("❓", "Help")
        ]
        nav_widgets = []
        nav_buttons = []
        nav_labels = []
        for icon, text in navs:
            w, btn, label = nav_button_with_label(icon, text)
            nav_layout.addWidget(w)
            nav_widgets.append(w)
            nav_buttons.append(btn)
            nav_labels.append(label)


        # User info on the right
        nav_layout.addStretch()
        user_info = QLabel(f"👤 {self.username} ({self.role})")
        user_info.setStyleSheet("color: #495057; font-size: 12px; font-weight: 500; margin-left: 16px; margin-right: 8px;")
        nav_layout.addWidget(user_info)

        logout_btn = QPushButton("Logout")
        logout_btn.setStyleSheet("""
            QPushButton {
                background: #dc3545;
                color: white;
                border: 1px solid #bb2d3b;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #c82333; }
        """)
        logout_btn.clicked.connect(self.handle_logout)
        nav_layout.addWidget(logout_btn)

        # Connect buttons
        nav_buttons[0].clicked.connect(lambda: self._navigate_to(0))
        nav_buttons[1].clicked.connect(lambda: self._navigate_to(1))
        nav_buttons[2].clicked.connect(lambda: self._navigate_to(2))
        nav_buttons[3].clicked.connect(lambda: self._navigate_to(3))
        nav_buttons[4].clicked.connect(lambda: self._navigate_to(4, requires_admin=True))
        nav_buttons[5].clicked.connect(lambda: self._navigate_to(5))
        nav_buttons[6].clicked.connect(lambda: self._navigate_to(6))

        if self.role != "admin":
            nav_buttons[4].setEnabled(False)
            nav_buttons[4].setToolTip("Admins only")
            nav_labels[4].setStyleSheet("font-size: 10px; color: #adb5bd; margin-top: 0px;")

        # (All navigation button connections are now handled via nav_buttons list above)

        root_layout.addWidget(nav_bar)

        # Main content area
        main = QWidget()
        main.setStyleSheet("background: #f8f9fa;")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.pages = QStackedWidget()

        # Create main pages first so dashboard can query live data
        self.screening_page = ScreeningPage()
        self.camera_page = CameraPage()
        self.reports_page = ReportsPage()
        self.users_page = UsersPage()
        self.settings_page = SettingsPage()
        self.help_support_page = HelpSupportPage()

        # Dashboard is created after the other pages so it can be refreshed
        self.dashboard_page = self.create_dashboard_page()

        self.users_page.parent_app = self

        self.pages.addWidget(self.dashboard_page)
        self.pages.addWidget(self.screening_page)
        self.pages.addWidget(self.camera_page)
        self.pages.addWidget(self.reports_page)
        self.pages.addWidget(self.users_page)
        self.pages.addWidget(self.settings_page)
        self.pages.addWidget(self.help_support_page)
        self.pages.currentChanged.connect(self._on_page_changed)

        main_layout.addWidget(self.pages)
        root_layout.addWidget(main)
        self.setCentralWidget(root)
        self.refresh_dashboard()

    # Sidebar removed; navigation is now in the top bar

    def _navigate_to(self, index, requires_admin=False):
        if requires_admin and self.role != "admin":
            QMessageBox.warning(self, "Access Denied", "Only admins can access the Users tab.")
            return
        self.pages.setCurrentIndex(index)

    def _on_page_changed(self, index):
        if index == 2:
            self.camera_page.enter_page()
        else:
            self.camera_page.leave_page()

    def handle_logout(self):
        reply = QMessageBox.question(
            self,
            "Logout",
            "Are you sure you want to log out?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from login import LoginWindow
        self.login = LoginWindow()
        self.login.show()
        self.close()

    def create_dashboard_page(self):
        """Create dashboard page"""
        page = QWidget()
        page.setStyleSheet("background: #f8f9fa;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        grid = QGridLayout()
        grid.setSpacing(14)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 1)

        def make_tile(title, accent="#007bff", minimum_height=120):
            tile = QWidget()
            tile.setObjectName("dashTile")
            tile.setMinimumHeight(minimum_height)
            tile.setStyleSheet(f"""
                QWidget#dashTile {{
                    background: white;
                    border: 1px solid #dee2e6;
                    border-left: 4px solid {accent};
                    border-radius: 8px;
                }}
                QLabel#tileTitle {{
                    color: #495057;
                    font-size: 12px;
                    font-weight: 700;
                    letter-spacing: 0.5px;
                    text-transform: uppercase;
                }}
            """)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(16, 16, 16, 16)
            tile_layout.setSpacing(8)
            title_label = QLabel(title)
            title_label.setObjectName("tileTitle")
            tile_layout.addWidget(title_label)
            return tile, tile_layout

        hero_tile, hero_layout = make_tile("Overview", accent="#007bff", minimum_height=170)
        welcome_title = QLabel(f"Welcome, {self.username}!")
        welcome_title.setStyleSheet("color: #007bff; font-size: 24px; font-weight: 700;")
        welcome_subtitle = QLabel("Diabetic Retinopathy Screening Command Center")
        welcome_subtitle.setStyleSheet("color: #6c757d; font-size: 14px;")
        self.quote_label = QLabel(self.get_medical_quote())
        self.quote_label.setStyleSheet("color: #495057; font-size: 13px; font-style: italic;")
        self.quote_label.setTextFormat(Qt.RichText)
        self.quote_label.setWordWrap(True)
        hero_layout.addWidget(welcome_title)
        hero_layout.addWidget(welcome_subtitle)
        hero_layout.addWidget(self.quote_label)
        grid.addWidget(hero_tile, 0, 0, 1, 2)

        session_tile, session_layout = make_tile("Session", accent="#17a2b8", minimum_height=170)
        self.dashboard_date_label = QLabel("")
        self.dashboard_date_label.setStyleSheet("font-size: 13px; color: #007bff; font-weight: 600;")
        role_label = QLabel(f"Role: {self.role.capitalize()}")
        role_label.setStyleSheet("font-size: 13px; color: #495057;")
        self.queue_status_label = QLabel("Queue: Ready")
        self.queue_status_label.setStyleSheet("font-size: 13px; color: #495057;")
        session_layout.addWidget(self.dashboard_date_label)
        session_layout.addWidget(role_label)
        session_layout.addWidget(self.queue_status_label)
        session_layout.addStretch()
        grid.addWidget(session_tile, 0, 2)

        total_tile, total_layout = make_tile("Total Screenings", accent="#28a745")
        self.total_screenings_value = QLabel("0")
        self.total_screenings_value.setStyleSheet("font-size: 32px; font-weight: 700; color: #212529;")
        total_hint = QLabel("All saved DR screenings")
        total_hint.setStyleSheet("font-size: 12px; color: #6c757d;")
        total_layout.addWidget(self.total_screenings_value)
        total_layout.addWidget(total_hint)
        total_layout.addStretch()
        grid.addWidget(total_tile, 1, 0)

        attention_tile, attention_layout = make_tile("High Attention", accent="#dc3545")
        self.high_attention_value = QLabel("0")
        self.high_attention_value.setStyleSheet("font-size: 32px; font-weight: 700; color: #212529;")
        self.high_attention_hint = QLabel("Cases flagged for follow-up")
        self.high_attention_hint.setStyleSheet("font-size: 12px; color: #6c757d;")
        attention_layout.addWidget(self.high_attention_value)
        attention_layout.addWidget(self.high_attention_hint)
        attention_layout.addStretch()
        grid.addWidget(attention_tile, 1, 1)

        actions_tile, actions_layout = make_tile("Clinical Actions", accent="#6f42c1", minimum_height=250)
        actions_layout.setSpacing(10)

        def make_action_btn(label, color, hover):
            btn = QPushButton(label)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color};
                    color: white;
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 8px;
                    padding: 8px 16px;
                    font-size: 13px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background: {hover}; }}
            """)
            return btn

        btn_new = make_action_btn("New Patient Screening", "#28a745", "#218838")
        btn_new.clicked.connect(lambda: self.pages.setCurrentIndex(1))

        btn_camera = make_action_btn("Open Camera", "#fd7e14", "#e66a00")
        btn_camera.clicked.connect(lambda: self.pages.setCurrentIndex(2))

        btn_reports = make_action_btn("Reports", "#17a2b8", "#117a8b")
        btn_reports.clicked.connect(lambda: self.pages.setCurrentIndex(3))

        btn_users = make_action_btn("Users", "#6f42c1", "#563d7c")
        btn_users.clicked.connect(lambda: self.pages.setCurrentIndex(4))

        actions_layout.addWidget(btn_new)
        actions_layout.addWidget(btn_camera)
        actions_layout.addWidget(btn_reports)
        actions_layout.addWidget(btn_users)
        actions_layout.addStretch()

        if self.role == "clinician":
            btn_users.setEnabled(False)
            btn_users.setToolTip("Admins only")

        grid.addWidget(actions_tile, 1, 2, 2, 1)

        confidence_tile, confidence_layout = make_tile("Average Confidence", accent="#ffc107")
        self.avg_confidence_value = QLabel("—")
        self.avg_confidence_value.setStyleSheet("font-size: 32px; font-weight: 700; color: #212529;")
        confidence_hint = QLabel("Across records with confidence data")
        confidence_hint.setStyleSheet("font-size: 12px; color: #6c757d;")
        confidence_layout.addWidget(self.avg_confidence_value)
        confidence_layout.addWidget(confidence_hint)
        confidence_layout.addStretch()
        grid.addWidget(confidence_tile, 2, 0)

        insight_tile, insight_layout = make_tile("Clinical Insight", accent="#fd7e14")
        self.insight_label = QLabel("Start a screening to generate real-time insight.")
        self.insight_label.setStyleSheet("font-size: 13px; color: #495057;")
        self.insight_label.setWordWrap(True)
        insight_layout.addWidget(self.insight_label)
        insight_layout.addStretch()
        grid.addWidget(insight_tile, 2, 1)

        activity_tile, activity_layout = make_tile("Recent Clinical Activity", accent="#007bff", minimum_height=180)
        self.recent_activity_label = QLabel("No recent clinical activity. Ready for patient screenings.")
        self.recent_activity_label.setStyleSheet("color: #6c757d; font-size: 14px; font-style: italic;")
        self.recent_activity_label.setWordWrap(True)
        activity_layout.addWidget(self.recent_activity_label)
        activity_layout.addStretch()
        grid.addWidget(activity_tile, 3, 0, 1, 2)

        quick_notes_tile, quick_notes_layout = make_tile("Workflow", accent="#20c997", minimum_height=180)
        quick_notes = QLabel("• Verify patient details before analysis\n• Capture clear retinal images\n• Record follow-up actions in notes")
        quick_notes.setStyleSheet("font-size: 13px; color: #495057; line-height: 1.35;")
        quick_notes.setWordWrap(True)
        quick_notes_layout.addWidget(quick_notes)
        quick_notes_layout.addStretch()
        grid.addWidget(quick_notes_tile, 3, 2)

        layout.addLayout(grid)
        layout.addStretch()
        return page

    @staticmethod
    def get_medical_quote():
        quotes = [
            ("Wherever the art of Medicine is loved, there is also a love of Humanity.", "Hippocrates"),
            ("Healing is a matter of time, but it is sometimes also a matter of opportunity.", "Hippocrates"),
            ("Life is short, art is long, opportunity fleeting, experience treacherous, judgment difficult.", "Hippocrates"),
            ("First, do no harm.", "Hippocrates (attributed)"),
            ("Let food be thy medicine and medicine be thy food.", "Hippocrates (attributed)"),
            ("The good physician treats the disease; the great physician treats the patient who has the disease.", "William Osler"),
            ("Medicine is a science of uncertainty and an art of probability.", "William Osler"),
            ("Listen to the patient, he is telling you the diagnosis.", "William Osler"),
            ("The practice of medicine is an art, based on science.", "William Osler"),
            ("To study the phenomena of disease without books is to sail an uncharted sea; to study books without patients is not to go to sea at all.", "William Osler"),
            ("Cure sometimes, treat often, comfort always.", "Ambroise Pare"),
            ("The art of medicine consists of amusing the patient while nature cures the disease.", "Voltaire"),
            ("The best physician is also a philosopher.", "Galen (attributed)"),
            ("In nothing do men more nearly approach the gods than in giving health to men.", "Cicero"),
            ("The dose makes the poison.", "Paracelsus"),
            ("In the fields of observation, chance favors the prepared mind.", "Louis Pasteur"),
            ("Medicine is a social science, and politics is nothing else but medicine on a large scale.", "Rudolf Virchow"),
            ("He who takes medicine and neglects diet wastes the skill of the physician.", "Hippocrates (attributed)"),
            ("To cure a disease after it has taken hold is like digging a well after one is thirsty.", "Chinese proverb"),
            ("A good laugh and a long sleep are the best cures in the doctor's book.", "Irish proverb"),
        ]
        text, author = random.choice(quotes)
        return f'"{text}"<br><span style=\'color:#007bff;\'>- {author}</span>'

    def refresh_dashboard(self):
        """Refresh recent activity from screening records"""
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT patient_id, name, result, confidence
                FROM patient_records
                ORDER BY id DESC
                """
            )
            rows = cur.fetchall()
            conn.close()

            total = len(rows)

            recent_lines = []
            high_attention = 0
            pending_count = 0
            confidence_values = []

            for _, _, result, confidence_text in rows:
                result = str(result or "")
                if self._is_high_attention_result(result):
                    high_attention += 1
                if not result or "pending" in result.lower():
                    pending_count += 1

                conf_value = self._extract_confidence_value(str(confidence_text or ""))
                if conf_value is not None:
                    confidence_values.append(conf_value)

            for patient_id, name, result, _ in rows[:5]:
                pid = str(patient_id or "")
                name = str(name or "")
                result = str(result or "")
                recent_lines.append(f"• {pid} — {name} — {result or 'Pending'}")

            avg_conf = sum(confidence_values) / len(confidence_values) if confidence_values else None

            if hasattr(self, "total_screenings_value"):
                self.total_screenings_value.setText(str(total))
            if hasattr(self, "high_attention_value"):
                self.high_attention_value.setText(str(high_attention))
            if hasattr(self, "high_attention_hint"):
                if high_attention > 0:
                    self.high_attention_hint.setText("Cases flagged for follow-up")
                else:
                    self.high_attention_hint.setText("No high-attention cases detected")
            if hasattr(self, "avg_confidence_value"):
                self.avg_confidence_value.setText(f"{avg_conf:.1f}%" if avg_conf is not None else "—")
            if hasattr(self, "queue_status_label"):
                self.queue_status_label.setText(f"Queue: {pending_count} pending review")
            if hasattr(self, "dashboard_date_label"):
                today = datetime.now().strftime('%A, %B %d, %Y')
                self.dashboard_date_label.setText(f"Today: {today}")
            if hasattr(self, "insight_label"):
                if total == 0:
                    self.insight_label.setText("No screenings yet. Start with a new screening to populate trends.")
                elif high_attention > 0:
                    self.insight_label.setText(f"{high_attention} case(s) require closer follow-up. Prioritize report review.")
                elif pending_count > 0:
                    self.insight_label.setText("Screenings are recorded. Complete pending reviews and finalize outcomes.")
                else:
                    self.insight_label.setText("All recorded screenings appear up-to-date. Continue routine monitoring.")

            if recent_lines:
                self.recent_activity_label.setStyleSheet("color: #495057; font-size: 14px;")
                self.recent_activity_label.setText("\n".join(recent_lines))
            else:
                self.recent_activity_label.setStyleSheet("color: #6c757d; font-size: 14px; font-style: italic;")
                self.recent_activity_label.setText("No recent clinical activity. Ready for patient screenings.")
        except Exception:
            pass

    @staticmethod
    def _is_high_attention_result(result_text):
        text = str(result_text or "").lower()
        keywords = ("moderate", "severe", "proliferative", "refer", "urgent", "dr detected")
        return any(word in text for word in keywords)

    @staticmethod
    def _extract_confidence_value(conf_text):
        text = str(conf_text or "")
        numeric = "".join(ch for ch in text if ch.isdigit() or ch == ".")
        if not numeric:
            return None
        try:
            return float(numeric)
        except ValueError:
            return None

    @staticmethod
    def get_nav_button_style(icon_only=False):
        """Get navigation button stylesheet. If icon_only, use smaller font and center icon."""
        if icon_only:
            return """
                QPushButton {
                    color: #495057;
                    text-align: center;
                    padding: 8px 10px;
                    border: none;
                    border-radius: 8px;
                    font-size: 18px;
                    font-weight: 500;
                    margin: 2px 6px;
                    min-width: 40px;
                    min-height: 40px;
                    background: transparent;
                }
                QPushButton:hover {
                    background: #e9ecef;
                    color: #007bff;
                }
                QPushButton:focus {
                    border: 1px solid #0d6efd;
                }
            """
        else:
            return """
                QPushButton {
                    color: #495057;
                    text-align: left;
                    padding: 15px 20px;
                    border: none;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: 500;
                    margin: 2px 15px;
                    background: transparent;
                }
                QPushButton:hover {
                    background: #e9ecef;
                    color: #007bff;
                }
                QPushButton:focus {
                    border: 1px solid #0d6efd;
                }
            """