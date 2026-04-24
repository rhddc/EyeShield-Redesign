-- ============================================================
-- EyeShield EMR Database Schema
-- ============================================================
-- Roles: admin, frontdesk, doctor
-- Flow: frontdesk creates patient → doctor performs screening
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ------------------------------------------------------------
-- 1. USERS (all system accounts)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,                  -- bcrypt hash
    full_name       TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('admin', 'frontdesk', 'doctor')),
    email           TEXT,
    contact_number  TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,     -- 1=active, 0=disabled
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed: ensure at least one admin exists at all times
-- (enforced at application layer)

-- ------------------------------------------------------------
-- 2. PATIENTS (created and managed by frontdesk)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patients (
    patient_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code        TEXT NOT NULL UNIQUE,       -- e.g. EYS-2026-00001
    last_name           TEXT NOT NULL,
    first_name          TEXT NOT NULL,
    middle_name         TEXT,
    date_of_birth       TEXT NOT NULL,              -- ISO 8601: YYYY-MM-DD
    age                 INTEGER,                    -- computed or stored
    sex                 TEXT CHECK (sex IN ('Male', 'Female', 'Other')),
    contact_number      TEXT,
    email               TEXT,
    address             TEXT,

    -- Anthropometric
    height_cm           REAL,
    weight_kg           REAL,
    bmi                 REAL,                       -- stored after computation

    -- Clinical History
    diabetes_type       TEXT CHECK (diabetes_type IN ('Type 1', 'Type 2', 'Gestational', 'Other', NULL)),
    dm_duration_years   REAL,                       -- years with DM
    hba1c               REAL,                       -- latest HbA1c %
    current_medications TEXT,
    known_allergies     TEXT,
    other_conditions    TEXT,                       -- comorbidities

    -- Eye History
    current_eye_treatment   TEXT,
    previous_eye_treatment  TEXT,
    last_eye_exam_date      TEXT,

    -- Administrative
    created_by          INTEGER NOT NULL REFERENCES users(user_id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- 3. QUEUE ENTRIES (one record per patient visit, managed by frontdesk)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS queue_entries (
    queue_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    queue_number    TEXT NOT NULL,                  -- e.g. "Q-001", sequential per day
    visit_date      TEXT NOT NULL DEFAULT (date('now')),
    status          TEXT NOT NULL DEFAULT 'waiting'
                    CHECK (status IN ('waiting', 'in_progress', 'completed', 'cancelled')),
    assigned_by     INTEGER NOT NULL REFERENCES users(user_id),  -- frontdesk user
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- 4. SCREENINGS (one record per screening visit)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS screenings (
    screening_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id          INTEGER NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    queue_entry_id      INTEGER REFERENCES queue_entries(queue_id) ON DELETE SET NULL,  -- visit that triggered this
    performed_by        INTEGER NOT NULL REFERENCES users(user_id),   -- doctor
    screening_date      TEXT NOT NULL DEFAULT (datetime('now')),

    -- Visit type (controls UI: "Start Diagnosis" vs "New Follow-up Screening")
    screening_type      TEXT NOT NULL DEFAULT 'initial'
                        CHECK (screening_type IN ('initial', 'follow_up')),

    -- Which eye(s) were screened
    eye_screened        TEXT NOT NULL CHECK (eye_screened IN ('Left', 'Right', 'Both')),

    -- Overall session status
    session_status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (session_status IN ('pending', 'completed', 'rejected_all', 'partial')),

    -- Doctor notes for the entire session
    doctor_notes        TEXT,

    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- 5. SCREENING EYES (one row per eye per screening)
--    Allows storing separate results for Left and Right eyes
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS screening_eyes (
    eye_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    screening_id        INTEGER NOT NULL REFERENCES screenings(screening_id) ON DELETE CASCADE,
    eye_side            TEXT NOT NULL CHECK (eye_side IN ('Left', 'Right')),

    -- ── Image Storage ──────────────────────────────────────────
    fundus_image_path   TEXT,                       -- path to original fundus image
    gradcam_image_path  TEXT,                       -- path to Grad-CAM++ overlay image

    -- ── Image Quality Gate ─────────────────────────────────────
    image_quality_status    TEXT CHECK (image_quality_status IN ('gradable', 'rejected', 'pending'))
                            DEFAULT 'pending',
    quality_rejection_reason TEXT,                  -- e.g. "blur", "poor_illumination", "artifact"
    blur_score              REAL,
    illumination_score      REAL,
    entropy_score           REAL,

    -- ── AI Inference ───────────────────────────────────────────
    -- Only populated when image_quality_status = 'gradable' AND uncertainty accepted
    ai_dr_grade             INTEGER CHECK (ai_dr_grade BETWEEN 0 AND 4),
                                                    -- 0=No DR, 1=Mild, 2=Moderate, 3=Severe, 4=Proliferative
    ai_confidence           REAL,                   -- 0.0–1.0
    aleatoric_uncertainty   REAL,                   -- data uncertainty
    epistemic_uncertainty   REAL,                   -- model uncertainty
    total_uncertainty       REAL,                   -- u = K/S (EDL)
    uncertainty_status      TEXT CHECK (uncertainty_status IN ('accepted', 'rejected', 'pending'))
                            DEFAULT 'pending',

    -- ── Heatmap Gate ───────────────────────────────────────────
    heatmap_generated       INTEGER DEFAULT 0,      -- 1 if Grad-CAM++ was conditionally generated

    -- ── Treatment Suggestion ───────────────────────────────────
    ai_treatment_suggestion TEXT,                   -- auto-generated based on grade

    -- ── Doctor Verification ────────────────────────────────────
    doctor_accepted_ai      INTEGER,                -- 1=accepted, 0=overridden, NULL=pending
    final_dr_grade          INTEGER CHECK (final_dr_grade BETWEEN 0 AND 4),
                                                    -- if overridden, doctor's grade; else = ai_dr_grade
    override_justification  TEXT,                   -- required if doctor_accepted_ai = 0
    final_treatment_notes   TEXT,                   -- doctor's specific treatment plan

    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE (screening_id, eye_side)                 -- one row per eye per screening
);

-- ------------------------------------------------------------
-- 6. ACTIVITY LOGS (audit trail for all user actions)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_logs (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    action          TEXT NOT NULL,                  -- e.g. 'LOGIN', 'CREATE_PATIENT', 'RUN_INFERENCE'
    target_type     TEXT,                           -- e.g. 'patient', 'screening', 'user'
    target_id       INTEGER,                        -- FK to affected record
    detail          TEXT,                           -- JSON or free-text extra info
    ip_address      TEXT,
    logged_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- INDEXES (for common query patterns)
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_patients_code         ON patients (patient_code);
CREATE INDEX IF NOT EXISTS idx_patients_name         ON patients (last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_queue_patient         ON queue_entries (patient_id);
CREATE INDEX IF NOT EXISTS idx_queue_date            ON queue_entries (visit_date);
CREATE INDEX IF NOT EXISTS idx_queue_status          ON queue_entries (status);
CREATE INDEX IF NOT EXISTS idx_screenings_patient    ON screenings (patient_id);
CREATE INDEX IF NOT EXISTS idx_screenings_queue      ON screenings (queue_entry_id);
CREATE INDEX IF NOT EXISTS idx_screenings_doctor     ON screenings (performed_by);
CREATE INDEX IF NOT EXISTS idx_screenings_date       ON screenings (screening_date);
CREATE INDEX IF NOT EXISTS idx_screenings_type       ON screenings (screening_type);
CREATE INDEX IF NOT EXISTS idx_eyes_screening        ON screening_eyes (screening_id);
CREATE INDEX IF NOT EXISTS idx_eyes_grade            ON screening_eyes (final_dr_grade);
CREATE INDEX IF NOT EXISTS idx_logs_user             ON activity_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_logs_action           ON activity_logs (action);
CREATE INDEX IF NOT EXISTS idx_logs_target           ON activity_logs (target_type, target_id);

-- ============================================================
-- VIEWS (convenience queries)
-- ============================================================

-- Full screening summary (latest result per eye per patient)
CREATE VIEW IF NOT EXISTS v_screening_summary AS
SELECT
    p.patient_id,
    p.patient_code,
    p.last_name || ', ' || p.first_name          AS patient_name,
    p.date_of_birth,
    p.diabetes_type,
    s.screening_id,
    s.screening_type,
    s.screening_date,
    q.queue_number,
    q.visit_date,
    u.full_name                                  AS doctor_name,
    se.eye_side,
    se.image_quality_status,
    se.uncertainty_status,
    se.ai_dr_grade,
    ROUND(se.ai_confidence * 100, 1)             AS ai_confidence_pct,
    se.total_uncertainty,
    se.doctor_accepted_ai,
    se.final_dr_grade,
    CASE se.final_dr_grade
        WHEN 0 THEN 'No DR'
        WHEN 1 THEN 'Mild DR'
        WHEN 2 THEN 'Moderate DR'
        WHEN 3 THEN 'Severe DR'
        WHEN 4 THEN 'Proliferative DR'
        ELSE 'Unknown'
    END                                          AS dr_severity_label,
    se.heatmap_generated,
    s.session_status
FROM screening_eyes se
JOIN screenings      s  ON se.screening_id  = s.screening_id
JOIN patients        p  ON s.patient_id     = p.patient_id
JOIN users           u  ON s.performed_by   = u.user_id
LEFT JOIN queue_entries q ON s.queue_entry_id = q.queue_id;

-- DR grade distribution (for dashboard stats)
CREATE VIEW IF NOT EXISTS v_grade_distribution AS
SELECT
    CASE final_dr_grade
        WHEN 0 THEN 'No DR'
        WHEN 1 THEN 'Mild DR'
        WHEN 2 THEN 'Moderate DR'
        WHEN 3 THEN 'Severe DR'
        WHEN 4 THEN 'Proliferative DR'
        ELSE 'Pending/Rejected'
    END AS grade_label,
    final_dr_grade,
    COUNT(*) AS count
FROM screening_eyes
GROUP BY final_dr_grade;

-- ============================================================
-- TRIGGERS (auto-update timestamps + auto-compute BMI)
-- ============================================================

CREATE TRIGGER IF NOT EXISTS trg_patients_updated
AFTER UPDATE ON patients
BEGIN
    UPDATE patients SET updated_at = datetime('now') WHERE patient_id = NEW.patient_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_queue_updated
AFTER UPDATE ON queue_entries
BEGIN
    UPDATE queue_entries SET updated_at = datetime('now') WHERE queue_id = NEW.queue_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_screenings_updated
AFTER UPDATE ON screenings
BEGIN
    UPDATE screenings SET updated_at = datetime('now') WHERE screening_id = NEW.screening_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_eyes_updated
AFTER UPDATE ON screening_eyes
BEGIN
    UPDATE screening_eyes SET updated_at = datetime('now') WHERE eye_id = NEW.eye_id;
END;

-- Auto-compute BMI on patient insert/update if height and weight are provided
CREATE TRIGGER IF NOT EXISTS trg_compute_bmi_insert
AFTER INSERT ON patients
WHEN NEW.height_cm IS NOT NULL AND NEW.weight_kg IS NOT NULL AND NEW.height_cm > 0
BEGIN
    UPDATE patients
    SET bmi = ROUND(NEW.weight_kg / ((NEW.height_cm / 100.0) * (NEW.height_cm / 100.0)), 2)
    WHERE patient_id = NEW.patient_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_compute_bmi_update
AFTER UPDATE OF height_cm, weight_kg ON patients
WHEN NEW.height_cm IS NOT NULL AND NEW.weight_kg IS NOT NULL AND NEW.height_cm > 0
BEGIN
    UPDATE patients
    SET bmi = ROUND(NEW.weight_kg / ((NEW.height_cm / 100.0) * (NEW.height_cm / 100.0)), 2)
    WHERE patient_id = NEW.patient_id;
END;
