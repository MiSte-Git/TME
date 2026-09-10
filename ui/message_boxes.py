from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget


class _MouseOnlyErrorBox(QMessageBox):
    """Verhindert, dass versehentliches Tippen einen Fehlerdialog schließt."""

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        event.accept()


def show_copyable_error(parent: QWidget, title: str, message: str) -> None:
    """Zeigt einen Fehler mit einer sichtbaren Kopiermöglichkeit."""
    box = _MouseOnlyErrorBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    box.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    copy_button = QPushButton(parent.tr("Fehler kopieren"))
    box.addButton(copy_button, QMessageBox.ButtonRole.ActionRole)
    box.setProperty("copyButtonInstalled", True)
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()
    if box.clickedButton() is copy_button:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(message)


class _CopyableErrorFilter(QObject):
    """Ergänzt auch bestehende QMessageBox.critical-Aufrufe appweit."""

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt override)
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QMessageBox)
            and watched.icon() == QMessageBox.Icon.Critical
            and not watched.property("copyButtonInstalled")
        ):
            watched.setProperty("copyButtonInstalled", True)
            watched.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            button = QPushButton(watched.tr("Fehler kopieren"), watched)
            watched.addButton(button, QMessageBox.ButtonRole.ActionRole)
            button.clicked.connect(
                lambda _checked=False, box=watched: QApplication.clipboard().setText(
                    box.text()
                )
            )
        return super().eventFilter(watched, event)


def install_copyable_error_messages(app: QApplication) -> None:
    """Macht alle kritischen Fehlerdialoge der Anwendung kopierbar."""
    error_filter = _CopyableErrorFilter(app)
    app.installEventFilter(error_filter)
    # Explizite Referenz für die gesamte Laufzeit der Anwendung.
    app._copyable_error_filter = error_filter
