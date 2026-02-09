"""
Main entry point for EyeShield EMR with segmented modules.
Run this file to start the application with the segmented code structure.
"""

import sys
from pathlib import Path

# Add parent directory to path to import auth module
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication
from auth import init_db
from login import LoginWindow



if __name__ == "__main__":
    app = QApplication(sys.argv)

    init_db()

    win = LoginWindow()
    win.show()

    sys.exit(app.exec())
