from __future__ import annotations

from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
SQL_DIR = PROJECT_ROOT / "sql"
TOOLS_DIR = PROJECT_ROOT / "tools"
TEXT_DIR = PROJECT_ROOT / "text"

ICONS_DIR = APP_DIR / "icons"
CONFIG_DIR = APP_DIR / "config"
MODELS_DIR = APP_DIR / "models"
STORED_IMAGES_DIR = APP_DIR / "stored_images"
UPLOADS_DIR = APP_DIR / "uploads"

BACKUPS_DIR = DATA_DIR / "backups"
USERS_DB_PATH = DATA_DIR / "users.db"
PATIENT_RECORDS_DB_PATH = DATA_DIR / "patient_records.db"
LEGACY_DB_PATH = DATA_DIR / "eyeshield.db"
USERS_BACKUP_DB_GLOB = "users.backup_*.db"


def ensure_repo_dirs() -> None:
    for path in (
        DATA_DIR,
        SQL_DIR,
        TOOLS_DIR,
        TEXT_DIR,
        BACKUPS_DIR,
        UPLOADS_DIR,
        STORED_IMAGES_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def data_path(filename: str) -> Path:
    return DATA_DIR / filename


def sql_path(filename: str) -> Path:
    return SQL_DIR / filename
