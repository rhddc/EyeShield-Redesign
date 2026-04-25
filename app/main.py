"""
Main entry point for EyeShield EMR with segmented modules.
Run this file to start the application with the segmented code structure.
"""

import os
import sys
import traceback
from pathlib import Path
import threading

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
    from .safety_runtime import get_results_dir, write_activity, write_crash_log
except ImportError:
    from auth import UserManager
    from login import LoginWindow
    from app_paths import ICONS_DIR, ensure_repo_dirs
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
    # EMR-only runtime: patient data lives in EMR tables (users.db). Legacy patient_records.db
    # is not initialized here to avoid mixing demo/legacy rows with real patient flow.

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

    # NOTE: Importing torch can be very slow on some Windows installs.
    # Do NOT import model_inference on the UI thread during startup.
    def _warm_model_non_blocking() -> None:
        if os.environ.get("EYESHIELD_DISABLE_MODEL_WARMUP") == "1":
            return

        def _worker():
            try:
                try:
                    from .model_inference import preload_model_async, is_model_available, MODEL_PATH
                except ImportError:
                    from model_inference import preload_model_async, is_model_available, MODEL_PATH

                # If the model is missing, keep the app usable; the Screening page will surface this.
                if not is_model_available():
                    write_activity("WARN", "MODEL_MISSING", f"Expected model path: {MODEL_PATH}")
                    return

                preload_model_async()
            except Exception as err:
                # Never fail app launch because of warmup.
                write_activity("WARN", "MODEL_WARMUP_FAILED", str(err))

        threading.Thread(target=_worker, daemon=True, name="eyeshield-warmup-import").start()
    write_activity("INFO", "APP_OPENED", "EyeShield launched")
    app.aboutToQuit.connect(lambda: write_activity("INFO", "APP_CLOSED", "Application exit"))

    win = LoginWindow()
    win.show()

    # Start warmup only after the first window is visible.
    try:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, _warm_model_non_blocking)
    except Exception:
        _warm_model_non_blocking()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
