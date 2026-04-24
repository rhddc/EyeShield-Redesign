"""
Main entry point for EyeShield EMR with segmented modules.
Run this file to start the application with the segmented code structure.
"""

import os
import sys
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon, QPixmap, QImage, QPainter, QFont, QFontDatabase
from PySide6.QtSvg import QSvgRenderer
try:
    from .auth import UserManager
    from .login import LoginWindow
    from .app_paths import ICONS_DIR, ensure_repo_dirs
    from .db import ensure_patient_records_db
    from .safety_runtime import get_results_dir, write_activity, write_crash_log
except ImportError:
    from auth import UserManager
    from login import LoginWindow
    from app_paths import ICONS_DIR, ensure_repo_dirs
    from db import ensure_patient_records_db
    from safety_runtime import get_results_dir, write_activity, write_crash_log


def _hide_detached_windows_console():
    """Hide the Windows console host unless explicitly kept for debugging."""
    if os.name != "nt":
        return
    if os.environ.get("EYESHIELD_KEEP_CONSOLE") == "1":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def load_svg_icon(svg_path, size=256):
    """Render an SVG file to a QIcon."""
    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        return QIcon()
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return QIcon(QPixmap.fromImage(image))


def main() -> int:
    _hide_detached_windows_console()
    ensure_repo_dirs()
    app = QApplication(sys.argv)

    def _crash_hook(exc_type, exc_value, exc_tb):
        try:
            crash_file = write_crash_log(exc_type, exc_value, exc_tb, app_state="main")
            write_activity("ERROR", "APP_CRASH", f"Crash log: {crash_file}")
        except Exception:
            pass
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_hook

    # Modern font — Segoe UI Variable is available on Windows 11; falls back gracefully
    modern_font = QFont("Segoe UI Variable", 11)
    if not modern_font.exactMatch():
        modern_font = QFont("Segoe UI", 11)
    modern_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(modern_font)

    # Enforce font family globally via stylesheet
    app.setStyleSheet("* { font-family: 'Segoe UI Variable', 'Segoe UI', 'Inter', 'Arial', sans-serif; font-size: 13px; text-decoration: none; }")

    # Set application-wide icon
    _logo_path = str(ICONS_DIR / "Logo.png")
    _fallback_icon_path = str(ICONS_DIR / "eyeshield_icon.svg")
    if os.path.isfile(_logo_path):
        app.setWindowIcon(QIcon(_logo_path))
    else:
        app.setWindowIcon(load_svg_icon(_fallback_icon_path))

    # Initialize the database
    UserManager._init_db()
    ok_records, records_err = ensure_patient_records_db()
    if not ok_records:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("Database Error")
        msg.setText("Cannot initialize patient records database.")
        msg.setInformativeText(str(records_err))
        msg.addButton("Exit", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        sys.exit(1)

    # Validate write access to local results directory on launch.
    _results_dir_display = "unknown path"
    try:
        _results_dir = get_results_dir()
        _results_dir_display = str(_results_dir)
    except OSError as err:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("Storage Error")
        msg.setText(f"Cannot access results directory at {_results_dir_display}. Check folder permissions.")
        msg.setInformativeText(str(err))
        msg.addButton("Exit", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        sys.exit(1)

    # Begin loading the DR model in the background so it is warm before the
    # user navigates to the Screening page (eliminates first-scan delay).
    try:
        from .model_inference import preload_model_async, is_model_available, MODEL_PATH
    except ImportError:
        from model_inference import preload_model_async, is_model_available, MODEL_PATH
    if not is_model_available():
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("Model Error")
        msg.setText("Model file not found or corrupted. Please reinstall the application.")
        msg.setInformativeText(f"Expected model path:\n{MODEL_PATH}")
        copy_btn = msg.addButton("Copy Error", QMessageBox.ButtonRole.ActionRole)
        exit_btn = msg.addButton("Exit", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == copy_btn:
            app.clipboard().setText(f"Model file not found or corrupted: {MODEL_PATH}")
        if msg.clickedButton() in (copy_btn, exit_btn):
            sys.exit(1)

    preload_model_async()
    write_activity("INFO", "APP_OPENED", "EyeShield launched")
    app.aboutToQuit.connect(lambda: write_activity("INFO", "APP_CLOSED", "Application exit"))

    win = LoginWindow()
    win.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
