from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
PATIENT_RECORDS_DB = PROJECT_ROOT / "data" / "patient_records.db"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(str(PROJECT_ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from PySide6.QtWidgets import QApplication, QMessageBox

import emr_service as emr
import screening_form as screening_form_module
from doctor_diagnosis_form import DoctorDiagnosisForm


TEST_IMAGE = PROJECT_ROOT / "app" / "stored_images" / "ES-260421-SGGPJ" / "20260421_223222_right_eye_source.jpg"


class _FakeSignal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self._slots):
            slot(*args)


class _FakeInferenceWorker:
    def __init__(self, image_path: str):
        self._image_path = image_path
        self._running = False
        self.result_ready = _FakeSignal()
        self.finished = _FakeSignal()
        self.error = _FakeSignal()
        self.ungradable = _FakeSignal()

    def start(self) -> None:
        self._running = True
        confidence = "Confidence: 88.0%  |  Uncertainty: 12.0%"
        self.result_ready.emit("Moderate DR", confidence)
        self.finished.emit("Moderate DR", confidence, "")
        self._running = False

    def isRunning(self) -> bool:
        return self._running


def _queue_case(note_fragment: str) -> tuple[dict, int]:
    rows = emr.list_queue_rows()
    for row in rows:
        note = str(row.get("notes") or "")
        if note_fragment.lower() in note.lower():
            patient = emr.get_patient(int(row["patient_id"])) or {}
            if patient:
                return patient, int(row["queue_id"])
    raise RuntimeError(f"Could not find queued case containing note: {note_fragment}")


def _wait_for_worker(page, timeout_s: float = 45.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        worker = getattr(page, "_worker", None)
        QApplication.processEvents()
        if worker is None or not worker.isRunning():
            return
        time.sleep(0.05)
    raise TimeoutError("Inference worker did not finish before timeout.")


def _prepare_form(patient: dict, queue_id: int) -> DoctorDiagnosisForm:
    form = DoctorDiagnosisForm()
    form.username = "Kelcy"
    form.role = "clinician"
    form.display_name = "Kelcy"
    form.start_for_patient(patient, queue_entry_id=queue_id)
    page = form.screening
    page.current_image = str(TEST_IMAGE)
    page._set_preview_image(str(TEST_IMAGE))
    if hasattr(page, "btn_analyze"):
        page.btn_analyze.setEnabled(True)
    return form


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _patient_record_count(patient_code: str) -> int:
    conn = sqlite3.connect(PATIENT_RECORDS_DB)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM patient_records WHERE patient_id = ?", (patient_code,))
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def _smoke_single_eye_save() -> str:
    print("single-eye: loading queued case", flush=True)
    patient, queue_id = _queue_case("single-eye")
    form = _prepare_form(patient, queue_id)
    page = form.screening

    before_count = _patient_record_count(str(patient.get("patient_code") or ""))
    print("single-eye: analyze", flush=True)
    page.open_results_window()
    _assert(page.stacked_widget.currentIndex() == 1, "Analyze should switch diagnosis tab to screening results.")
    _wait_for_worker(page)
    _assert(str(page.last_result_class) not in {"Pending", "Analyzing…", ""}, "Inference did not populate a result.")

    page.results_page._accept_ai_classification()
    current_eye = str(page.p_eye.currentText() or "").strip()
    opposite_eye = "Left Eye" if current_eye == "Right Eye" else "Right Eye"
    original_find_existing = page._find_existing_eye_record
    page._find_existing_eye_record = lambda pid, eye: {"id": 999999} if eye == opposite_eye else None
    try:
        print("single-eye: save record", flush=True)
        result = page.save_screening(reset_after=False)
    finally:
        page._find_existing_eye_record = original_find_existing
    print(f"single-eye: save result {result}", flush=True)

    _assert(isinstance(result, dict) and result.get("status") in {"saved", "replaced"}, "Save Record should persist the result.")
    _assert(page.stacked_widget.currentIndex() == 0, "Save Record should return to the diagnosis window.")
    after_count = _patient_record_count(str(patient.get("patient_code") or ""))
    _assert(after_count == before_count + 1, "Save Record should add one patient_records row for screening history.")
    return f"single-eye save passed ({patient.get('patient_code')})"


def _smoke_screen_other_eye() -> str:
    print("bilateral: loading queued case", flush=True)
    patient, queue_id = _queue_case("bilateral")
    form = _prepare_form(patient, queue_id)
    page = form.screening

    print("bilateral: analyze", flush=True)
    page.open_results_window()
    _assert(page.stacked_widget.currentIndex() == 1, "Analyze should switch diagnosis tab to screening results.")
    _wait_for_worker(page)
    _assert(str(page.last_result_class) not in {"Pending", "Analyzing…", ""}, "Inference did not populate a result.")

    page.results_page._accept_ai_classification()
    current_eye = str(page.p_eye.currentText() or "").strip()
    opposite_eye = "Left Eye" if current_eye == "Right Eye" else "Right Eye"
    original_find_existing = page._find_existing_eye_record
    page._find_existing_eye_record = lambda pid, eye: {"id": 999999} if eye == opposite_eye else None
    try:
        print("bilateral: save first eye", flush=True)
        result = page.save_screening(reset_after=False)
    finally:
        page._find_existing_eye_record = original_find_existing
    print(f"bilateral: save result {result}", flush=True)

    _assert(isinstance(result, dict) and result.get("status") in {"saved", "replaced"}, "First-eye save should succeed before switching eyes.")
    print("bilateral: switch eye", flush=True)
    page.screen_other_eye()
    _assert(page.stacked_widget.currentIndex() == 0, "Screen other eye should return to the diagnosis window.")
    _assert(str(page.p_eye.currentText() or "").strip() == opposite_eye, "Diagnosis window should switch to the opposite eye.")
    _assert(str(page.p_name.text() or "").strip() != "", "Patient demographics should remain populated when switching eyes.")
    return f"screen-other-eye passed ({patient.get('patient_code')})"


def main() -> None:
    if not TEST_IMAGE.exists():
        raise SystemExit(f"Missing test image: {TEST_IMAGE}")

    app = QApplication.instance() or QApplication([])
    screening_form_module._InferenceWorker = _FakeInferenceWorker
    QMessageBox.information = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    QMessageBox.warning = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    QMessageBox.critical = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    results = [
        _smoke_single_eye_save(),
        _smoke_screen_other_eye(),
    ]
    QApplication.processEvents()
    print("Diagnosis flow smoke test results:")
    for item in results:
        print(f"- {item}")
    app.quit()


if __name__ == "__main__":
    main()
