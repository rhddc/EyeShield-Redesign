"""
Dashboard module for EyeShield EMR application.
Contains main application window and dashboard functionality.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QGroupBox
)
from PySide6.QtCore import Qt

from screening import ScreeningPage
from patient_records import PatientRecordsPage
from reports import ReportsPage
from users import UsersPage


class EyeShieldApp(QMainWindow):
    """Main application window"""

    def __init__(self, username, role):
        super().__init__()

        self.username = username
        self.role = role

        self.setWindowTitle("EyeShield – DR Screening")
        self.setMinimumSize(1350, 850)

        root = QWidget()
        root_layout = QHBoxLayout(root)

        # Create sidebar
        sidebar = self.create_sidebar()
        root_layout.addWidget(sidebar)

        # Create main content area
        main = QWidget()
        main.setStyleSheet("background: #f8f9fa;")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.pages = QStackedWidget()

        self.dashboard_page = self.create_dashboard_page()
        self.screening_page = ScreeningPage()
        self.patient_records_page = PatientRecordsPage()
        self.reports_page = ReportsPage()
        self.users_page = UsersPage()

        self.pages.addWidget(self.dashboard_page)
        self.pages.addWidget(self.screening_page)
        self.pages.addWidget(self.patient_records_page)
        self.pages.addWidget(self.reports_page)
        self.pages.addWidget(self.users_page)

        main_layout.addWidget(self.pages)

        root_layout.addWidget(main)
        self.setCentralWidget(root)

    def create_sidebar(self):
        """Create sidebar with navigation buttons"""
        sidebar = QWidget()
        sidebar.setFixedWidth(250)
        sidebar.setStyleSheet("""
            QWidget {
                background: #f8f9fa;
                border-right: 1px solid #dee2e6;
            }
        """)

        s = QVBoxLayout(sidebar)
        s.setContentsMargins(0, 20, 0, 20)

        # Title
        title_label = QLabel("EyeShield EMR")
        title_label.setStyleSheet("""
            color: #007bff;
            font-size: 22px;
            font-weight: bold;
            qproperty-alignment: AlignCenter;
            margin-bottom: 10px;
        """)
        s.addWidget(title_label)

        # Navigation buttons
        btn_dash = QPushButton("📊 Dashboard")
        btn_dash.setStyleSheet(self.get_nav_button_style())

        btn_screen = QPushButton("🩺 New Screening")
        btn_screen.setStyleSheet(self.get_nav_button_style())

        btn_pat = QPushButton("📁 Patient Records")
        btn_pat.setStyleSheet(self.get_nav_button_style())

        btn_rep = QPushButton("📄 Reports")
        btn_rep.setStyleSheet(self.get_nav_button_style())

        btn_users = QPushButton("👥 Users")
        btn_users.setStyleSheet(self.get_nav_button_style())

        for b in [btn_dash, btn_screen, btn_pat, btn_rep, btn_users]:
            s.addWidget(b)

        s.addStretch()

        # Connect buttons
        btn_dash.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        btn_screen.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        btn_pat.clicked.connect(lambda: self.pages.setCurrentIndex(2))
        btn_rep.clicked.connect(lambda: self.pages.setCurrentIndex(3))
        btn_users.clicked.connect(lambda: self.pages.setCurrentIndex(4))

        return sidebar

    def create_dashboard_page(self):
        """Create dashboard page"""
        page = QWidget()
        page.setStyleSheet("background: #f8f9fa;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(30)

        # Welcome section
        welcome_widget = QWidget()
        welcome_widget.setStyleSheet("""
            QWidget {
                background: #007bff;
                border-radius: 8px;
                padding: 20px;
            }
        """)
        welcome_layout = QVBoxLayout(welcome_widget)

        welcome_title = QLabel(f"Welcome back, Dr. {self.username}")
        welcome_title.setStyleSheet("""
            color: white;
            font-size: 24px;
            font-weight: bold;
        """)

        welcome_subtitle = QLabel("Electronic Medical Records - Diabetic Retinopathy Screening System")
        welcome_subtitle.setStyleSheet("""
            color: rgba(255, 255, 255, 0.9);
            font-size: 14px;
            margin-top: 5px;
        """)

        welcome_layout.addWidget(welcome_title)
        welcome_layout.addWidget(welcome_subtitle)
        layout.addWidget(welcome_widget)

        # Stats cards
        cards_container = QWidget()
        cards_layout = QHBoxLayout(cards_container)
        cards_layout.setSpacing(20)

        def create_stat_card(title, value):
            card = QWidget()
            card.setStyleSheet("""
                QWidget {
                    background: white;
                    border-radius: 8px;
                    border: 1px solid #dee2e6;
                }
            """)
            card.setFixedSize(200, 100)

            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(20, 15, 20, 15)

            value_label = QLabel(value)
            value_label.setStyleSheet("""
                font-size: 28px;
                font-weight: bold;
                color: #007bff;
                qproperty-alignment: AlignCenter;
            """)

            title_label = QLabel(title)
            title_label.setStyleSheet("""
                font-size: 12px;
                color: #6c757d;
                qproperty-alignment: AlignCenter;
            """)

            card_layout.addWidget(value_label)
            card_layout.addWidget(title_label)

            return card

        # Get stats from patient table
        patient_count = self.patient_records_page.patient_table.rowCount() if hasattr(self, 'patient_records_page') else 0
        screenings_today = patient_count

        cards_layout.addWidget(create_stat_card("Today's Screenings", str(screenings_today)))
        cards_layout.addWidget(create_stat_card("Total Patients", str(patient_count)))
        cards_layout.addWidget(create_stat_card("Images Processed", str(patient_count)))
        cards_layout.addWidget(create_stat_card("DR Positive Cases", "0"))

        layout.addWidget(cards_container)

        # Quick Actions
        actions_group = QGroupBox("Clinical Actions")
        actions_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                color: #007bff;
                border: 1px solid #dee2e6;
                border-radius: 8px;
                margin-top: 10px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 20px;
                padding: 0 10px 0 10px;
            }
        """)

        actions_layout = QHBoxLayout(actions_group)
        actions_layout.setContentsMargins(20, 40, 20, 20)
        actions_layout.setSpacing(15)

        new_screening_btn = QPushButton("🩺 New Patient Screening")
        new_screening_btn.setStyleSheet("""
            QPushButton {
                background: #28a745;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 18px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #218838;
            }
        """)
        new_screening_btn.clicked.connect(lambda: self.pages.setCurrentIndex(1))

        view_patients_btn = QPushButton("📁 Patient Records")
        view_patients_btn.setStyleSheet("""
            QPushButton {
                background: #007bff;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 18px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #0056b3;
            }
        """)
        view_patients_btn.clicked.connect(lambda: self.pages.setCurrentIndex(2))

        actions_layout.addWidget(new_screening_btn)
        actions_layout.addWidget(view_patients_btn)
        actions_layout.addStretch()

        layout.addWidget(actions_group)

        # Recent Activity
        activity_group = QGroupBox("Recent Clinical Activity")
        activity_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                color: #007bff;
                border: 1px solid #dee2e6;
                border-radius: 8px;
                margin-top: 10px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 20px;
                padding: 0 10px 0 10px;
            }
        """)

        activity_layout = QVBoxLayout(activity_group)
        activity_layout.setContentsMargins(20, 40, 20, 20)

        if patient_count > 0:
            recent_label = QLabel(f"Recent screenings: {patient_count} patient records updated")
            recent_label.setStyleSheet("color: #495057; font-size: 14px;")
            activity_layout.addWidget(recent_label)
        else:
            no_activity_label = QLabel("No recent clinical activity. Ready for patient screenings.")
            no_activity_label.setStyleSheet("color: #6c757d; font-size: 14px; font-style: italic;")
            activity_layout.addWidget(no_activity_label)

        layout.addWidget(activity_group)

        layout.addStretch()

        return page

    @staticmethod
    def get_nav_button_style():
        """Get navigation button stylesheet"""
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
        """
