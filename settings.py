import json
import os
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QPushButton,
    QCheckBox,
    QComboBox,
    QMessageBox,
)

class SettingsPage(QWidget):
    SETTINGS_FILE = "settings_data.json"

    def __init__(self):
        super().__init__()
        self.setStyleSheet("""
            QWidget {
                background: #f8f9fa;
                color: #212529;
                font-size: 13px;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #dee2e6;
                border-radius: 8px;
                margin-top: 10px;
                font-weight: 600;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #0d6efd;
            }
            QComboBox {
                background: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 8px;
                padding: 8px;
                min-height: 20px;
            }
            QComboBox:focus {
                border: 1px solid #0d6efd;
            }
            QCheckBox:focus {
                color: #0d6efd;
            }
            QPushButton {
                background: #e9ecef;
                color: #212529;
                border: 1px solid #ced4da;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #dee2e6;
            }
            QPushButton:focus {
                border: 1px solid #0d6efd;
            }
            QPushButton#primaryAction {
                background: #0d6efd;
                color: #ffffff;
                border: 1px solid #0b5ed7;
            }
            QPushButton#primaryAction:hover {
                background: #0b5ed7;
            }
            QLabel#statusLabel {
                color: #495057;
                font-size: 12px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.title_label = QLabel("Settings")
        self.title_label.setStyleSheet("font-size:24px;font-weight:700;color:#007bff;font-family:'Segoe UI','Inter','Arial';")
        self.subtitle_label = QLabel("Local offline preferences for this installation")
        self.subtitle_label.setStyleSheet("font-size:13px;color:#6c757d;")
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)

        pref_group = QGroupBox("Preferences")
        self.pref_group = pref_group
        pref_layout = QVBoxLayout(pref_group)
        pref_layout.setSpacing(8)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_label = QLabel("Theme:")
        pref_layout.addWidget(self.theme_label)
        pref_layout.addWidget(self.theme_combo)

        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["English", "Spanish", "French", "Other"])
        self.language_label = QLabel("Language:")
        pref_layout.addWidget(self.language_label)
        pref_layout.addWidget(self.lang_combo)

        self.auto_logout = QCheckBox("Enable auto-logout after inactivity")
        self.confirm_deletions = QCheckBox("Ask confirmation before destructive actions")
        self.compact_tables = QCheckBox("Use compact table rows")
        checkbox_style = """
            QCheckBox {
                color: #212529;
                spacing: 8px;
                font-size: 13px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #6c757d;
                border-radius: 4px;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #0d6efd;
                border: 1px solid #0b5ed7;
            }
        """
        self.auto_logout.setStyleSheet(checkbox_style)
        self.confirm_deletions.setStyleSheet(checkbox_style)
        self.compact_tables.setStyleSheet(checkbox_style)
        pref_layout.addWidget(self.auto_logout)
        pref_layout.addWidget(self.confirm_deletions)
        pref_layout.addWidget(self.compact_tables)

        layout.addWidget(pref_group)

        # About/Info
        about_group = QGroupBox("About")
        self.about_group = about_group
        about_layout = QVBoxLayout(about_group)
        self.about_version_label = QLabel("EyeShield EMR v1.0.0")
        self.about_copyright_label = QLabel("© 2026 EyeShield Team")
        self.about_contact_label = QLabel("For support, contact: support@eyeshield.local")
        about_layout.addWidget(self.about_version_label)
        about_layout.addWidget(self.about_copyright_label)
        about_layout.addWidget(self.about_contact_label)
        layout.addWidget(about_group)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.reset_btn = QPushButton("Reset Defaults")
        self.reset_btn.clicked.connect(self.reset_defaults)
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("primaryAction")
        self.save_btn.setAutoDefault(True)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.save_settings)
        button_row.addWidget(self.reset_btn)
        button_row.addWidget(self.save_btn)
        layout.addLayout(button_row)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        self.load_settings()
        self.theme_combo.currentTextChanged.connect(self.apply_live_preview)
        self.lang_combo.currentTextChanged.connect(self.apply_live_preview)

        self.theme_combo.setFocus()
        self.setTabOrder(self.theme_combo, self.lang_combo)
        self.setTabOrder(self.lang_combo, self.auto_logout)
        self.setTabOrder(self.auto_logout, self.confirm_deletions)
        self.setTabOrder(self.confirm_deletions, self.compact_tables)
        self.setTabOrder(self.compact_tables, self.reset_btn)
        self.setTabOrder(self.reset_btn, self.save_btn)

        layout.addStretch()

    def _language_pack(self, language: str) -> dict:
        packs = {
            "English": {
                "title": "Settings",
                "subtitle": "Local offline preferences for this installation",
                "preferences": "Preferences",
                "theme": "Theme:",
                "language": "Language:",
                "auto_logout": "Enable auto-logout after inactivity",
                "confirm": "Ask confirmation before destructive actions",
                "compact": "Use compact table rows",
                "about": "About",
                "reset": "Reset Defaults",
                "save": "Save Settings",
            },
            "Spanish": {
                "title": "Configuración",
                "subtitle": "Preferencias locales sin conexión para esta instalación",
                "preferences": "Preferencias",
                "theme": "Tema:",
                "language": "Idioma:",
                "auto_logout": "Activar cierre automático por inactividad",
                "confirm": "Pedir confirmación antes de acciones destructivas",
                "compact": "Usar filas compactas en tablas",
                "about": "Acerca de",
                "reset": "Restablecer",
                "save": "Guardar configuración",
            },
            "French": {
                "title": "Paramètres",
                "subtitle": "Préférences locales hors ligne pour cette installation",
                "preferences": "Préférences",
                "theme": "Thème :",
                "language": "Langue :",
                "auto_logout": "Activer la déconnexion automatique après inactivité",
                "confirm": "Demander confirmation avant les actions destructrices",
                "compact": "Utiliser des lignes de tableau compactes",
                "about": "À propos",
                "reset": "Réinitialiser",
                "save": "Enregistrer les paramètres",
            },
        }
        return packs.get(language, packs["English"])

    def apply_live_preview(self, _value=None):
        app = QApplication.instance()
        if app is None:
            return

        theme = self.theme_combo.currentText()
        if theme == "Dark":
            app.setStyleSheet("""
                QWidget { background: #1f232a; color: #e9ecef; }
                QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
                    background: #2b3038;
                    color: #e9ecef;
                    border: 1px solid #495057;
                }
                QGroupBox { border: 1px solid #495057; }
                QPushButton { background: #343a40; color: #f8f9fa; border: 1px solid #495057; }
            """)
        else:
            app.setStyleSheet("")

        pack = self._language_pack(self.lang_combo.currentText())
        self.title_label.setText(pack["title"])
        self.subtitle_label.setText(pack["subtitle"])
        self.pref_group.setTitle(pack["preferences"])
        self.theme_label.setText(pack["theme"])
        self.language_label.setText(pack["language"])
        self.auto_logout.setText(pack["auto_logout"])
        self.confirm_deletions.setText(pack["confirm"])
        self.compact_tables.setText(pack["compact"])
        self.about_group.setTitle(pack["about"])
        self.reset_btn.setText(pack["reset"])
        self.save_btn.setText(pack["save"])

        self.status_label.setText(f"Live preview: {theme} / {self.lang_combo.currentText()}")

    def _settings_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), self.SETTINGS_FILE)

    def _default_settings(self) -> dict:
        return {
            "theme": "Light",
            "language": "English",
            "auto_logout": True,
            "confirm_deletions": True,
            "compact_tables": False,
        }

    def load_settings(self):
        settings = self._default_settings()
        path = self._settings_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass

        self.theme_combo.setCurrentText(settings.get("theme", "Light"))
        self.lang_combo.setCurrentText(settings.get("language", "English"))
        self.auto_logout.setChecked(bool(settings.get("auto_logout", True)))
        self.confirm_deletions.setChecked(bool(settings.get("confirm_deletions", True)))
        self.compact_tables.setChecked(bool(settings.get("compact_tables", False)))
        self.apply_live_preview()
        self.status_label.setText("Settings loaded")

    def save_settings(self):
        settings = {
            "theme": self.theme_combo.currentText(),
            "language": self.lang_combo.currentText(),
            "auto_logout": self.auto_logout.isChecked(),
            "confirm_deletions": self.confirm_deletions.isChecked(),
            "compact_tables": self.compact_tables.isChecked(),
        }
        try:
            with open(self._settings_path(), "w", encoding="utf-8") as file:
                json.dump(settings, file, indent=2)
            timestamp = datetime.now().strftime("%I:%M %p").lstrip("0")
            self.status_label.setText(f"Saved locally at {timestamp}")
        except OSError as err:
            self.status_label.setText("Save failed")
            QMessageBox.warning(self, "Settings", f"Failed to save settings: {err}")

    def reset_defaults(self):
        defaults = self._default_settings()
        self.theme_combo.setCurrentText(defaults["theme"])
        self.lang_combo.setCurrentText(defaults["language"])
        self.auto_logout.setChecked(defaults["auto_logout"])
        self.confirm_deletions.setChecked(defaults["confirm_deletions"])
        self.compact_tables.setChecked(defaults["compact_tables"])
        self.status_label.setText("Defaults restored (not yet saved)")
