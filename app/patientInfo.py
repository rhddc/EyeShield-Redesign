from PySide6.QtWidgets import QMessageBox
from patient_timeline_dialog import PatientTimelineDialog
from auth import UserManager

def handle_patient_info_double_click(parent, get_selected_record, fetch_patient_timeline_records, username,
                                    on_follow_up=None, on_view_report=None, on_compare=None, on_export=None):
    """Show the patient overview — inline inside the parent window if supported,
    otherwise fall back to a modal dialog."""
    record = get_selected_record()
    if not record:
        return

    patient_id = str(record.get("patient_id") or "").strip()
    if not patient_id:
        QMessageBox.warning(parent, "Patient Timeline", "Unable to retrieve patient history.")
        return

    timeline_records = fetch_patient_timeline_records(patient_id)
    if not timeline_records:
        QMessageBox.warning(parent, "Patient Timeline", "Unable to load patient timeline.")
        return

    latest_record = timeline_records[-1]
    UserManager.add_activity_log(
        username,
        f"RECORD_OPENED patient_id={patient_id}; record_id={latest_record.get('id')}; source=reports_timeline",
    )

    # Prefer inline replacement when the parent window supports it
    if hasattr(parent, "_show_patient_overview"):
        parent._show_patient_overview(latest_record, timeline_records)
        return

    # Fallback: open as a floating dialog (e.g. when called from other pages)
    dialog = PatientTimelineDialog(
        latest_record,
        timeline_records,
        on_follow_up=on_follow_up,
        on_view_report=on_view_report,
        on_compare=on_compare,
        on_export=on_export,
    )
    # Wrap in a QDialog shell so we can exec() it
    from PySide6.QtWidgets import QDialog, QVBoxLayout
    shell = QDialog(parent)
    shell.setWindowTitle("Patient Overview")
    shell.setModal(True)
    from PySide6.QtGui import QGuiApplication
    avail = QGuiApplication.primaryScreen().availableGeometry()
    shell.resize(min(860, int(avail.width() * 0.88)), min(760, int(avail.height() * 0.86)))
    shell.move(
        avail.left() + (avail.width()  - shell.width())  // 2,
        avail.top()  + (avail.height() - shell.height()) // 2,
    )
    lay = QVBoxLayout(shell)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(dialog)
    dialog.back_requested.connect(shell.accept)
    shell.exec()
