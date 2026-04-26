import os

with open(r'c:\Users\Computer\Desktop\EyeShield\EyeShield-modelTest\app\reports.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_block = """        import traceback as _tb
        record_id = 0
        try:
            import sqlite3
            from datetime import datetime
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Use patient_code if available, otherwise fall back to patient_id
            pid_code = str(
                record.get("patient_code")
                or record.get("patient_id")
                or ""
            ).strip()

            dob_iso = str(record.get("birthdate") or record.get("date_of_birth") or "")
            age_str = str(record.get("age") or "")
            if not age_str and dob_iso:
                try:
                    born = datetime.strptime(dob_iso[:10], "%Y-%m-%d").date()
                    today = datetime.now().date()
                    age_val = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
                    age_str = str(max(age_val, 0))
                except Exception:
                    pass

            cur.execute(
                '''
                INSERT INTO patient_records (
                    patient_id, name, birthdate, age, sex, contact, phone, email, address, eyes,
                    diabetes_type, duration, treatment_regimen, prev_dr_stage,
                    notes, result, confidence,
                    screened_at, screening_type, follow_up, followup_date, followup_label,
                    original_screener_username, original_screener_name, decision_mode,
                    height, weight, bmi
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                ''',
                (
                    pid_code,
                    str(record.get("name") or ""),
                    dob_iso,
                    age_str,
                    str(record.get("sex") or ""),
                    str(record.get("contact") or record.get("contact_number") or record.get("phone") or ""),
                    str(record.get("phone") or record.get("contact") or record.get("contact_number") or ""),
                    str(record.get("email") or ""),
                    str(record.get("address") or ""),
                    str(record.get("eyes") or record.get("eye_summary") or ""),
                    str(record.get("diabetes_type") or ""),
                    str(record.get("duration") or record.get("dm_duration_years") or ""),
                    str(record.get("treatment_regimen") or record.get("treatment") or record.get("current_medications") or ""),
                    str(record.get("prev_dr_stage") or ""),
                    str(record.get("notes") or record.get("doctor_findings") or ""),
                    str(record.get("result") or record.get("final_diagnosis_icdr") or record.get("ai_classification") or "Pending"),
                    str(record.get("confidence") or ""),
                    now,
                    "follow_up",
                    "Yes",
                    now,
                    "Follow-up screening",
                    str(record.get("original_screener_username") or ""),
                    str(record.get("original_screener_name") or ""),
                    "emr",
                    str(record.get("height_cm") or record.get("height") or ""),
                    str(record.get("weight_kg") or record.get("weight") or ""),
                    str(record.get("bmi") or ""),
                ),
            )
            conn.commit()
            record_id = int(cur.lastrowid or 0)
        except Exception as exc:
            _tb.print_exc()
            QMessageBox.warning(
                self, "New Follow-up Screening",
                f"Unable to prepare the follow-up screening form.\\n\\nDetail: {type(exc).__name__}: {exc}",
            )
            return
"""

start_idx = -1
end_idx = -1
for i, l in enumerate(lines):
    if 'ok_db, err = ensure_patient_records_db()' in l:
        start_idx = i + 4
        break

for i in range(start_idx, len(lines)):
    if 'finally:' in lines[i]:
        end_idx = i - 1
        break

if start_idx != -1 and end_idx != -1:
    lines = lines[:start_idx] + [new_block] + lines[end_idx+1:]
    with open(r'c:\Users\Computer\Desktop\EyeShield\EyeShield-modelTest\app\reports.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('Replaced block from', start_idx, 'to', end_idx)
else:
    print('Could not find bounds')
