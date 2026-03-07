"""
Authentication module for EyeShield EMR application.
Handles user database, login verification, and user management.
"""

import sqlite3
import hashlib
import hmac
import os
import json
import secrets
from typing import Optional

DB_FILE = "users.db"


# ============================================================
# DATABASE CONNECTION
# ============================================================

class DatabaseConnection:
    """Manages database connections"""
    
    @staticmethod
    def get_connection() -> sqlite3.Connection:
        """Get a database connection"""
        return sqlite3.connect(DB_FILE)


def get_connection() -> sqlite3.Connection:
    """Get a database connection (legacy function)"""
    return DatabaseConnection.get_connection()


# ============================================================
# PASSWORD HASHING
# ============================================================

class PasswordManager:
    """Manages password hashing and verification"""

    _ALGO = "pbkdf2_sha256"
    _ITERATIONS = 260_000
    _SALT_BYTES = 16
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using PBKDF2-SHA256"""
        salt = secrets.token_bytes(PasswordManager._SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PasswordManager._ITERATIONS,
        )
        return (
            f"{PasswordManager._ALGO}${PasswordManager._ITERATIONS}$"
            f"{salt.hex()}${digest.hex()}"
        )

    @staticmethod
    def needs_upgrade(password_hash: str) -> bool:
        return not password_hash.startswith(f"{PasswordManager._ALGO}$")

    @staticmethod
    def _verify_pbkdf2(password: str, password_hash: str) -> bool:
        try:
            algo, iterations_str, salt_hex, digest_hex = password_hash.split("$")
            if algo != PasswordManager._ALGO:
                return False
            iterations = int(iterations_str)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(digest_hex)
        except (ValueError, TypeError):
            return False

        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(candidate, expected)

    @staticmethod
    def _verify_legacy_sha256(password: str, password_hash: str) -> bool:
        if not password_hash.startswith("sha256:"):
            return False
        stored_hash = password_hash.split(":", 1)[1]
        password_hash_candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(password_hash_candidate, stored_hash)
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash"""
        if password_hash.startswith(f"{PasswordManager._ALGO}$"):
            return PasswordManager._verify_pbkdf2(password, password_hash)
        if password_hash.startswith("sha256:"):
            return PasswordManager._verify_legacy_sha256(password, password_hash)
        return hmac.compare_digest(password, password_hash)


def hash_password(password: str) -> str:
    """Hash a password (legacy function)"""
    return PasswordManager.hash_password(password)


# ============================================================
# USER DATABASE MANAGEMENT
# ============================================================

class UserManager:
    """Manages user database operations"""

    _PATIENT_RECORD_COLUMNS = {
        "archived_at": "TEXT",
        "archived_by": "TEXT",
        "archive_reason": "TEXT",
    }
    
    def __init__(self):
        self.conn = self._init_db()
    
    @staticmethod
    def _init_db() -> sqlite3.Connection:
        """Initialize the database"""
        first_run = not os.path.exists(DB_FILE)

        conn = get_connection()
        cur = conn.cursor()
        # Users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
            )
        """)

        # Patient records table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS patient_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT,
                name TEXT,
                birthdate TEXT,
                age TEXT,
                sex TEXT,
                contact TEXT,
                eyes TEXT,
                diabetes_type TEXT,
                duration TEXT,
                hba1c TEXT,
                prev_treatment TEXT,
                notes TEXT,
                result TEXT,
                confidence TEXT,
                archived_at TEXT,
                archived_by TEXT,
                archive_reason TEXT
            )
        """)

        UserManager._ensure_patient_record_columns(conn)

        conn.commit()

        UserManager._migrate_users_json(conn)
        UserManager._ensure_admin_user(conn, first_run)

        return conn

    @staticmethod
    def _ensure_patient_record_columns(conn: sqlite3.Connection) -> None:
        """Add archive-related columns for existing patient_records tables."""
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(patient_records)")
        existing_columns = {row[1] for row in cur.fetchall()}

        for column_name, column_type in UserManager._PATIENT_RECORD_COLUMNS.items():
            if column_name in existing_columns:
                continue
            cur.execute(
                f"ALTER TABLE patient_records ADD COLUMN {column_name} {column_type}"
            )

    @staticmethod
    def _migrate_users_json(conn: sqlite3.Connection) -> None:
        """Migrate legacy JSON users into SQLite (one-time safe import)."""
        json_path = os.path.join(os.path.dirname(__file__), "users_data.json")
        if not os.path.exists(json_path):
            return

        try:
            with open(json_path, "r", encoding="utf-8") as file:
                users = json.load(file)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(users, list):
            return

        cur = conn.cursor()
        for user in users:
            if not isinstance(user, dict):
                continue
            username = str(user.get("username", "")).strip()
            raw_password = str(user.get("password", ""))
            role = str(user.get("role", "clinician") or "clinician")
            if not username or not raw_password:
                continue

            cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cur.fetchone():
                continue

            if raw_password.startswith("sha256:"):
                password_hash = raw_password
            else:
                password_hash = PasswordManager.hash_password(raw_password)

            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, password_hash, role),
            )
        conn.commit()

    @staticmethod
    def _ensure_admin_user(conn: sqlite3.Connection, first_run: bool) -> None:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        if total_users > 0:
            return

        username = os.environ.get("EYESHIELD_DEFAULT_ADMIN_USER", "admin")
        password = os.environ.get("EYESHIELD_DEFAULT_ADMIN_PASS")
        generated_password = False
        if not password:
            password = secrets.token_urlsafe(10)
            generated_password = True

        password_hash = PasswordManager.hash_password(password)
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, "admin"),
        )
        conn.commit()

        if generated_password:
            print("[EyeShield] Initial admin account created.")
            print(f"[EyeShield] Username: {username}")
            print(f"[EyeShield] Temporary password: {password}")
            print("[EyeShield] Set EYESHIELD_DEFAULT_ADMIN_PASS to control first-run credentials.")
    
    @staticmethod
    def create_user(username: str, password: str, role: str = "clinician") -> bool:
        """Create a new user"""
        if not username or not password:
            return False
        
        conn = get_connection()
        cur = conn.cursor()
        
        pw_hash = PasswordManager.hash_password(password)
        
        try:
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, pw_hash, role)
            )
            conn.commit()
            success = True
        except sqlite3.IntegrityError:
            success = False
        
        conn.close()
        return success
    
    @staticmethod
    def verify_user(username: str, password: str) -> Optional[str]:
        """Verify user credentials and return role"""
        if not username or not password:
            return None
        
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id, password_hash, role FROM users WHERE username = ?", (username,))
        
        row = cur.fetchone()
        
        if not row:
            conn.close()
            return None
        
        user_id, pw_hash, role = row
        
        if PasswordManager.verify_password(password, pw_hash):
            if PasswordManager.needs_upgrade(pw_hash):
                try:
                    upgraded_hash = PasswordManager.hash_password(password)
                    cur.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (upgraded_hash, user_id),
                    )
                    conn.commit()
                except sqlite3.Error:
                    pass
            conn.close()
            return role

        conn.close()
        return None
    
    @staticmethod
    def get_all_users() -> list[tuple]:
        """Get all users"""
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT username, role FROM users")
        users = cur.fetchall()
        
        conn.close()
        return users
    
    @staticmethod
    def update_user_role(username: str, new_role: str) -> bool:
        """Update a user's role"""
        valid_roles = ["clinician", "admin", "viewer"]
        if new_role not in valid_roles:
            return False
        
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            cur.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                (new_role, username)
            )
            conn.commit()
            success = True
        except sqlite3.Error:
            success = False
        
        conn.close()
        return success
    
    @staticmethod
    def delete_user(username: str) -> bool:
        """Delete a user"""
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            cur.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
            success = True
        except sqlite3.Error:
            success = False
        
        conn.close()
        return success

    @staticmethod
    def reset_password(username: str, new_password: str) -> bool:
        """Reset a user's password"""
        if not username or not new_password:
            return False

        conn = get_connection()
        cur = conn.cursor()

        pw_hash = PasswordManager.hash_password(new_password)

        try:
            cur.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (pw_hash, username),
            )
            conn.commit()
            success = cur.rowcount > 0
        except sqlite3.Error:
            success = False

        conn.close()
        return success


