"""
Shared UI feedback helpers (success/error/warn/loading) for consistent UX.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget


def show_success(parent: QWidget, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def show_error(parent: QWidget, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def show_warning(parent: QWidget, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def confirm(parent: QWidget, title: str, message: str, *, yes_text: str = "Yes", no_text: str = "No") -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    yes = box.addButton(yes_text, QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(no_text, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)
    box.exec()
    return box.clickedButton() == yes


@contextmanager
def loading_state(
    buttons: Iterable[QPushButton],
    *,
    loading_text: str = "Processing…",
):
    btns = [b for b in buttons if isinstance(b, QPushButton)]
    prior = [(b, b.text(), b.isEnabled()) for b in btns]
    for b in btns:
        b.setEnabled(False)
        if loading_text:
            b.setText(loading_text)
            b.setCursor(Qt.CursorShape.BusyCursor)
    try:
        yield
    finally:
        for b, text, enabled in prior:
            b.setText(text)
            b.setEnabled(enabled)
            b.setCursor(Qt.CursorShape.ArrowCursor)

