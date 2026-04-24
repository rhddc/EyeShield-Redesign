"""
Dashboard module for EyeShield EMR application.
Contains main application window and dashboard functionality.
"""

import contextlib
import json
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

try:
    import winsound
except Exception:  # pragma: no cover - platform specific
    winsound = None

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QGroupBox, QMessageBox, QProgressBar, QSizePolicy,
    QFrame, QMenu, QInputDialog, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QDialog, QApplication, QLineEdit
)
from PySide6.QtCore import Qt, QSize, QByteArray, QEvent, QTimer, QCoreApplication
from PySide6.QtGui import QIcon, QPixmap, QImage, QPainter, QFont, QShortcut, QKeySequence, QColor, QGuiApplication, QPainterPath
from PySide6.QtSvg import QSvgRenderer

from screening import ScreeningPage
from reports import ReportsPage
from users import UsersPage, ActivityLogPage
from settings import SettingsPage, DARK_STYLESHEET
from help_support import HelpSupportPage
from trusted_hospitals import TrustedHospitalsPage
from camera import CameraPage
from auth import UserManager
from app_paths import PATIENT_RECORDS_DB_PATH
try:
    from db import get_records_conn, ensure_patient_records_db_schema
except Exception:
    from .db import get_records_conn, ensure_patient_records_db_schema


DB_FILE = str(PATIENT_RECORDS_DB_PATH)
from user_auth import get_user_profile
from emr_pages import EmrVisitsPage


class EyeShieldApp(QMainWindow):
    """Main application window"""

    ROLE_PAGE_ACCESS = {
        "admin": {2, 4, 5, 7},
        "clinician": {0, 3, 5, 6, 10},
        "frontdesk": {0, 1, 3, 5, 6, 10},
    }

    # ── Sidebar design tokens ────────────────────────────────────────────────
    _SIDEBAR_W          = 260
    _SIDEBAR_BG_TOP     = "#0A1628"
    _SIDEBAR_BG_BTM     = "#0f2d5e"
    _NAV_ACTIVE_BG      = "rgba(255,255,255,0.13)"
    _NAV_ACTIVE_BORDER  = "rgba(255,255,255,0.28)"
    _NAV_HOVER_BG       = "rgba(255,255,255,0.07)"
    _NAV_TEXT           = "rgba(255,255,255,0.72)"
    _NAV_TEXT_ACTIVE    = "#ffffff"
    _NAV_ICON_INACTIVE  = "#ffffff"
    _NAV_ICON_ACTIVE    = "#ffffff"
    # ────────────────────────────────────────────────────────────────────────

    def __init__(self, username, role, display_name=None, full_name=None, specialization=None, contact=None):
        super().__init__()

        self.username = username
        self.role = role
        self.full_name = str(full_name or "").strip()
        self.display_name = str(display_name or os.environ.get("EYESHIELD_CURRENT_NAME") or username).strip()
        self.specialization = str(specialization or os.environ.get("EYESHIELD_CURRENT_SPECIALIZATION") or "").strip()
        self.contact = str(contact or os.environ.get("EYESHIELD_CURRENT_CONTACT") or "").strip()
        self.display_title = self.specialization if self.role == "clinician" and self.specialization else self.role
        self.allowed_pages = self._allowed_pages_for_role(role)
        self._dark_mode = False
        self._saved_styles = {}
        self._logging_out = False
        self._current_language = "English"
        self._inactivity_timeout_enabled = True
        self._inactivity_timeout_minutes = 15
        self._inactivity_warning_seconds = 30
        self._inactivity_warning_remaining_sec = 0
        self._inactivity_warning_dialog = None
        self._inactivity_warning_text_label = None
        self._inactivity_warning_timer = None
        self._inactivity_warning_active = False
        self._dashboard_clock_timer = None
        self._active_nav_key = ""
        self._svg_icon_cache = {}
        self._last_dashboard_refresh_at = 0.0
        self._last_reports_refresh_at = 0.0
        self._last_activity_log_refresh_at = 0.0

        self.setWindowTitle("EyeShield – DR Screening")
        self.setMinimumSize(900, 560)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            target_width = min(1400, max(1024, int(available.width() * 0.95)))
            target_height = min(860, max(620, int(available.height() * 0.92)))
            self.resize(target_width, target_height)
        else:
            self.resize(1280, 720)
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "eyeshield_icon.svg")
        self._brand_logo_path = self._resolve_existing_path(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "Logo.png"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "logo.png"),
            _icon_path,
        )
        self._brand_title_path = self._resolve_existing_path(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "title.png"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "Title.png"),
        )
        self._app_icon_pixmap = self._load_svg_pixmap(_icon_path, 256)
        self._app_icon = QIcon(self._app_icon_pixmap)
        self.setWindowIcon(self._app_icon)
        icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Left sidebar ─────────────────────────────────────────────────────
        sidebar_w   = self._SIDEBAR_W
        nav_btn_h   = 44
        nav_icon    = QSize(20, 20)

        nav_bar = QWidget()
        nav_bar.setObjectName("appSidebar")
        nav_bar.setFixedWidth(sidebar_w)
        self.nav_bar = nav_bar
        nav_layout = QVBoxLayout(nav_bar)
        nav_layout.setContentsMargins(14, 20, 14, 16)
        nav_layout.setSpacing(0)

        # Apply gradient sidebar background
        nav_bar.setStyleSheet(
            f"QWidget#appSidebar{{"
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"  stop:0 {self._SIDEBAR_BG_TOP}, stop:1 {self._SIDEBAR_BG_BTM});"
            f"border-right: 1px solid rgba(255,255,255,0.06);"
            f"}}"
            f"QLabel{{background:transparent;border:none;}}"
        )

        # ── Brand header ─────────────────────────────────────────────────────
        brand_widget = QWidget()
        brand_widget.setStyleSheet("background: transparent;")
        brand_layout = QHBoxLayout(brand_widget)
        brand_layout.setContentsMargins(4, 0, 4, 0)
        brand_layout.setSpacing(10)

        # App icon (small)
        self._brand_icon_label = QLabel()
        self._brand_icon_label.setFixedSize(32, 32)
        self._brand_icon_label.setAlignment(Qt.AlignCenter)
        self._brand_icon_label.setStyleSheet("background: transparent;")
        brand_pix = self._load_svg_pixmap_colored(_icon_path, "#60a5fa", 64)
        if not brand_pix.isNull():
            self._brand_icon_label.setPixmap(
                brand_pix.scaled(QSize(28, 28), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

        brand_text_col = QVBoxLayout()
        brand_text_col.setSpacing(0)
        brand_text_col.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("EyeShield")
        self.title_label.setStyleSheet(
            "color: #ffffff; font-size: 17px; font-weight: 800;"
            "letter-spacing: 0.4px; font-family: 'Segoe UI Variable','Segoe UI',sans-serif;"
        )
        self._apply_title_label_font(self.title_label)

        subtitle_lbl = QLabel("DR Screening")
        subtitle_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size: 10px; font-weight: 600;"
            "letter-spacing: 1.2px; text-transform: uppercase;"
        )

        brand_text_col.addWidget(self.title_label)
        brand_text_col.addWidget(subtitle_lbl)

        brand_layout.addWidget(self._brand_icon_label)
        brand_layout.addLayout(brand_text_col)
        brand_layout.addStretch()
        nav_layout.addWidget(brand_widget)

        # ── Thin separator ───────────────────────────────────────────────────
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setFixedHeight(1)
        sep1.setStyleSheet("background: rgba(255,255,255,0.08); border: none; margin: 14px 4px 10px 4px;")
        nav_layout.addWidget(sep1)

        # ── Nav buttons ──────────────────────────────────────────────────────
        nav_list = QWidget()
        nav_list.setStyleSheet("background: transparent;")
        nav_list_l = QVBoxLayout(nav_list)
        nav_list_l.setContentsMargins(0, 0, 0, 0)
        nav_list_l.setSpacing(3)

        # Group label helper
        def _add_group_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color: rgba(255,255,255,0.28); font-size: 9px; font-weight: 700;"
                "letter-spacing: 1.4px; text-transform: uppercase;"
                "padding: 10px 8px 4px 8px; background: transparent;"
            )
            nav_list_l.addWidget(lbl)

        navs = [
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "dashboard.svg"), os.path.join(icons_dir, "dasboard.svg")),
                "label": "Dashboard",
                "page_index": 0,
                "group": "MAIN",
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "screening.svg")),
                "label": "Assessment",
                "page_index": 1,
                "group": None,
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "users.svg")),
                "label": "Patient Queue",
                "page_index": 10,
                "nav_key": "emr_visits",
                "group": None,
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "patients.svg")),
                "label": "Patient Records",
                "page_index": 3,
                "group": None,
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "users.svg")),
                "label": "Users",
                "display_label": "Users",
                "page_index": 2,
                "nav_key": "users",
                "group": "ADMIN",
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "activity_log.svg")),
                "label": "Activity Log",
                "display_label": "Activity Log",
                "page_index": 4,
                "group": None,
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "trusted referred hospitals.svg")),
                "label": "Trusted Referrals",
                "display_label": "Trusted Referrals",
                "page_index": 7,
                "nav_key": "trusted_referrals",
                "group": None,
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "urgentcases.svg")),
                "label": "Priority Cases",
                "display_label": "Priority Cases",
                "page_index": 9,
                "nav_key": "priority_cases",
                "group": None,
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "settings.svg")),
                "label": "Settings",
                "page_index": 5,
                "group": "SYSTEM",
            },
            {
                "icon": self._resolve_existing_path(os.path.join(icons_dir, "help.svg")),
                "label": "Help",
                "page_index": 6,
                "group": None,
            },
        ]

        nav_buttons = []
        nav_label_originals = []
        last_group = "__unset__"

        for nav_item in navs:
            if nav_item["page_index"] not in self.allowed_pages:
                continue

            # Group heading
            grp = nav_item.get("group")
            if grp is not None and grp != last_group:
                _add_group_label(grp)
                last_group = grp
            elif grp is None and last_group == "__unset__":
                pass

            btn = QPushButton(f"  {nav_item.get('label', '')}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("pageIndex", nav_item["page_index"])
            btn.setProperty("navKey", nav_item.get("nav_key", nav_item["label"]))
            btn.setProperty("navIconPath", nav_item["icon"])
            btn.setFixedHeight(nav_btn_h)
            btn.setIconSize(nav_icon)
            btn.setStyleSheet(self._nav_btn_stylesheet())

            self._set_button_svg_icon(btn, nav_item["icon"], self._NAV_ICON_INACTIVE, nav_icon)

            nav_list_l.addWidget(btn)
            nav_buttons.append(btn)
            nav_label_originals.append(nav_item["label"])

        nav_list_l.addStretch(1)
        nav_layout.addWidget(nav_list, 1)

        self.nav_buttons = nav_buttons
        self.nav_widgets = []
        self._nav_label_originals = nav_label_originals

        # ── Sidebar bottom: user chip + logout ───────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: rgba(255,255,255,0.08); border: none; margin: 4px 4px 12px 4px;")
        nav_layout.addWidget(sep2)

        user_chip = QWidget()
        user_chip.setStyleSheet(
            "background: rgba(255,255,255,0.07);"
            "border: 1px solid rgba(255,255,255,0.10);"
            "border-radius: 12px;"
        )
        user_chip_l = QHBoxLayout(user_chip)
        user_chip_l.setContentsMargins(10, 8, 10, 8)
        user_chip_l.setSpacing(10)

        # Small avatar in chip
        self._sidebar_avatar_lbl = QLabel()
        self._sidebar_avatar_lbl.setFixedSize(32, 32)
        self._sidebar_avatar_lbl.setAlignment(Qt.AlignCenter)
        self._draw_sidebar_avatar()

        user_text_col = QVBoxLayout()
        user_text_col.setSpacing(0)
        user_text_col.setContentsMargins(0, 0, 0, 0)

        self._sidebar_name_lbl = QLabel(self.display_name or self.username)
        self._sidebar_name_lbl.setStyleSheet(
            "color: #ffffff; font-size: 12px; font-weight: 700;"
            "background: transparent; border: none;"
        )
        self._sidebar_role_lbl = QLabel(self.display_title.capitalize())
        self._sidebar_role_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 10px; font-weight: 600;"
            "background: transparent; border: none;"
        )
        user_text_col.addWidget(self._sidebar_name_lbl)
        user_text_col.addWidget(self._sidebar_role_lbl)

        user_chip_l.addWidget(self._sidebar_avatar_lbl)
        user_chip_l.addLayout(user_text_col)
        user_chip_l.addStretch()

        logout_btn = QPushButton()
        logout_btn.setObjectName("logoutBtn")
        self.logout_btn = logout_btn
        logout_btn.setFixedSize(28, 28)
        logout_btn.setCursor(Qt.PointingHandCursor)
        logout_btn.setToolTip("Log out")
        logout_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,0.15);border:1px solid rgba(255,80,80,0.25);"
            "border-radius:8px;padding:0;}"
            "QPushButton:hover{background:rgba(255,80,80,0.28);border-color:rgba(255,80,80,0.45);}"
            "QPushButton:focus{outline:none;}"
        )
        user_chip_l.addWidget(logout_btn, 0, Qt.AlignVCenter)

        nav_layout.addWidget(user_chip)

        self._logout_icon_path = self._resolve_existing_path(os.path.join(icons_dir, "logout.svg"))
        self._update_logout_icon()
        logout_btn.clicked.connect(self.handle_logout)

        for button in nav_buttons:
            page_index = int(button.property("pageIndex"))
            nav_key = str(button.property("navKey") or "")
            button.clicked.connect(lambda checked=False, idx=page_index, key=nav_key: self._navigate_to(idx, nav_key=key))

        root_layout.addWidget(nav_bar)

        # ── Main content area ────────────────────────────────────────────────
        main = QWidget()
        main.setStyleSheet("background: #f0f4f8;")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.pages = QStackedWidget()

        self.screening_page = ScreeningPage()
        self.screening_page.username = self.username
        self.screening_page.display_name = self.display_name
        self.screening_page.role = self.role
        if hasattr(self.screening_page, "configure_role_permissions"):
            self.screening_page.configure_role_permissions(self.role)
        self.reports_page = ReportsPage(
            self.username, self.role,
            display_name=self.display_name,
            specialization=self.specialization,
        )
        self.reports_page.records_changed_callback = self.refresh_dashboard
        self.users_page = UsersPage()
        self.activity_log_page = ActivityLogPage()
        self.settings_page = SettingsPage()
        self.help_support_page = HelpSupportPage()
        self.trusted_hospitals_page = TrustedHospitalsPage()
        self.reserved_nav_page = QWidget()
        self.priority_cases_page = QWidget()
        self.emr_page = EmrVisitsPage(self)

        self.dashboard_page = self.create_dashboard_page()

        self.users_page.parent_app = self
        self.activity_log_page.parent_app = self

        self.pages.addWidget(self.dashboard_page)         # 0
        self.pages.addWidget(self.screening_page)         # 1
        self.pages.addWidget(self.users_page)             # 2
        self.pages.addWidget(self.reports_page)           # 3
        self.pages.addWidget(self.activity_log_page)      # 4
        self.pages.addWidget(self.settings_page)          # 5
        self.pages.addWidget(self.help_support_page)      # 6
        self.pages.addWidget(self.trusted_hospitals_page) # 7
        self.pages.addWidget(self.reserved_nav_page)      # 8
        self.pages.addWidget(self.priority_cases_page)    # 9
        self.pages.addWidget(self.emr_page)                # 10
        self.pages.currentChanged.connect(self._on_page_changed)

        main_layout.addWidget(self.pages)
        root_layout.addWidget(main, 1)
        self.setCentralWidget(root)

        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.activated.connect(self._global_save_shortcut)

        self.refresh_dashboard()
        default_page_index = self._default_page_index()
        self._active_nav_key = self._default_nav_key_for_page(default_page_index)
        self.pages.setCurrentIndex(default_page_index)
        self._set_active_nav(self.pages.currentIndex())

        self._apply_nav_theme(False)
        self._set_active_nav(self.pages.currentIndex())

        saved_theme = self.settings_page.theme_combo.currentText()
        if saved_theme == "Dark":
            self.apply_theme("Dark")

        saved_lang = self.settings_page.lang_combo.currentText()
        if saved_lang != "English":
            self.apply_language(saved_lang)

        self._setup_inactivity_timeout()
        self._setup_dashboard_clock()

    # ── Sidebar helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _nav_btn_stylesheet() -> str:
        return (
            "QPushButton{"
            "background: transparent;"
            "color: rgba(255,255,255,0.72);"
            "border: 1px solid transparent;"
            "border-radius: 10px;"
            "padding: 0 10px;"
            "font-size: 13px;"
            "font-weight: 600;"
            "font-family: 'Segoe UI Variable','Segoe UI',sans-serif;"
            "text-align: left;"
            "}"
            "QPushButton:hover{"
            "background: rgba(255,255,255,0.07);"
            "color: #ffffff;"
            "border-color: rgba(255,255,255,0.10);"
            "}"
            "QPushButton[active='true']{"
            "background: rgba(255,255,255,0.13);"
            "color: #ffffff;"
            "border: 1px solid rgba(255,255,255,0.22);"
            "}"
            "QPushButton[active='true']:hover{"
            "background: rgba(255,255,255,0.17);"
            "}"
            "QPushButton:disabled{"
            "background: transparent;"
            "color: rgba(255,255,255,0.20);"
            "border-color: transparent;"
            "}"
        )

    def _draw_sidebar_avatar(self):
        """Draw initials-based circular avatar for sidebar user chip."""
        size = 32
        initials = ""
        name = self.display_name or self.username or ""
        parts = name.strip().split()
        if len(parts) >= 2:
            initials = (parts[0][0] + parts[-1][0]).upper()
        elif parts:
            initials = parts[0][:2].upper()
        else:
            initials = "U"

        img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        painter.fillRect(0, 0, size, size, QColor("#3b82f6"))

        font = QFont("Segoe UI Variable", 11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(img.rect(), Qt.AlignCenter, initials)
        painter.end()

        self._sidebar_avatar_lbl.setPixmap(QPixmap.fromImage(img))

    @staticmethod
    def _make_circle_avatar_pixmap(size: int, initials: str, bg_color: str = "#3b82f6") -> QPixmap:
        """Render a circle avatar with initials."""
        img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        painter.fillRect(0, 0, size, size, QColor(bg_color))

        font_size = max(8, size // 3)
        font = QFont("Segoe UI Variable", font_size)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(img.rect(), Qt.AlignCenter, initials)
        painter.end()
        return QPixmap.fromImage(img)

    # ── Role / page helpers ───────────────────────────────────────────────────

    @classmethod
    def _allowed_pages_for_role(cls, role: str) -> set[int]:
        return set(cls.ROLE_PAGE_ACCESS.get(str(role or "").lower(), cls.ROLE_PAGE_ACCESS["frontdesk"]))

    def _is_page_allowed(self, index: int) -> bool:
        return index in self.allowed_pages

    def _default_page_index(self) -> int:
        return 0 if 0 in self.allowed_pages else min(self.allowed_pages)

    # ── SVG / icon helpers ────────────────────────────────────────────────────

    @staticmethod
    def _load_svg_pixmap(svg_path: str, size: int = 64) -> QPixmap:
        renderer = QSvgRenderer(svg_path)
        if not renderer.isValid():
            return QPixmap()
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return QPixmap.fromImage(image)

    @staticmethod
    def _load_svg_pixmap_colored(svg_path: str, color: str, size: int = 64) -> QPixmap:
        try:
            with open(svg_path, "r", encoding="utf-8") as f:
                svg_text = f.read()
        except OSError:
            return QPixmap()

        def _replace_paint(match: re.Match) -> str:
            attr = match.group(1)
            value = match.group(2)
            if value.lower() in {"none", "transparent"}:
                return match.group(0)
            return f'{attr}="{color}"'

        svg_text = re.sub(r'(fill|stroke)=["\']([^"\']+)["\']', _replace_paint, svg_text, flags=re.IGNORECASE)
        data = QByteArray(svg_text.encode("utf-8"))
        renderer = QSvgRenderer(data)
        if not renderer.isValid():
            return QPixmap()
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return QPixmap.fromImage(image)

    @staticmethod
    def _resolve_existing_path(*paths: str) -> str:
        for path in paths:
            if path and os.path.exists(path):
                return path
        return paths[0] if paths else ""

    def _set_button_svg_icon(self, button: QPushButton, svg_path: str, color: str, size: QSize):
        if not svg_path:
            button.setIcon(QIcon())
            button.setProperty("navIconColor", "")
            return
        cache_key = (str(svg_path), str(color), int(size.width()), int(size.height()))
        cached_icon = self._svg_icon_cache.get(cache_key)
        if cached_icon is not None:
            button.setIcon(cached_icon)
            button.setIconSize(size)
            button.setProperty("navIconColor", str(color))
            return
        is_users_icon = os.path.basename(str(svg_path or "")).lower() == "users.svg"
        pixmap = self._load_svg_pixmap_colored(svg_path, color, 256)
        if not self._pixmap_has_visible_pixels(pixmap):
            pixmap = QPixmap()
        if pixmap.isNull():
            base_pixmap = self._load_svg_pixmap(svg_path, 256)
            if not base_pixmap.isNull():
                pixmap = self._tint_pixmap(base_pixmap, color)
                if not self._pixmap_has_visible_pixels(pixmap):
                    pixmap = QPixmap()
            if pixmap.isNull() and is_users_icon:
                pixmap = self._build_users_fallback_pixmap(color, 256)
            if pixmap.isNull():
                icon = QIcon(svg_path)
                self._svg_icon_cache[cache_key] = icon
                button.setIcon(icon)
                button.setIconSize(size)
                button.setProperty("navIconColor", str(color))
                return
        icon = QIcon(pixmap)
        self._svg_icon_cache[cache_key] = icon
        button.setIcon(icon)
        button.setIconSize(size)
        button.setProperty("navIconColor", str(color))

    @staticmethod
    def _pixmap_has_visible_pixels(pixmap: QPixmap, min_alpha: int = 24, min_coverage: float = 0.008) -> bool:
        if pixmap.isNull():
            return False
        image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        width, height = image.width(), image.height()
        if width <= 0 or height <= 0:
            return False
        visible = sum(
            1 for y in range(height) for x in range(width)
            if image.pixelColor(x, y).alpha() >= min_alpha
        )
        return (visible / float(width * height)) >= float(min_coverage)

    @staticmethod
    def _build_users_fallback_pixmap(color: str, size: int = 64) -> QPixmap:
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(int(size * 0.34), int(size * 0.17), int(size * 0.32), int(size * 0.32))
        painter.drawRoundedRect(int(size * 0.20), int(size * 0.52), int(size * 0.60), int(size * 0.28), 12, 12)
        painter.drawEllipse(int(size * 0.10), int(size * 0.27), int(size * 0.22), int(size * 0.22))
        painter.drawRoundedRect(int(size * 0.06), int(size * 0.58), int(size * 0.28), int(size * 0.19), 8, 8)
        painter.end()
        return QPixmap.fromImage(image)

    @staticmethod
    def _tint_pixmap(source: QPixmap, color: str) -> QPixmap:
        if source.isNull():
            return QPixmap()
        tinted = QPixmap(source.size())
        tinted.fill(Qt.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), color)
        painter.end()
        return tinted

    # ── Nav active state ──────────────────────────────────────────────────────

    def _refresh_nav_button_icons(self, active_index: int):
        if not hasattr(self, "nav_buttons"):
            return
        icon_size = QSize(20, 20)
        for btn in self.nav_buttons:
            icon_path = btn.property("navIconPath") or ""
            if not btn.isEnabled():
                color = "rgba(255,255,255,0.20)"
            elif self._is_nav_button_active(btn, active_index):
                color = self._NAV_ICON_ACTIVE
            else:
                color = self._NAV_ICON_INACTIVE
            if str(btn.property("navIconColor") or "") == str(color):
                continue
            self._set_button_svg_icon(btn, icon_path, color, icon_size)

    def _default_nav_key_for_page(self, index: int) -> str:
        if not hasattr(self, "nav_buttons"):
            return ""
        for btn in self.nav_buttons:
            if int(btn.property("pageIndex") or -1) == index:
                return str(btn.property("navKey") or "")
        return ""

    def _is_nav_button_active(self, button: QPushButton, active_index: int) -> bool:
        btn_index = int(button.property("pageIndex") or -1)
        if btn_index != active_index:
            return False
        same_index_buttons = [b for b in self.nav_buttons if int(b.property("pageIndex") or -1) == active_index]
        if len(same_index_buttons) <= 1:
            return True
        active_key = str(getattr(self, "_active_nav_key", "") or "").strip().lower()
        if not active_key:
            return button is same_index_buttons[0]
        return str(button.property("navKey") or "").strip().lower() == active_key

    @staticmethod
    def _apply_title_label_font(label):
        f = QFont("Segoe UI Variable", 14)
        f.setBold(True)
        f.setUnderline(False)
        label.setFont(f)

    @staticmethod
    def _make_nav_font(size: int) -> QFont:
        f = QFont("Segoe UI Variable", size)
        f.setUnderline(False)
        f.setStrikeOut(False)
        return f

    def _update_logout_icon(self):
        if hasattr(self, "logout_btn"):
            self._set_button_svg_icon(
                self.logout_btn,
                getattr(self, "_logout_icon_path", ""),
                "#ff8080",
                QSize(16, 16),
            )

    def _apply_nav_theme(self, dark: bool):
        """Re-apply sidebar styles; called on every theme switch."""
        if not (hasattr(self, "nav_bar") and
                getattr(self.nav_bar, "objectName", lambda: "")() == "appSidebar"):
            return

        nav_bar = self.nav_bar
        nav_bar.setFixedWidth(self._SIDEBAR_W)

        if dark:
            top, btm = "#06111e", "#091e38"
        else:
            top, btm = self._SIDEBAR_BG_TOP, self._SIDEBAR_BG_BTM

        nav_bar.setStyleSheet(
            f"QWidget#appSidebar{{"
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {top},stop:1 {btm});"
            f"border-right: 1px solid rgba(255,255,255,0.05);"
            f"}}"
            f"QLabel{{background:transparent;border:none;}}"
        )

        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet(
                "color:#ffffff;font-size:17px;font-weight:800;letter-spacing:0.4px;"
            )
            self._apply_title_label_font(self.title_label)

        if hasattr(self, "nav_buttons"):
            icon_size = QSize(20, 20)
            for btn in self.nav_buttons:
                btn.setFixedHeight(44)
                btn.setIconSize(icon_size)
                btn.setFont(self._make_nav_font(10))
                btn.setStyleSheet(self._nav_btn_stylesheet())

            active_index = self.pages.currentIndex() if hasattr(self, "pages") else 0
            self._set_active_nav(active_index)

        self._update_logout_icon()

    def _update_nav_icon(self, dark: bool):
        if not hasattr(self, "_brand_icon_label"):
            return
        color = "#93c5fd" if dark else "#60a5fa"
        logo_path = getattr(self, "_brand_logo_path", "")
        icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        icon_path = os.path.join(icons_dir, "eyeshield_icon.svg")
        pix = QPixmap()
        if logo_path and logo_path.lower().endswith(".svg"):
            pix = self._load_svg_pixmap_colored(logo_path, color, 64)
        elif os.path.exists(icon_path):
            pix = self._load_svg_pixmap_colored(icon_path, color, 64)
        if not pix.isNull():
            self._brand_icon_label.setPixmap(
                pix.scaled(QSize(28, 28), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    # ── Navigation locking ────────────────────────────────────────────────────

    def _is_screening_navigation_locked(self) -> bool:
        return bool(
            hasattr(self, "screening_page")
            and hasattr(self.screening_page, "is_navigation_locked")
            and self.screening_page.is_navigation_locked()
        )

    def _refresh_navigation_lock(self):
        if not hasattr(self, "nav_buttons"):
            return
        locked = self._is_screening_navigation_locked()
        for btn in self.nav_buttons:
            page_index = int(btn.property("pageIndex") or -1)
            btn.setEnabled((not locked) or page_index == 1)
        current_index = self.pages.currentIndex() if hasattr(self, "pages") else 0
        self._set_active_nav(current_index)

    def _navigate_to(self, index, show_denied_message=True, nav_key=""):
        if self._is_screening_navigation_locked() and index != 1:
            if show_denied_message:
                QMessageBox.information(
                    self, "Screening In Progress",
                    "Please wait for the image analysis to finish before changing tabs.",
                )
            if hasattr(self, "pages"):
                self.pages.setCurrentIndex(1)
                self._active_nav_key = self._default_nav_key_for_page(1)
                self._set_active_nav(1)
            return
        if not self._is_page_allowed(index):
            if show_denied_message:
                QMessageBox.warning(self, "Access Denied", "Your account role cannot access this page.")
            if hasattr(self, "pages"):
                current_index = int(self.pages.currentIndex())
                self._active_nav_key = self._default_nav_key_for_page(current_index)
                self._set_active_nav(current_index)
            return
        if (
            index == 1
            and hasattr(self, "emr_page")
            and hasattr(self.emr_page, "release_screening_to_main_stack_if_embedded")
        ):
            self.emr_page.release_screening_to_main_stack_if_embedded()
        self._active_nav_key = str(nav_key or self._default_nav_key_for_page(index) or "")
        self.pages.setCurrentIndex(index)
        self._set_active_nav(self.pages.currentIndex())
        # If user clicks the active page again, currentChanged will not fire.
        # Force-refresh key data pages so the click always produces visible content.
        if index == 3 and hasattr(self, "reports_page") and hasattr(self.reports_page, "refresh_report"):
            QTimer.singleShot(0, self.reports_page.refresh_report)
        if index == 4 and hasattr(self, "activity_log_page") and hasattr(self.activity_log_page, "load_activity_log"):
            QTimer.singleShot(0, self.activity_log_page.load_activity_log)
        if index == 10 and hasattr(self, "emr_page") and hasattr(self.emr_page, "refresh"):
            QTimer.singleShot(0, self.emr_page.refresh)
        QTimer.singleShot(0, lambda: self._set_active_nav(self.pages.currentIndex()))

    def _global_save_shortcut(self):
        if not hasattr(self, "pages") or not hasattr(self, "screening_page"):
            return
        if self.pages.currentIndex() != 1:
            return
        if hasattr(self.screening_page, "stacked_widget") and self.screening_page.stacked_widget.currentIndex() == 1:
            if hasattr(self.screening_page, "results_page") and hasattr(self.screening_page.results_page, "save_patient"):
                self.screening_page.results_page.save_patient()

    def _on_page_changed(self, index):
        if self._is_screening_navigation_locked() and index != 1:
            self.pages.setCurrentIndex(1)
            self._active_nav_key = self._default_nav_key_for_page(1)
            self._set_active_nav(1)
            return
        if not self._is_page_allowed(index):
            fallback_index = self._default_page_index()
            self.pages.setCurrentIndex(fallback_index)
            self._active_nav_key = self._default_nav_key_for_page(fallback_index)
            self._set_active_nav(fallback_index)
            return
        self._active_nav_key = self._default_nav_key_for_page(index)
        self._set_active_nav(index)
        now = time.monotonic()
        if index == 3:
            needs_initial_report_load = not bool(getattr(self.reports_page, "_all_result_rows", []))
            if needs_initial_report_load or (now - self._last_reports_refresh_at >= 1.0):
                self._last_reports_refresh_at = now
                QTimer.singleShot(120, self.reports_page.refresh_report)
        if index == 0:
            if now - self._last_dashboard_refresh_at >= 1.0:
                QTimer.singleShot(120, self.refresh_dashboard)
        if index == 4 and hasattr(self, "activity_log_page") and hasattr(self.activity_log_page, "load_activity_log"):
            if now - self._last_activity_log_refresh_at >= 1.0:
                self._last_activity_log_refresh_at = now
                QTimer.singleShot(120, self.activity_log_page.load_activity_log)
        if index == 10 and hasattr(self, "emr_page") and hasattr(self.emr_page, "refresh"):
            QTimer.singleShot(80, self.emr_page.refresh)

    def _set_active_nav(self, index: int):
        if not hasattr(self, "nav_buttons"):
            return
        for btn in self.nav_buttons:
            is_active = self._is_nav_button_active(btn, index)
            was_active = bool(btn.property("active"))
            if was_active == bool(is_active):
                continue
            btn.setProperty("active", bool(is_active))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._refresh_nav_button_icons(index)

    # ── Theme / language ──────────────────────────────────────────────────────

    def apply_theme(self, theme: str):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()

        nav_protected = set()
        if hasattr(self, "nav_bar"):
            nav_protected.add(id(self.nav_bar))
            for w in self.nav_bar.findChildren(QWidget):
                nav_protected.add(id(w))

        def _strip_color_rules(stylesheet: str) -> str:
            color_props = {"color","background","background-color","selection-background-color",
                           "alternate-background-color","gridline-color","border-color"}
            border_like_props = {"border","border-top","border-right","border-bottom","border-left","outline"}
            color_token_pattern = re.compile(
                r"(#(?:[0-9a-fA-F]{3,8})\b|rgba?\([^\)]*\)|hsla?\([^\)]*\))"
            )
            def _rewrite_declaration(match: re.Match) -> str:
                prop, value = match.group("prop"), match.group("value")
                prop_lower = prop.lower()
                if prop_lower in color_props:
                    return ""
                if prop_lower in border_like_props:
                    if color_token_pattern.search(value):
                        stripped_value = re.sub(r"\s+", " ", color_token_pattern.sub("", value)).strip()
                        return "" if not stripped_value else f"{prop}: {stripped_value};"
                return match.group(0)
            return re.compile(r"(?P<prop>[a-zA-Z\-]+)\s*:\s*(?P<value>[^;{}]+)\s*;").sub(
                _rewrite_declaration, stylesheet
            )

        if theme == "Dark":
            if self._dark_mode:
                return
            self._dark_mode = True
            self._apply_nav_theme(True)
            self._saved_styles = {}
            for widget in self.findChildren(QWidget):
                if id(widget) in nav_protected:
                    continue
                if ss := widget.styleSheet():
                    self._saved_styles[id(widget)] = (widget, ss)
                    widget.setStyleSheet(_strip_color_rules(ss))
            app.setStyleSheet(DARK_STYLESHEET)
            self._apply_nav_theme(True)
        else:
            if not self._dark_mode:
                return
            self._dark_mode = False
            self._apply_nav_theme(False)
            app.setStyleSheet("")
            for _, (widget, ss) in self._saved_styles.items():
                with contextlib.suppress(RuntimeError):
                    widget.setStyleSheet(ss)
            self._saved_styles = {}
            self._apply_nav_theme(False)

        if hasattr(self, "nav_bar"):
            self.nav_bar.updateGeometry()
            self.nav_bar.update()

        self._set_active_nav(self.pages.currentIndex())

        if hasattr(self, "screening_page") and hasattr(self.screening_page, "apply_theme"):
            self.screening_page.apply_theme(theme)

        self._update_nav_icon(self._dark_mode)
        self.refresh_dashboard()

    def apply_language(self, language: str):
        from translations import get_pack
        self._current_language = language
        pack = get_pack(language)

        _nav_key_map = {
            "Dashboard": "nav_dashboard",
            "Screening": "nav_screening",
            "Camera": "nav_camera",
            "Reports": "nav_reports",
            "Users": "nav_users",
            "Activity Log": "usr_log",
            "Settings": "nav_settings",
            "Help": "nav_help",
        }
        if hasattr(self, "nav_labels") and hasattr(self, "_nav_label_originals"):
            for label, orig in zip(self.nav_labels, self._nav_label_originals):
                key = _nav_key_map.get(orig, "")
                if key:
                    label.setText(pack.get(key, orig))

        if hasattr(self, "welcome_label"):
            self.welcome_label.setText(f"{pack['dash_welcome']}, {self.display_name}")

        if hasattr(self, "_dash_severity_title_lbl"):
            self._dash_severity_title_lbl.setText("SCREENED PATIENTS")
        if hasattr(self, "_dash_recent_title_lbl"):
            self._dash_recent_title_lbl.setText(pack.get("dash_recent", "RECENT SCREENINGS"))

        kpi_map = {"kpiTotal": "dash_kpi_total"}
        for obj_name, key in kpi_map.items():
            title_w = self.findChild(QLabel, f"{obj_name}_title")
            if title_w:
                title_w.setText(pack[key])

        for page in (self.screening_page, self.reports_page, self.users_page,
                     self.activity_log_page, self.help_support_page):
            if hasattr(page, "apply_language"):
                page.apply_language(language)

        self.refresh_dashboard()

    # ── Window events ─────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if getattr(self, '_logging_out', False):
            event.accept()
            return
        if self._confirm_quit_during_screening():
            event.ignore()
            return
        reply = QMessageBox.question(
            self, "Quit EyeShield", "Are you sure you want to quit EyeShield?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        event.accept() if reply == QMessageBox.StandardButton.Yes else event.ignore()

    def handle_logout(self):
        if self._confirm_quit_during_screening():
            return
        reply = QMessageBox.question(
            self, "Logout", "Are you sure you want to log out?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import user_store
            user_store.log_activity_event(self.username, "LOGOUT",
                                          metadata={"source": "manual"}, action_text="Logout")
        except Exception:
            pass
        from login import LoginWindow
        self._logging_out = True
        self.login = LoginWindow()
        self.login.show()
        self.close()

    def _is_screening_ongoing_context(self) -> bool:
        if not hasattr(self, "screening_page"):
            return False
        page = self.screening_page
        on_screening_tab = bool(hasattr(self, "pages") and self.pages.currentIndex() == 1)
        on_results_screen = bool(on_screening_tab and hasattr(page, "stacked_widget")
                                 and page.stacked_widget.currentIndex() == 1)
        has_unsaved = bool(hasattr(page, "has_unsaved_result") and page.has_unsaved_result())
        analysis_busy = bool(hasattr(page, "is_navigation_locked") and page.is_navigation_locked())
        return bool(on_results_screen or has_unsaved or analysis_busy)

    def _confirm_quit_during_screening(self) -> bool:
        if not self._is_screening_ongoing_context():
            return False
        box = QMessageBox(self)
        box.setWindowTitle("Screening Ongoing")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Screening is on going. Do you really want to quit application?")
        no_btn = box.addButton("No, back to screening", QMessageBox.ButtonRole.RejectRole)
        yes_btn = box.addButton("Yes", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(no_btn)
        box.exec()
        if box.clickedButton() == yes_btn:
            return False
        if hasattr(self, "pages"):
            self.pages.setCurrentIndex(1)
            self._set_active_nav(1)
        return True

    # ── Inactivity timeout ────────────────────────────────────────────────────

    def _setup_inactivity_timeout(self):
        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.setSingleShot(True)
        self._inactivity_timer.timeout.connect(self._on_inactivity_timeout)

        self._inactivity_warning_timer = QTimer(self)
        self._inactivity_warning_timer.setInterval(1000)
        self._inactivity_warning_timer.timeout.connect(self._tick_inactivity_warning)

        runtime_enabled = bool(self.settings_page.auto_logout_check.isChecked())
        runtime_minutes = int(self.settings_page.timeout_spin.value())
        runtime_warning_seconds = (
            int(self.settings_page.warning_spin.value())
            if hasattr(self.settings_page, "warning_spin") else 30
        )
        if hasattr(self.settings_page, "get_runtime_inactivity_settings"):
            runtime_enabled, runtime_minutes, runtime_warning_seconds = (
                self.settings_page.get_runtime_inactivity_settings()
            )

        self.apply_inactivity_settings(runtime_enabled, runtime_minutes, runtime_warning_seconds)
        app = QGuiApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def apply_inactivity_settings(self, enabled: bool, timeout_minutes: int, warning_seconds: int = 30):
        self._inactivity_timeout_enabled = bool(enabled)
        self._inactivity_timeout_minutes = max(1, int(timeout_minutes or 1))
        self._inactivity_warning_seconds = max(10, min(300, int(warning_seconds or 30)))
        self._dismiss_inactivity_warning(restart_timer=False)
        if self._inactivity_timeout_enabled:
            self._restart_inactivity_timer()
        elif hasattr(self, "_inactivity_timer"):
            self._inactivity_timer.stop()

    def _restart_inactivity_timer(self):
        if not hasattr(self, "_inactivity_timer") or not self._inactivity_timeout_enabled:
            return
        self._inactivity_timer.start(self._inactivity_timeout_minutes * 60 * 1000)

    def _on_inactivity_timeout(self):
        if not self._inactivity_timeout_enabled:
            return
        if not self.isVisible():
            self._restart_inactivity_timer()
            return
        self._show_inactivity_warning()

    def _show_inactivity_warning(self):
        if self._inactivity_warning_active:
            return
        self._inactivity_warning_active = True
        self._inactivity_warning_remaining_sec = max(10, int(self._inactivity_warning_seconds or 30))

        box = QDialog(self)
        box.setObjectName("inactivityWarningToast")
        box.setWindowTitle("Inactivity Warning")
        box.setModal(False)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        box.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        box.setStyleSheet("""
            QDialog#inactivityWarningToast{background:#fff4e5;border:1px solid #f59e0b;border-radius:12px;}
            QLabel#warningTitle{color:#92400e;font-size:14px;font-weight:700;}
            QLabel#warningBody{color:#7c2d12;font-size:12px;font-weight:500;}
            QPushButton#warningStayBtn{background:#ffffff;border:1px solid #f59e0b;border-radius:8px;
                color:#92400e;padding:6px 12px;font-size:12px;font-weight:600;}
            QPushButton#warningStayBtn:hover{background:#fff7ed;}
            QPushButton#warningLogoutBtn{background:#f59e0b;border:1px solid #d97706;border-radius:8px;
                color:#ffffff;padding:6px 12px;font-size:12px;font-weight:600;}
            QPushButton#warningLogoutBtn:hover{background:#d97706;}
        """)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        title = QLabel("Inactivity warning")
        title.setObjectName("warningTitle")
        layout.addWidget(title)
        self._inactivity_warning_text_label = QLabel()
        self._inactivity_warning_text_label.setObjectName("warningBody")
        self._inactivity_warning_text_label.setWordWrap(True)
        layout.addWidget(self._inactivity_warning_text_label)
        actions = QHBoxLayout()
        actions.addStretch()
        stay_btn = QPushButton("Stay Signed In")
        stay_btn.setObjectName("warningStayBtn")
        logout_btn_w = QPushButton("Log Out Now")
        logout_btn_w.setObjectName("warningLogoutBtn")
        actions.addWidget(stay_btn)
        actions.addWidget(logout_btn_w)
        layout.addLayout(actions)
        stay_btn.clicked.connect(self._continue_session_after_warning)
        logout_btn_w.clicked.connect(self._trigger_auto_logout)
        box.destroyed.connect(lambda *_: self._clear_warning_dialog_reference())

        self._inactivity_warning_dialog = box
        self._refresh_inactivity_warning_text()
        self._play_inactivity_alert_sound(urgent=False)
        if self._inactivity_warning_timer is not None:
            self._inactivity_warning_timer.start()
        box.adjustSize()
        self._position_inactivity_warning()
        box.show()

    def _clear_warning_dialog_reference(self):
        self._inactivity_warning_dialog = None
        self._inactivity_warning_text_label = None

    def _position_inactivity_warning(self):
        if self._inactivity_warning_dialog is None:
            return
        anchor = self.geometry().topRight()
        margin = 16
        x = anchor.x() - self._inactivity_warning_dialog.width() - margin
        y = self.geometry().top() + margin
        self._inactivity_warning_dialog.move(max(0, x), max(0, y))

    def _play_inactivity_alert_sound(self, urgent: bool = False):
        played = False
        if winsound is not None:
            try:
                tone = winsound.MB_ICONHAND if urgent else winsound.MB_ICONEXCLAMATION
                winsound.MessageBeep(tone)
                played = True
            except Exception:
                played = False
        if not played:
            with contextlib.suppress(Exception):
                QApplication.beep()

    def _refresh_inactivity_warning_text(self):
        if self._inactivity_warning_dialog is None or self._inactivity_warning_text_label is None:
            return
        mins = self._inactivity_warning_remaining_sec // 60
        secs = self._inactivity_warning_remaining_sec % 60
        self._inactivity_warning_text_label.setText(
            f"No activity detected. Automatic logout in {mins:02d}:{secs:02d}."
        )

    def _tick_inactivity_warning(self):
        if not self._inactivity_warning_active:
            if self._inactivity_warning_timer is not None:
                self._inactivity_warning_timer.stop()
            return
        self._inactivity_warning_remaining_sec = max(0, self._inactivity_warning_remaining_sec - 1)
        self._refresh_inactivity_warning_text()
        if self._inactivity_warning_remaining_sec in {20, 10, 5, 4, 3, 2, 1}:
            self._play_inactivity_alert_sound(urgent=self._inactivity_warning_remaining_sec <= 5)
        if self._inactivity_warning_remaining_sec <= 0:
            self._trigger_auto_logout()

    def _dismiss_inactivity_warning(self, restart_timer: bool = True):
        if self._inactivity_warning_timer is not None:
            self._inactivity_warning_timer.stop()
        self._inactivity_warning_active = False
        self._inactivity_warning_remaining_sec = 0
        if self._inactivity_warning_dialog is not None:
            self._inactivity_warning_dialog.close()
            self._inactivity_warning_dialog = None
        self._inactivity_warning_text_label = None
        if restart_timer:
            self._restart_inactivity_timer()

    def _continue_session_after_warning(self):
        self._dismiss_inactivity_warning(restart_timer=True)

    def _trigger_auto_logout(self):
        self._dismiss_inactivity_warning(restart_timer=False)
        try:
            import user_store
            user_store.log_activity_event(self.username, "LOGOUT",
                                          metadata={"source": "inactivity_timeout"},
                                          action_text="Logout (Inactivity Timeout)")
        except Exception:
            pass
        self._logging_out = True
        from login import LoginWindow
        self.login = LoginWindow()
        self.login.show()
        self.close()

    def eventFilter(self, watched, event):
        if hasattr(self, "_inactivity_timer") and self._inactivity_timeout_enabled:
            if self._inactivity_warning_active:
                return super().eventFilter(watched, event)
            reset_types = {
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove,
                QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
                QEvent.Type.Wheel, QEvent.Type.TouchBegin,
                QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd,
            }
            if event.type() in reset_types and self.isVisible():
                self._restart_inactivity_timer()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_inactivity_warning()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._position_inactivity_warning()

    # ── Dashboard clock ───────────────────────────────────────────────────────

    @staticmethod
    def _format_dashboard_datetime(now: datetime) -> str:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(EyeShieldApp._ph_now().tzinfo)
        hour = now.strftime("%I").lstrip("0") or "0"
        return f"{now.strftime('%A, %B %d, %Y')}  ·  {hour}:{now.strftime('%M')} {now.strftime('%p').lower()}"

    @staticmethod
    def _ph_now() -> datetime:
        if ZoneInfo is not None:
            try:
                return datetime.now(ZoneInfo("Asia/Manila"))
            except Exception:
                pass
        return datetime.now(timezone(timedelta(hours=8)))

    def _update_dashboard_datetime_label(self):
        if hasattr(self, "dashboard_date_label"):
            self.dashboard_date_label.setText(self._format_dashboard_datetime(self._ph_now()))

    def _setup_dashboard_clock(self):
        self._dashboard_clock_timer = QTimer(self)
        self._dashboard_clock_timer.setInterval(1000)
        self._dashboard_clock_timer.timeout.connect(self._update_dashboard_datetime_label)
        self._dashboard_clock_timer.start()
        self._update_dashboard_datetime_label()

    # ── Dashboard page ────────────────────────────────────────────────────────

    def create_dashboard_page(self):
        """Build the modernised dashboard layout."""
        page = QWidget()
        page.setObjectName("dashboardPage")
        page.setStyleSheet("QWidget#dashboardPage { background: #f0f4f8; }")

        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)

        # ── Hero card ─────────────────────────────────────────────────────────
        hero = QWidget()
        hero.setObjectName("heroCard")
        hero.setMinimumHeight(120)
        hero_h = QHBoxLayout(hero)
        hero_h.setContentsMargins(28, 22, 28, 22)
        hero_h.setSpacing(0)

        # Left stack: greeting / role / date
        left_stack = QVBoxLayout()
        left_stack.setSpacing(3)
        left_stack.setContentsMargins(0, 0, 0, 0)

        self.welcome_label = QLabel(f"Welcome back, {self.display_name}!")
        self.welcome_label.setObjectName("welcomeGreeting")
        self.welcome_label.setStyleSheet(
            "color: #0f172a; font-size: 26px; font-weight: 800;"
            "font-family: 'Segoe UI Variable','Segoe UI',sans-serif; background: transparent;"
        )

        self.welcome_role_label = QLabel(self.display_title.capitalize())
        self.welcome_role_label.setObjectName("welcomeRole")
        self.welcome_role_label.setStyleSheet(
            "color: #64748b; font-size: 13px; font-weight: 600; background: transparent;"
        )

        self.dashboard_date_label = QLabel("")
        self.dashboard_date_label.setObjectName("dashDate")
        self.dashboard_date_label.setStyleSheet(
            "color: #3b82f6; font-size: 12px; font-weight: 600; background: transparent;"
        )

        left_stack.addWidget(self.welcome_label)
        left_stack.addWidget(self.welcome_role_label)
        left_stack.addSpacing(4)
        left_stack.addWidget(self.dashboard_date_label)
        left_stack.addStretch()

        # Right: circular avatar
        initials = ""
        parts = (self.display_name or self.username or "").strip().split()
        if len(parts) >= 2:
            initials = (parts[0][0] + parts[-1][0]).upper()
        elif parts:
            initials = parts[0][:2].upper()
        else:
            initials = "U"

        avatar_size = 56
        avatar_lbl = QLabel()
        avatar_lbl.setObjectName("heroAvatar")
        avatar_lbl.setFixedSize(avatar_size, avatar_size)
        avatar_lbl.setAlignment(Qt.AlignCenter)
        avatar_lbl.setToolTip(self.display_name or self.username)
        avatar_pix = self._make_circle_avatar_pixmap(avatar_size * 2, initials, "#3b82f6")
        avatar_lbl.setPixmap(
            avatar_pix.scaled(QSize(avatar_size, avatar_size), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self._hero_avatar_lbl = avatar_lbl

        hero_h.addLayout(left_stack, 1)
        hero_h.addWidget(avatar_lbl, 0, Qt.AlignVCenter)

        outer.addWidget(hero)

        # DB health banner (hidden unless DB errors)
        self._dash_db_error_banner = QLabel("")
        self._dash_db_error_banner.setWordWrap(True)
        self._dash_db_error_banner.setVisible(False)
        self._dash_db_error_banner.setStyleSheet(
            "background:#fff7ed;border:1px solid #fdba74;color:#9a3412;"
            "border-radius:12px;padding:10px 12px;font-size:12px;font-weight:600;"
        )
        outer.addWidget(self._dash_db_error_banner)

        # ── KPI row ───────────────────────────────────────────────────────────
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(16)

        def make_kpi(obj_name, title_text, accent):
            card = QWidget()
            card.setObjectName(obj_name)
            card.setMinimumHeight(92)
            card.setStyleSheet(
                f"QWidget#{obj_name}{{"
                f"background: #ffffff;"
                f"border-radius: 14px;"
                f"border-left: 4px solid {accent};"
                f"border-top: 1px solid #e2e8f0;"
                f"border-right: 1px solid #e2e8f0;"
                f"border-bottom: 1px solid #e2e8f0;"
                f"}}"
            )
            v = QVBoxLayout(card)
            v.setContentsMargins(16, 12, 16, 12)
            v.setSpacing(4)
            t = QLabel(title_text)
            t.setObjectName(f"{obj_name}_title")
            t.setStyleSheet(
                "color: #94a3b8; font-size: 10px; font-weight: 700;"
                "letter-spacing: 0.8px; text-transform: uppercase; background: transparent;"
            )
            val = QLabel("—")
            val.setObjectName(f"{obj_name}_value")
            val.setStyleSheet(
                f"font-size: 28px; font-weight: 800; color: #0f172a; background: transparent;"
            )
            v.addWidget(t)
            v.addWidget(val)
            v.addStretch()
            return card, val

        card_total,    self.total_screenings_value = make_kpi("kpiTotal",    "TOTAL SCREENINGS", "#3b82f6")
        card_patients, self.unique_patients_value  = make_kpi("kpiPatients", "NO DR CASES",      "#22c55e")
        card_abnormal, self.abnormal_cases_value   = make_kpi("kpiAbnormal", "ABNORMAL CASES",   "#f59e0b")
        card_high,     self.high_risk_cases_value  = make_kpi("kpiHighRisk", "HIGH RISK CASES",  "#ef4444")

        for c in (card_total, card_patients, card_abnormal, card_high):
            kpi_row.addWidget(c, 1)
        outer.addLayout(kpi_row)

        # ── Content row ───────────────────────────────────────────────────────
        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        # ── Left: severity bar chart ──────────────────────────────────────────
        severity_card = QWidget()
        severity_card.setObjectName("severityCard")
        severity_card.setMinimumHeight(380)
        severity_card.setStyleSheet(
            "QWidget#severityCard{background:#ffffff;border-radius:16px;"
            "border:1px solid #e2e8f0;}"
        )
        sev_v = QVBoxLayout(severity_card)
        sev_v.setContentsMargins(20, 16, 20, 18)
        sev_v.setSpacing(0)

        sev_header = QHBoxLayout()
        sev_header.setContentsMargins(0, 0, 0, 0)
        self._dash_severity_title_lbl = QLabel("SCREENED PATIENTS")
        self._dash_severity_title_lbl.setStyleSheet(
            "color:#64748b;font-size:10px;font-weight:800;"
            "letter-spacing:1.0px;background:transparent;"
        )
        sev_header.addWidget(self._dash_severity_title_lbl)
        sev_header.addStretch()
        sev_v.addLayout(sev_header)

        sev_divider = QFrame()
        sev_divider.setFrameShape(QFrame.HLine)
        sev_divider.setFixedHeight(1)
        sev_divider.setStyleSheet("background: #f1f5f9; border: none; margin: 10px 0 12px 0;")
        sev_v.addWidget(sev_divider)

        self.severity_bars = {}
        self._severity_order = ["No DR", "Mild DR", "Moderate DR", "Severe DR", "Proliferative DR"]

        for level in self._severity_order:
            row_w = QWidget()
            row_w.setMinimumHeight(46)
            row_w.setStyleSheet("background: transparent;")
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 4, 0, 4)
            row_l.setSpacing(12)

            lbl = QLabel(level)
            lbl.setFixedWidth(130)
            lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #475569; background: transparent;")

            bar = QProgressBar()
            bar.setMinimum(0)
            bar.setMaximum(100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            count_lbl = QLabel("0")
            count_lbl.setFixedWidth(40)
            count_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_lbl.setStyleSheet(
                "font-size: 14px; font-weight: 800; color: #0f172a; background: transparent;"
            )

            row_l.addWidget(lbl)
            row_l.addWidget(bar)
            row_l.addWidget(count_lbl)
            sev_v.addWidget(row_w, 1)
            self.severity_bars[level] = (bar, count_lbl)

        sev_v.addStretch(1)
        content_row.addWidget(severity_card, 6)

        # ── Right sidebar ─────────────────────────────────────────────────────
        sidebar_w = QWidget()
        sidebar_w.setObjectName("dashSidebar")
        sidebar_w.setStyleSheet("QWidget#dashSidebar{background:transparent;}")
        sb_v = QVBoxLayout(sidebar_w)
        sb_v.setContentsMargins(0, 0, 0, 0)
        sb_v.setSpacing(14)

        # Quick actions
        qa_card = QWidget()
        qa_card.setObjectName("quickActionsCard")
        qa_card.setStyleSheet(
            "QWidget#quickActionsCard{background:#ffffff;border:1px solid #e2e8f0;border-radius:16px;}"
        )
        qa_v = QVBoxLayout(qa_card)
        qa_v.setContentsMargins(16, 14, 16, 16)
        qa_v.setSpacing(10)

        qa_title = QLabel("QUICK ACTIONS")
        qa_title.setStyleSheet(
            "color:#94a3b8;font-size:10px;font-weight:800;letter-spacing:1.0px;background:transparent;"
        )
        qa_v.addWidget(qa_title)

        def _primary_btn(text, slot):
            b = QPushButton(text)
            b.setMinimumHeight(42)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #2563eb,stop:1 #3b82f6);"
                "color:#fff;border:none;border-radius:10px;"
                "padding:0 14px;text-align:left;font-weight:700;font-size:13px;}"
                "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #1d4ed8,stop:1 #2563eb);}"
                "QPushButton:pressed{background:#1e40af;}"
            )
            b.clicked.connect(slot)
            return b

        def _ghost_btn(text, slot):
            b = QPushButton(text)
            b.setMinimumHeight(40)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
                "border-radius:10px;padding:0 14px;text-align:left;font-weight:600;font-size:13px;}"
                "QPushButton:hover{background:#f1f5f9;border-color:#cbd5e1;}"
                "QPushButton:pressed{background:#e2e8f0;}"
            )
            b.clicked.connect(slot)
            return b

        role_l = str(getattr(self, "role", "") or "").strip().lower()
        if role_l in {"frontdesk"}:
            qa_v.addWidget(_primary_btn("➕  New Patient / Visit",
                                        lambda: self._navigate_to(1, nav_key="Screening")))
        else:
            qa_v.addWidget(_primary_btn("➕  New Screening",
                                        lambda: self._navigate_to(1, nav_key="Screening")))
        qa_v.addWidget(_ghost_btn("📊  View Reports",
                                   lambda: self._navigate_to(3, nav_key="Reports")))
        sb_v.addWidget(qa_card)

        # Availability
        av_card = QWidget()
        av_card.setObjectName("availabilityCard")
        av_v = QVBoxLayout(av_card)
        av_v.setContentsMargins(16, 14, 16, 14)
        av_v.setSpacing(6)

        self._dash_availability_title_lbl = QLabel("MY AVAILABILITY")
        self._dash_availability_title_lbl.setStyleSheet(
            "color:#94a3b8;font-size:10px;font-weight:800;letter-spacing:1.0px;background:transparent;"
        )
        av_v.addWidget(self._dash_availability_title_lbl)

        self.availability_days_label = QLabel("Days: Not set")
        self.availability_days_label.setWordWrap(True)
        av_v.addWidget(self.availability_days_label)

        self.availability_time_label = QLabel("Hours: Not set")
        self.availability_time_label.setWordWrap(True)
        av_v.addWidget(self.availability_time_label)

        self.availability_updated_label = QLabel("Update from Users > Edit Availability")
        self.availability_updated_label.setWordWrap(True)
        av_v.addWidget(self.availability_updated_label)

        sb_v.addWidget(av_card)

        # Recent screenings
        rec_card = QWidget()
        rec_card.setObjectName("recentCard")
        rec_v = QVBoxLayout(rec_card)
        rec_v.setContentsMargins(16, 14, 16, 14)
        rec_v.setSpacing(10)

        self._dash_recent_title_lbl = QLabel("RECENT SCREENINGS")
        self._dash_recent_title_lbl.setStyleSheet(
            "color:#94a3b8;font-size:10px;font-weight:800;letter-spacing:1.0px;background:transparent;"
        )
        rec_v.addWidget(self._dash_recent_title_lbl)

        self.recent_list_layout = QVBoxLayout()
        self.recent_list_layout.setSpacing(6)
        rec_v.addLayout(self.recent_list_layout)
        rec_v.addStretch(1)
        sb_v.addWidget(rec_card, 1)

        content_row.addWidget(sidebar_w, 4)
        outer.addLayout(content_row, 1)

        return page

    # ── Dashboard refresh ─────────────────────────────────────────────────────

    def refresh_dashboard(self):
        """Refresh all dashboard widgets with current data and theme colours."""
        self._last_dashboard_refresh_at = time.monotonic()
        dark = getattr(self, "_dark_mode", False)

        if dark:
            bg_page       = "#070f1a"
            card_bg       = "#0f1e30"
            text_primary  = "#e8f0f9"
            text_secondary = "#8ba5c0"
            text_muted    = "#5f7a94"
            accent_blue   = "#60a5fa"
            sev_green     = "#4ade80"
            hero_bg       = "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0d1f35,stop:1 #0a1828)"
            border_color  = "rgba(255,255,255,0.06)"
        else:
            bg_page       = "#f0f4f8"
            card_bg       = "#ffffff"
            text_primary  = "#0f172a"
            text_secondary = "#64748b"
            text_muted    = "#94a3b8"
            accent_blue   = "#3b82f6"
            sev_green     = "#22c55e"
            hero_bg       = "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #eff6ff,stop:1 #e0f2fe)"
            border_color  = "#e2e8f0"

        severity_colors = {
            "No DR":           sev_green,
            "Mild DR":         "#60a5fa" if dark else "#3b82f6",
            "Moderate DR":     "#fcd34d" if dark else "#f59e0b",
            "Severe DR":       "#fb923c" if dark else "#ea580c",
            "Proliferative DR":"#f87171" if dark else "#ef4444",
        }

        # Page background
        if hasattr(self, "dashboard_page"):
            self.dashboard_page.setStyleSheet(
                f"QWidget#dashboardPage{{background:{bg_page};}}"
            )

        # Hero card
        hero_card = self.findChild(QWidget, "heroCard")
        if hero_card:
            hero_card.setStyleSheet(
                f"QWidget#heroCard{{background:{hero_bg};"
                f"border-radius:18px;border:1px solid {border_color};}}"
            )

        if hasattr(self, "welcome_label"):
            self.welcome_label.setStyleSheet(
                f"color:{text_primary};font-size:26px;font-weight:800;"
                "font-family:'Segoe UI Variable','Segoe UI',sans-serif;background:transparent;"
            )
        if hasattr(self, "welcome_role_label"):
            self.welcome_role_label.setStyleSheet(
                f"color:{text_secondary};font-size:13px;font-weight:600;background:transparent;"
            )
        if hasattr(self, "dashboard_date_label"):
            self._update_dashboard_datetime_label()
            self.dashboard_date_label.setStyleSheet(
                f"color:{accent_blue};font-size:12px;font-weight:600;background:transparent;"
            )

        # Fetch data
        rows = []
        self._dash_db_error_text = ""
        try:
            conn = get_records_conn()
            ensure_patient_records_db_schema(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT patient_id, name, result, confidence "
                "FROM patient_records WHERE archived_at IS NULL ORDER BY id DESC"
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as err:
            self._dash_db_error_text = str(err)
            rows = []
        total = len(rows)

        # Surface DB errors (if any)
        if hasattr(self, "_dash_db_error_banner"):
            err = str(getattr(self, "_dash_db_error_text", "") or "").strip()
            if err:
                self._dash_db_error_banner.setText(f"Patient records database error: {err}")
                self._dash_db_error_banner.setVisible(True)
            else:
                self._dash_db_error_banner.setVisible(False)

        # Counts
        no_dr_count = abnormal_count = high_risk_count = 0
        for _, _, result, _ in rows:
            level = self._normalize_severity_label(result)
            if level == "No DR":
                no_dr_count += 1
            else:
                abnormal_count += 1
                if level in ("Severe DR", "Proliferative DR"):
                    high_risk_count += 1

        # KPI cards
        kpi_title_style = (
            f"color:{text_secondary};font-size:10px;font-weight:700;"
            "letter-spacing:0.8px;text-transform:uppercase;background:transparent;"
        )
        kpi_value_style = (
            f"font-size:28px;font-weight:800;color:{text_primary};background:transparent;"
        )

        def style_kpi(obj_name, accent, val_widget, val_text):
            card = self.findChild(QWidget, obj_name)
            if card:
                card.setStyleSheet(
                    f"QWidget#{obj_name}{{background:{card_bg};"
                    f"border-radius:14px;border-left:4px solid {accent};"
                    f"border-top:1px solid {border_color};"
                    f"border-right:1px solid {border_color};"
                    f"border-bottom:1px solid {border_color};}}"
                )
            t = self.findChild(QLabel, f"{obj_name}_title")
            if t:
                t.setStyleSheet(kpi_title_style)
            if val_widget:
                val_widget.setStyleSheet(kpi_value_style)
                val_widget.setText(val_text)

        style_kpi("kpiTotal",    accent_blue, self.total_screenings_value, str(total))
        style_kpi("kpiPatients", sev_green,   self.unique_patients_value,  str(no_dr_count))
        style_kpi("kpiAbnormal", "#f59e0b",   self.abnormal_cases_value,   str(abnormal_count))
        style_kpi("kpiHighRisk", "#ef4444",   self.high_risk_cases_value,  str(high_risk_count))

        # Severity card
        sev_card = self.findChild(QWidget, "severityCard")
        if sev_card:
            sev_card.setStyleSheet(
                f"QWidget#severityCard{{background:{card_bg};border-radius:16px;"
                f"border:1px solid {border_color};}}"
            )
        if hasattr(self, "_dash_severity_title_lbl"):
            self._dash_severity_title_lbl.setStyleSheet(
                f"color:{text_secondary};font-size:10px;font-weight:800;"
                "letter-spacing:1.0px;background:transparent;"
            )

        severity_counts = {level: 0 for level in getattr(self, "_severity_order", [])}
        for _, _, result, _ in rows:
            level = self._normalize_severity_label(result)
            if level in severity_counts:
                severity_counts[level] += 1

        if hasattr(self, "severity_bars"):
            total_sev = sum(severity_counts.values()) or 1
            for level in self._severity_order:
                bar, count_lbl = self.severity_bars[level]
                count = severity_counts.get(level, 0)
                color = severity_colors[level]
                bar.setMaximum(max(1, total_sev))
                bar.setValue(count)
                bar.setStyleSheet(
                    f"QProgressBar{{background:{'#1e293b' if dark else '#f1f5f9'};"
                    f"border:none;border-radius:5px;}}"
                    f"QProgressBar::chunk{{background:{color};border-radius:5px;}}"
                )
                count_lbl.setText(str(count))
                count_lbl.setStyleSheet(
                    f"font-size:14px;font-weight:800;color:{text_primary};"
                    "background:transparent;"
                )

        # Availability card
        av_card = self.findChild(QWidget, "availabilityCard")
        if av_card:
            av_card.setStyleSheet(
                f"QWidget#availabilityCard{{background:{card_bg};border-radius:16px;"
                f"border:1px solid {border_color};}}"
            )
        if hasattr(self, "_dash_availability_title_lbl"):
            self._dash_availability_title_lbl.setStyleSheet(
                f"color:{text_secondary};font-size:10px;font-weight:800;"
                "letter-spacing:1.0px;background:transparent;"
            )
        day_text, time_text, updated_text = self._get_dashboard_availability_text()
        for attr, text, style in (
            ("availability_days_label",    f"Days: {day_text}",
             f"font-size:12px;font-weight:700;color:{text_primary};background:transparent;"),
            ("availability_time_label",    f"Hours: {time_text}",
             f"font-size:12px;font-weight:700;color:{text_primary};background:transparent;"),
            ("availability_updated_label", updated_text,
             f"font-size:11px;color:{text_muted};font-weight:600;background:transparent;"),
        ):
            w = getattr(self, attr, None)
            if w:
                w.setText(text)
                w.setStyleSheet(style)

        # Recent card
        rec_card = self.findChild(QWidget, "recentCard")
        if rec_card:
            rec_card.setStyleSheet(
                f"QWidget#recentCard{{background:{card_bg};border-radius:16px;"
                f"border:1px solid {border_color};}}"
            )
        if hasattr(self, "_dash_recent_title_lbl"):
            self._dash_recent_title_lbl.setStyleSheet(
                f"color:{text_secondary};font-size:10px;font-weight:800;"
                "letter-spacing:1.0px;background:transparent;"
            )

        # Populate recent list
        if hasattr(self, "recent_list_layout"):
            while self.recent_list_layout.count():
                item = self.recent_list_layout.takeAt(0)
                if w := item.widget():
                    w.deleteLater()

            if not rows:
                empty = QLabel("No screenings yet.")
                empty.setStyleSheet(
                    f"font-size:12px;color:{text_muted};font-weight:600;"
                    "background:transparent;padding:8px 0;"
                )
                self.recent_list_layout.addWidget(empty)
            else:
                for row_data in rows[:4]:
                    patient_id = row_data[0]
                    name       = row_data[1] or "Unknown"
                    result     = self._normalize_severity_label(row_data[2]) or "Pending"

                    item_w = QWidget()
                    item_w.setStyleSheet(
                        f"QWidget{{background:{'#1a2d42' if dark else '#f8fafc'};"
                        f"border:1px solid {border_color};"
                        "border-radius:10px;}}"
                    )
                    item_v = QVBoxLayout(item_w)
                    item_v.setContentsMargins(12, 8, 12, 8)
                    item_v.setSpacing(3)

                    name_lbl = QLabel(name)
                    name_lbl.setStyleSheet(
                        f"font-size:13px;font-weight:700;color:{text_primary};background:transparent;"
                    )

                    sub_row = QHBoxLayout()
                    sub_row.setContentsMargins(0, 0, 0, 0)
                    sub_row.setSpacing(6)

                    id_lbl = QLabel(patient_id)
                    id_lbl.setStyleSheet(
                        f"font-size:11px;font-weight:600;color:{text_muted};background:transparent;"
                    )
                    res_color = severity_colors.get(result, text_secondary)
                    res_lbl = QLabel(result)
                    res_lbl.setStyleSheet(
                        f"font-size:11px;font-weight:700;color:{res_color};background:transparent;"
                    )

                    sub_row.addWidget(id_lbl)
                    sub_row.addStretch()
                    sub_row.addWidget(res_lbl)

                    item_v.addWidget(name_lbl)
                    item_v.addLayout(sub_row)
                    self.recent_list_layout.addWidget(item_w)

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_severity_label(result_text):
        text = str(result_text or "").strip().lower()
        if not text or "pending" in text:
            return ""
        if "proliferative" in text:
            return "Proliferative DR"
        if "severe" in text:
            return "Severe DR"
        if "moderate" in text:
            return "Moderate DR"
        if "mild" in text:
            return "Mild DR"
        if "no dr" in text or "no diabetic retinopathy" in text or "normal" in text:
            return "No DR"
        return ""

    @staticmethod
    def _is_high_attention_result(result_text):
        text = str(result_text or "").lower()
        keywords = ("moderate", "severe", "proliferative", "refer", "urgent", "dr detected")
        return any(w in text for w in keywords)

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

    def _get_dashboard_availability_text(self):
        profile = get_user_profile(self.username) or {}
        raw = profile.get("availability_json")
        if not raw:
            return "Not set", "Not set", "Update from Users > Edit Availability"
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            return "Not set", "Not set", "Update from Users > Edit Availability"

        start = str(payload.get("start_time") or "").strip()
        end   = str(payload.get("end_time")   or "").strip()
        time_text = "Not set"
        if start and end:
            time_text = f"{self._format_availability_time(start)} – {self._format_availability_time(end)}"

        selected_days = payload.get("days") or []
        weekday_order = [("mon","Mon"),("tue","Tue"),("wed","Wed"),("thu","Thu"),
                         ("fri","Fri"),("sat","Sat"),("sun","Sun")]
        day_text = "Not set"
        if isinstance(selected_days, list) and selected_days:
            sel = {str(v).strip().lower() for v in selected_days}
            ordered = [lbl for k, lbl in weekday_order if k in sel]
            if ordered:
                day_text = ", ".join(ordered)

        updated_at = str(payload.get("updated_at") or "").strip()
        updated_text = "Update from Users > Edit Availability"
        if updated_at:
            with contextlib.suppress(Exception):
                parsed = datetime.fromisoformat(updated_at)
                updated_text = f"Last updated: {parsed.strftime('%b %d, %Y %I:%M %p')}"

        return day_text, time_text, updated_text

    @staticmethod
    def _format_availability_time(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        for candidate, fmt in ((text.upper(), "%I:%M %p"), (text, "%H:%M")):
            with contextlib.suppress(ValueError):
                return datetime.strptime(candidate, fmt).strftime("%I:%M %p").lstrip("0")
        return text

    @staticmethod
    def get_nav_button_style(icon_only=False):
        if icon_only:
            return (
                "QPushButton{color:#475057;text-align:center;padding:4px 0;border:1px solid transparent;"
                "border-radius:8px;font-size:22px;font-weight:500;background:transparent;text-decoration:none;}"
                "QPushButton:hover{background:#eef6ff;color:#1d5fa8;}"
                "QPushButton:pressed{background:#e2f0ff;}"
                "QPushButton:focus{outline:none;border:1px solid transparent;}"
            )
        return (
            "QPushButton{color:#495057;text-align:left;padding:15px 20px;border:none;"
            "border-radius:6px;font-size:14px;font-weight:500;background:transparent;}"
            "QPushButton:hover{background:#e9ecef;color:#007bff;}"
            "QPushButton:focus{outline:none;border:none;}"
        )