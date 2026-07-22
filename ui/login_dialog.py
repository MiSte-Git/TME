from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QWidget, QMessageBox,
)

from credentials import get_telegram_credentials, save_telegram_credentials
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
    need_password = Signal(int, str)

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

    def password_callback(self, hint: Optional[str] = None) -> str:
        self._password_attempt += 1
        self.need_password.emit(self._password_attempt, hint or "")
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
    """Schrittweiser Telegram-Login (ggf. zuerst API-Zugangsdaten -> dann
    Telefonnummer -> Code -> ggf. 2FA-Passwort) direkt im UI, als Ersatz für
    scripts/telegram_login.py auf der Konsole. Der eigentliche Login läuft in
    einem Worker-Thread (siehe LoginWorker), damit client.start() die
    Qt-Event-Loop nicht blockiert.

    need_credentials=True zeigt vorgeschaltet eine Seite für API ID/API Hash
    (TelegramCredentialsMissing - komplett fehlende Zugangsdaten); der
    eigentliche Login-Worker startet erst, nachdem diese gespeichert wurden.
    Bei need_credentials=False (TelegramSessionInvalid - Zugangsdaten
    vorhanden, nur Session ungültig) startet der Worker sofort wie bisher."""

    PAGE_CREDENTIALS = 0
    PAGE_PHONE = 1
    PAGE_CODE = 2
    PAGE_PASSWORD = 3
    PAGE_WAIT = 4
    PAGE_DONE = 5
    PAGE_ERROR = 6

    def __init__(self, parent=None, need_credentials: bool = False) -> None:
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

        page_credentials = QWidget()
        v = QVBoxLayout(page_credentials)
        lbl_credentials_hint = QLabel(self.tr(
            "Telegram API-Zugangsdaten fehlen. Auf <a href='https://my.telegram.org'>my.telegram.org</a> "
            "unter \"API development tools\" erstellst du eine Anwendung und erhältst API ID und API Hash:"
        ))
        lbl_credentials_hint.setWordWrap(True)
        lbl_credentials_hint.setOpenExternalLinks(True)
        v.addWidget(lbl_credentials_hint)
        v.addWidget(QLabel(self.tr("API ID:")))
        self.api_id_edit = QLineEdit()
        self.api_id_edit.setPlaceholderText(self.tr("z.B. 12345678"))
        v.addWidget(self.api_id_edit)
        v.addWidget(QLabel(self.tr("API Hash:")))
        self.api_hash_edit = QLineEdit()
        v.addWidget(self.api_hash_edit)
        self.stack.addWidget(page_credentials)

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
        # Von Telegram hinterlegter Passwort-Hinweis (account.password.hint,
        # siehe pipeline/telegram_login.py), analog zur offiziellen App -
        # standardmäßig leer/ausgeblendet, nur befüllt wenn Telegram tatsächlich
        # einen Hinweis liefert (siehe _on_need_password).
        self.lbl_password_telegram_hint = QLabel("")
        self.lbl_password_telegram_hint.setWordWrap(True)
        self.lbl_password_telegram_hint.setStyleSheet("color: gray; font-style: italic;")
        self.lbl_password_telegram_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_password_telegram_hint.setVisible(False)
        v.addWidget(self.lbl_password_telegram_hint)
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
        self.lbl_done.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.lbl_done)
        self.stack.addWidget(page_done)

        page_error = QWidget()
        v = QVBoxLayout(page_error)
        # Kann technische Details enthalten (z.B. die ApiIdInvalidError-
        # Meldung mit ENV-/credentials.json-Hinweisen) - muss zur
        # Fehlersuche kopierbar sein, nicht nur lesbar.
        self.lbl_error = QLabel("")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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

        if need_credentials:
            self.stack.setCurrentIndex(self.PAGE_CREDENTIALS)
            self.api_id_edit.setFocus()
        else:
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

    def _on_need_password(self, attempt: int, hint: str) -> None:
        if attempt > 1:
            self.password_edit.clear()
            self.lbl_password_hint.setText(self.tr("Ungültiges Passwort. Bitte erneut eingeben:"))
        if hint:
            self.lbl_password_telegram_hint.setText(self.tr("Hinweis: {hint}").format(hint=hint))
            self.lbl_password_telegram_hint.setVisible(True)
        else:
            self.lbl_password_telegram_hint.clear()
            self.lbl_password_telegram_hint.setVisible(False)
        self.stack.setCurrentIndex(self.PAGE_PASSWORD)
        self.btn_next.setEnabled(True)
        self.password_edit.setFocus()

    def _on_credentials_submit(self) -> None:
        api_id_text = self.api_id_edit.text().strip()
        api_hash_text = self.api_hash_edit.text().strip()
        if not api_id_text or not api_hash_text:
            QMessageBox.warning(self, self.tr("Telegram-Login"), self.tr("Bitte API ID und API Hash eingeben."))
            return
        try:
            api_id = int(api_id_text)
        except ValueError:
            QMessageBox.warning(self, self.tr("Telegram-Login"), self.tr("API ID muss eine Zahl sein."))
            return
        try:
            save_telegram_credentials(api_id, api_hash_text)
        except Exception as exc:
            QMessageBox.critical(
                self, self.tr("Telegram-Login"),
                self.tr("Speichern fehlgeschlagen: {err}").format(err=exc),
            )
            return
        logger.info("Telegram-API-Zugangsdaten gespeichert, fahre mit Login fort.")
        try:
            _, _, cfg_phone = get_telegram_credentials()
        except Exception:
            cfg_phone = None
        if cfg_phone:
            self.phone_edit.setText(cfg_phone)
        self.stack.setCurrentIndex(self.PAGE_PHONE)
        self._start_login()

    def _on_next_clicked(self) -> None:
        idx = self.stack.currentIndex()
        if idx == self.PAGE_CREDENTIALS:
            self._on_credentials_submit()
            return
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
