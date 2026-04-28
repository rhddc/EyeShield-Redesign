"""Centralised translation strings for EyeShield EMR. English only."""

TRANSLATIONS = {
    "English": {
        # Settings page
        "settings_title": "Settings",
        "settings_subtitle": "Local offline preferences for this installation",
        "settings_preferences": "Preferences",
        "settings_theme": "Theme:",
        "settings_language": "Language:",
        "settings_auto_logout": "Enable auto-logout after inactivity",
        "settings_confirm": "Ask confirmation before destructive actions",
        "settings_compact": "Use compact table rows",
        "settings_about": "About EyeShield EMR",
        "settings_terms": "Terms of Use",
        "settings_privacy": "Privacy & Security Policy",
        "settings_about_text": (
            "EyeShield EMR is a state-of-the-art Electronic Medical Record system specifically designed for "
            "Diabetic Retinopathy (DR) screening and management. It combines a premium, high-performance "
            "interface with advanced AI diagnostics, bilateral screening workflows, and longitudinal "
            "patient tracking. Our local-first architecture ensures that sensitive clinical data remains "
            "private and accessible even without internet connectivity. AI output serves as a powerful "
            "decision support tool and must be reviewed by a qualified clinician."
        ),
        "settings_terms_text": (
            "EyeShield EMR is provided as a professional clinical support tool. By using this system, you "
            "agree to utilize it only for authorized clinical screening, documentation, and referral workflows. "
            "All AI-generated assessments are for decision support only and must be verified by a qualified "
            "healthcare professional. You agree to maintain strict confidentiality of patient records, use "
            "the system within your assigned role permissions, and ensure data integrity at all times. "
            "The software does not replace professional medical judgment."
        ),
        "settings_privacy_text": (
            "Your data remains under your control. EyeShield EMR utilizes a local-first architecture, storing "
            "all patient records and clinical data securely on this device or local network. We implement "
            "rigorous role-based access control and provide tools for secure data management, including "
            "workstation auto-lock and detailed activity logging. Users are responsible for complying with "
            "local healthcare privacy regulations (such as HIPAA/GDPR where applicable) and internal data "
            "handling policies. Exports and printed reports must be handled via approved clinical channels."
        ),
        "settings_reset": "Reset Defaults",
        "settings_save": "Save Settings",
        # Nav labels
        "nav_dashboard": "Dashboard",
        "nav_screening": "Screening",
        "nav_camera": "Camera",
        "nav_reports": "Reports",
        "nav_users": "Users",
        "nav_settings": "Settings",
        "nav_help": "Help",
        # Dashboard
        "dash_welcome": "Welcome back",
        "dash_kpi_total": "TOTAL SCREENINGS",
        "dash_kpi_flagged": "FLAGGED FOR REVIEW",
        "dash_kpi_pending": "PENDING REVIEW",
        "dash_kpi_conf": "MODEL CONFIDENCE",
        "dash_recent": "RECENT SCREENINGS",
        "dash_actions_title": "QUICK ACTIONS",
        "dash_btn_new": "  New Screening",
        "dash_btn_reports": "  View Reports",
        "dash_insight_title": "CLINICAL INSIGHT",
        "dash_insight_default": "Start a screening to generate insight.",
        "dash_empty": "No screening records yet. Start by running a new screening.",
        "dash_kpi_total_sub": "All saved DR screenings",
        "dash_flagged_cases": "Cases flagged for review",
        "dash_no_flagged": "No cases flagged",
        "dash_awaiting": "Awaiting review",
        "dash_all_reviewed": "All reviews complete",
        "dash_conf_across": "Across {n} record",
        "dash_no_conf": "No confidence data yet",
        "dash_no_screenings": "No screenings yet. Run a new screening to see trends here.",
        "dash_insight_all_clear": "All screenings reviewed — no action needed. Continue routine monitoring.",
        # Camera
        "cam_title": "Camera Integration Sandbox",
        "cam_subtitle": "Camera preview and diagnostics while fundus camera integration is in progress.",
        "cam_stopped": "Camera is stopped.",
        "cam_start": "Start Camera",
        "cam_stop": "Stop Camera",
        # Screening form
        "scr_patient_info": "Patient Information",
        "scr_clinical_history": "Diabetic History",
        "scr_image_upload": "Fundus Image Upload",
        "scr_upload_btn": "Upload Image",
        "scr_take_picture_btn": "Take Picture",
        "scr_clear_btn": "Clear",
        "scr_analyze_btn": "Analyze Image",
        "scr_label_pid": "Patient ID:",
        "scr_label_name": "Name:",
        "scr_label_dob": "Date of Birth:",
        "scr_label_age": "Age:",
        "scr_label_sex": "Sex:",
        "scr_label_contact": "Contact:",
        "scr_label_phone": "Phone:",
        "scr_label_email": "Email:",
        "scr_label_address": "Address:",
        "scr_label_eye": "Eye Screened:",
        "scr_label_diabetes": "Diabetes Type:",
        "scr_label_duration": "Duration:",
        "scr_label_hba1c": "HbA1c:",
        "scr_label_notes": "Notes:",
        # Reports
        "rep_title": "DR Screening Reports",
        "rep_subtitle": "Complete diabetic retinopathy screening outcomes from locally saved records",
        "rep_refresh": "Refresh",
        "rep_export": "Export Results",
        "rep_archived": "Archived Records",
        "rep_archive_sel": "Archive Selected",
        "rep_quick_filters": "Quick Filters",
        "rep_summary": "Summary",
        "rep_all_results": "All Screening Results",
        "rep_stat_total": "Total Screenings",
        "rep_stat_unique": "Unique Patients",
        "rep_stat_no_dr": "No DR",
        "rep_stat_review": "Needs Review",
        "rep_stat_hba1c": "Avg HbA1c",
        # Users
        "usr_title": "User Management",
        "usr_table": "Users",
        "usr_log": "Activity Log",
        # Help & Support
        "hlp_title": "Help & Support",
        "hlp_subtitle": "Complete guidance for screening workflow, result safety checks, reports, and support contacts.",
        "hlp_quick_start": "Quick Start Guide",
        "hlp_quick_start_body": """
            <ul>
                <li><b>Login:</b> Use your credentials and check the <b>Patient Queue</b> for pending screenings.</li>
                <li><b>New Patient:</b> Complete intake in the <b>Screening</b> tab or pull a patient from the queue.</li>
                <li><b>Bilateral Screening:</b> Capture both Left and Right eyes in a single session using the eye toggles.</li>
                <li><b>AI Analysis:</b> Click <b>Analyze Image</b> to see DR grading, confidence, and heatmaps.</li>
                <li><b>Comparison:</b> Use the <b>Compare Screenings</b> tool to review historical trends side-by-side.</li>
                <li><b>Referral:</b> If DR is detected, use <b>Refer Patient</b> to send documentation to a specialist.</li>
                <li><b>Reports:</b> Access the <b>Reports</b> tab for comprehensive filtering and PDF exports.</li>
            </ul>
            """,
        "hlp_howto": "How-to Guides",
        "hlp_howto_body": """
            <ul>
                <li><b>Bilateral Workflow:</b> Toggle between eyes during screening. Data for each eye is saved independently but linked to the same visit.</li>
                <li><b>Using Comparison:</b> In the Patient Record view, click <b>Compare Screenings</b> to select and analyze historical screenings side-by-side.</li>
                <li><b>Patient Timeline:</b> View a visual history of all screenings and visits in the <b>Timeline</b> view for better patient management.</li>
                <li><b>Quality Check:</b> Ensure images are clear and well-lit. The system will flag non-gradable images for recapture.</li>
                <li><b>Generating Reports:</b> After saving a screening, click <b>Generate Report</b> for a professional PDF summary.</li>
                <li><b>Managing Queue:</b> Frontdesk users can add patients to the queue, which updates live for clinicians and doctors.</li>
            </ul>
            """,
        "hlp_clinical": "Advanced Clinical Features",
        "hlp_clinical_body": """
            <ul>
                <li><b>Trend Analysis:</b> The comparison tool helps in tracking DR progression over time using standardized grading.</li>
                <li><b>Decision Support:</b> AI findings (ICDR grades) are designed to augment clinical review, not replace it.</li>
                <li><b>Referral Network:</b> Manage trusted hospitals and departments in the <b>Trusted Hospitals</b> page for efficient referrals.</li>
                <li><b>Role-Based Access:</b> Doctors have full clinical authority, while Clinicians focus on screening and Frontdesk on administration.</li>
            </ul>
            """,
        "hlp_faq": "Frequently Asked Questions",
        "hlp_faq_body": """
            <ul>
                <li><b>Why can't I see the comparison button?</b> Comparison requires at least two historical screenings for the same patient.</li>
                <li><b>How do I switch eyes during screening?</b> Use the 'Left Eye' and 'Right Eye' buttons in the screening form header.</li>
                <li><b>Can I use this without internet?</b> Yes, EyeShield EMR is fully functional offline; all data is stored locally.</li>
                <li><b>What does 'High Uncertainty' mean?</b> The AI model suggests the image quality or features are ambiguous; manual clinical review is critical.</li>
                <li><b>How do I update patient info?</b> Patient demographics can be updated in the <b>Reports > View Patient</b> dialog.</li>
            </ul>
            """,
        "hlp_troubleshoot": "Troubleshooting",
        "hlp_troubleshoot_body": """
            <ul>
                <li><b>Screening won't save:</b> Ensure all required fields (Name, DOB, etc.) are filled and at least one eye is analyzed.</li>
                <li><b>Comparison layout issues:</b> Ensure your screen resolution is sufficient; use scrollbars if content overflows.</li>
                <li><b>Printer/PDF Error:</b> Check if the destination folder is writable and you have a PDF viewer installed.</li>
                <li><b>Queue not updating:</b> Click the <b>Refresh</b> button or ensure you have an active network connection for local sync.</li>
                <li><b>Forgot Password:</b> Contact your system Administrator to reset your credentials via the Users page.</li>
            </ul>
            """,
        "hlp_privacy": "Privacy & Compliance Guide",
        "hlp_privacy_body": """
            <ul>
                <li><b>Local Storage:</b> Patient data never leaves your device unless you explicitly export it.</li>
                <li><b>Activity Audit:</b> All clinical actions (analysis, saves, exports) are logged for accountability.</li>
                <li><b>Session Security:</b> Enable auto-logout in Settings to protect data when away from the workstation.</li>
                <li><b>Data Retention:</b> Use the Archive feature to manage old records according to your facility's policy.</li>
            </ul>
            """,
        "hlp_contact": "Contact Support",
        "hlp_contact_body": """
            <p>
            <b>IT/App Support:</b> support@eyeshield.local<br>
            <b>Phone:</b> +1-000-000-0000<br>
            <b>Hours:</b> Mon-Fri, 8:00 AM - 6:00 PM<br><br>
            <b>When contacting support, include:</b><br>
            User role, patient ID (if applicable), page name, exact error message, and time of incident.
            </p>
            """,
    },
}


def get_pack(language: str) -> dict:
    """Return the translation pack for the given language, defaulting to English."""
    return TRANSLATIONS.get(language, TRANSLATIONS["English"])
