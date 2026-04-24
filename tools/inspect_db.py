import os
import sqlite3
from pathlib import Path
import sys

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_paths import PATIENT_RECORDS_DB_PATH

DB_FILE = str(PATIENT_RECORDS_DB_PATH)

def inspect_db():
    if not os.path.exists(DB_FILE):
        print(f"Database file {DB_FILE} not found.")
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    
    print("--- Table Info ---")
    cur.execute("PRAGMA table_info(patient_records);")
    columns = cur.fetchall()
    for col in columns:
        print(col)
        
    print("\n--- Record 86 ---")
    cur.execute("SELECT * FROM patient_records WHERE id = 86")
    row = cur.fetchone()
    if row:
        print(row)
    else:
        print("Record 86 not found.")
        
    conn.close()

if __name__ == "__main__":
    inspect_db()
