from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QWidget, QMessageBox,
)

from credentials import get_telegram_credentials
from pipeline.telegram_login import (
    LoginCancelled,
    TelegramLoginResult,
    perform_telegram_login,
)
from pipeline.logging_setup import get_logger

logger = get_logger(__name__)


class TelegramLoginBridge(QObject):
    """Verbindet die (im Login-Worker-Thread blockierend aufgerufenen)
    Telethon-Callbacks mit dem Dialog im GUI-Thread.

    Jede *_callback()-Methode wird von Telethon im Worker-Thread aufgerufen,
    emittiert ein Qt-Signal (dadurch automatisch in den GUI-Thread eingereiht)
    und blockiert anschließend nur den Worker-Thread, bis der Dialog per
    provide()/cancel() eine Antwort liefert - die Qt-Event-Loop bleibt frei.
    """

    need_phone = Signal(int)
    need_code = Signal(int)
    need_password = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._value: Optional[str] = None
        self._event = threading.Event()
        self._cancelled = False
        self._phone_attempt = 0
        self._code_attempt = 0
        self._password_attempt = 0

    def cancel(self) -> None:
        self._cancelled = True
        self._value = None
        self._event.set()

    def provide(self, value: str) -> None:
        self._value = value
        self._event.set()

    def _wait_for_value(self) -> str:
        self._event.wait()
        self._event.clear()
        if self._cancelled:
            raise LoginCancelled("Login vom Nutzer abgebrochen.")
        return self._value or ""

    def phone_callback(self) -> str:
        self._phone_attempt += 1
        self.need_phone.emit(self._phone_attempt)
        return self._wait_for_value()

    def code_callback(self) -> str:
        self._code_attempt += 1
        self.need_code.emit(self._code_attempt)
        return self._wait_for_value()

    def password_callback(self) -> str:
        self._password_attempt += 1
        self.need_password.emit(self._password_attempt)
        return self._wait_for_value()


class LoginWorker(QObject):
    finished = Signal(object)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, bridge: TelegramLoginBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def run(self) -> None:
        try:
            result = perform_telegram_login(
                phone_callback=self._bridge.phone_callback,
                code_callback=self._bridge.code_callback,
                password_callback=self._bridge.password_callback,
            )
            self.finished.emit(result)
        except LoginCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class LoginDialog(QDialog):
    """Schrittweiser Telegram-Login (Telefonnummer -> Code -> ggf. 2FA-Passwort)
    direkt im UI, als Ersatz für scripts/telegram_login.py auf der Konsole.
    Der eigentliche Login läuft in einem Worker-Thread (siehe LoginWorker),
    damit client.start() die Qt-Event-Loop nicht blockiert."""

    PAGE_PHONE = 0
    PAGE_CODE = 1
    PAGE_PASSWORD = 2
    PAGE_WAIT = 3
    PAGE_DONE = 4
    PAGE_ERROR = 5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Telegram-Login"))
        self.setModal(True)
        self.setMinimumWidth(380)
        self.login_result: Optional[TelegramLoginResult] = None

        self._bridge = TelegramLoginBridge()
        self._thread: Optional[QThread] = None
        self._worker: Optional[LoginWorker] = None

        outer = QVBoxLayout(self)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        page_phone = QWidget()
        v = QVBoxLayout(page_phone)
        self.lbl_phone_hint = QLabel(self.tr("Telefonnummer (inkl. Ländervorwahl):"))
        self.lbl_phone_hint.setWordWrap(True)
        v.addWidget(self.lbl_phone_hint)
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("+49 151 23456789")
        try:
            _, _, cfg_phone = get_telegram_credentials()
        except Exception:
            cfg_phone = None
        if cfg_phone:
            self.phone_edit.setText(cfg_phone)
        v.addWidget(self.phone_edit)
        self.stack.addWidget(page_phone)

        page_code = QWidget()
        v = QVBoxLayout(page_code)
        self.lbl_code_hint = QLabel(self.tr("Bitte den per Telegram gesendeten Bestätigungscode eingeben:"))
        self.lbl_code_hint.setWordWrap(True)
        v.addWidget(self.lbl_code_hint)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText(self.tr("Code"))
        v.addWidget(self.code_edit)
        self.stack.addWidget(page_code)

        page_password = QWidget()
        v = QVBoxLayout(page_password)
        self.lbl_password_hint = QLabel(
            self.tr("Dieses Konto hat die Zwei-Faktor-Authentifizierung aktiviert. Bitte Passwort eingeben:")
        )
        self.lbl_password_hint.setWordWrap(True)
        v.addWidget(self.lbl_password_hint)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        v.addWidget(self.password_edit)
        self.stack.addWidget(page_password)

        page_wait = QWidget()
        v = QVBoxLayout(page_wait)
        self.lbl_wait = QLabel(self.tr("Bitte warten…"))
        self.lbl_wait.setWordWrap(True)
        v.addWidget(self.lbl_wait)
        self.stack.addWidget(page_wait)

        page_done = QWidget()
        v = QVBoxLayout(page_done)
        self.lbl_done = QLabel("")
        self.lbl_done.setWordWrap(True)
        v.addWidget(self.lbl_done)
        self.stack.addWidget(page_done)

        page_error = QWidget()
        v = QVBoxLayout(page_error)
        self.lbl_error = QLabel("")
        self.lbl_error.setWordWrap(True)
        v.addWidget(self.lbl_error)
        self.stack.addWidget(page_error)

        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton(self.tr("Abbrechen"))
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_next = QPushButton(self.tr("Weiter"))
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._on_next_clicked)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_next)
        outer.addLayout(btn_row)

        self.stack.setCurrentIndex(self.PAGE_PHONE)
        self._start_login()

    def _start_login(self) -> None:
        self._thread = QThread(self)
        self._worker = LoginWorker(self._bridge)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._bridge.need_phone.connect(self._on_need_phone)
        self._bridge.need_code.connect(self._on_need_code)
        self._bridge.need_password.connect(self._on_need_password)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.error.connect(self._worker.deleteLater)
        self._worker.cancelled.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_need_phone(self, attempt: int) -> None:
        if attempt > 1:
            self.phone_edit.clear()
            self.lbl_phone_hint.setText(
                self.tr("Ungültige Telefonnummer. Bitte im internationalen Format eingeben (z.B. +49...):")
            )
        self.stack.setCurrentIndex(self.PAGE_PHONE)
        self.btn_next.setEnabled(True)
        self.phone_edit.setFocus()

    def _on_need_code(self, attempt: int) -> None:
        if attempt > 1:
            self.code_edit.clear()
            self.lbl_code_hint.setText(self.tr("Ungültiger Code. Bitte erneut eingeben:"))
        self.stack.setCurrentIndex(self.PAGE_CODE)
        self.btn_next.setEnabled(True)
        self.code_edit.setFocus()

    def _on_need_password(self, attempt: int) -> None:
        if attempt > 1:
            self.password_edit.clear()
            self.lbl_password_hint.setText(self.tr("Ungültiges Passwort. Bitte erneut eingeben:"))
        self.stack.setCurrentIndex(self.PAGE_PASSWORD)
        self.btn_next.setEnabled(True)
        self.password_edit.setFocus()

    def _on_next_clicked(self) -> None:
        idx = self.stack.currentIndex()
        if idx == self.PAGE_PHONE:
            value = self.phone_edit.text().strip()
            if not value:
                QMessageBox.warning(self, self.tr("Telegram-Login"), self.tr("Bitte eine Telefonnummer eingeben."))
                return
        elif idx == self.PAGE_CODE:
            value = self.code_edit.text().strip()
            if not value:
                QMessageBox.warning(self, self.tr("Telegram-Login"), self.tr("Bitte den Bestätigungscode eingeben."))
                return
        elif idx == self.PAGE_PASSWORD:
            value = self.password_edit.text()
        else:
            return
        self.btn_next.setEnabled(False)
        self.stack.setCurrentIndex(self.PAGE_WAIT)
        self._bridge.provide(value)

    def _on_cancel_clicked(self) -> None:
        self._bridge.cancel()
        self.reject()

    def _on_finished(self, result: object) -> None:
        self.login_result = result  # type: ignore[assignment]
        username = f"@{result.username}" if getattr(result, "username", None) else self.tr("(kein Username gesetzt)")
        self.lbl_done.setText(
            self.tr("Erfolgreich eingeloggt als {user} (ID {uid}).\nDieses Fenster schließt sich automatisch…")
            .format(user=username, uid=getattr(result, "user_id", "?"))
        )
        logger.info("Telegram-Login erfolgreich (user_id=%s).", getattr(result, "user_id", "?"))
        self.stack.setCurrentIndex(self.PAGE_DONE)
        self.btn_next.setVisible(False)
        self.btn_cancel.setText(self.tr("Schließen"))
        QTimer.singleShot(1500, self.accept)

    def _on_error(self, message: str) -> None:
        logger.error("Telegram-Login fehlgeschlagen: %s", message)
        self.lbl_error.setText(self.tr("Login fehlgeschlagen:") + "\n" + message)
        self.stack.setCurrentIndex(self.PAGE_ERROR)
        self.btn_next.setVisible(False)
        self.btn_cancel.setText(self.tr("Schließen"))

    def _on_cancelled(self) -> None:
        logger.info("Telegram-Login vom Nutzer abgebrochen.")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._thread is not None and self._thread.isRunning():
            self._bridge.cancel()
        super().closeEvent(event)
