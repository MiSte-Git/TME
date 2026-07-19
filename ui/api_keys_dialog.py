from __future__ import annotations

from typing import Dict

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox,
)

from credentials import (
    get_deepl_api_key,
    get_google_translate_api_key,
    get_openai_api_key,
    get_provider_api_key_source,
    save_deepl_api_key,
    save_google_translate_api_key,
    save_openai_api_key,
)
from pipeline.logging_setup import get_logger

logger = get_logger(__name__)

_PROVIDERS = [
    ("deepl", "DeepL"),
    ("google", "Google Translate"),
    ("openai", "ChatGPT (OpenAI)"),
]

_SAVE_FUNCS = {
    "deepl": save_deepl_api_key,
    "google": save_google_translate_api_key,
    "openai": save_openai_api_key,
}

_GET_FUNCS = {
    "deepl": get_deepl_api_key,
    "google": get_google_translate_api_key,
    "openai": get_openai_api_key,
}


class ApiKeysDialog(QDialog):
    """Verwaltung der Übersetzungs-Provider-API-Keys (DeepL/Google/OpenAI).

    Speichert bevorzugt über das OS-Keyring (siehe credentials.py:
    save_provider_api_key); zeigt pro Provider an, woher der aktuell aktive
    Key stammt (Umgebungsvariable / OS-Keyring / unverschlüsseltes
    credentials.json-Fallback / nicht gesetzt).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("API-Keys verwalten"))
        self.setModal(True)
        self.setMinimumWidth(480)

        outer = QVBoxLayout(self)
        self.lbl_intro = QLabel(self.tr(
            "Übersetzungs-Provider-Keys werden nach Möglichkeit verschlüsselt im "
            "OS-Keyring gespeichert (Windows Credential Locker / macOS Keychain / "
            "Secret Service unter Linux). Ist dort kein Backend verfügbar, wird als "
            "Fallback credentials.json im Klartext verwendet - die Quellenanzeige "
            "unten zeigt, welcher Fall gerade zutrifft."
        ))
        self.lbl_intro.setWordWrap(True)
        outer.addWidget(self.lbl_intro)

        grid = QGridLayout()
        grid.setSpacing(8)
        self._edits: Dict[str, QLineEdit] = {}
        self._status_labels: Dict[str, QLabel] = {}
        # Zuletzt gespeicherter/vorbefüllter Wert je Provider - dient dazu, ein
        # unverändert gelassenes Feld beim Klick auf "Speichern" als No-Op zu
        # erkennen, statt den Key unnötig neu zu speichern/zu rotieren.
        self._original_values: Dict[str, str] = {}

        for row, (provider, display_name) in enumerate(_PROVIDERS):
            grid.addWidget(QLabel(display_name), row, 0)

            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText(self.tr("Neuen Key eingeben zum Ändern…"))
            try:
                current_value = _GET_FUNCS[provider]() or ""
            except Exception:
                current_value = ""
            edit.setText(current_value)
            self._original_values[provider] = current_value
            self._edits[provider] = edit

            field_row = QHBoxLayout()
            field_row.setContentsMargins(0, 0, 0, 0)
            field_row.setSpacing(4)
            field_row.addWidget(edit, 1)
            eye_btn = QPushButton("👁")
            eye_btn.setCheckable(True)
            eye_btn.setFixedWidth(32)
            eye_btn.setToolTip(self.tr("Wert anzeigen/verbergen"))
            eye_btn.setEnabled(bool(current_value))
            eye_btn.toggled.connect(
                lambda checked, e=edit: e.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
                )
            )

            def _on_field_text_changed(text: str, e: QLineEdit = edit, b: QPushButton = eye_btn) -> None:
                has_text = bool(text)
                b.setEnabled(has_text)
                if not has_text:
                    b.setChecked(False)

            edit.textChanged.connect(_on_field_text_changed)
            field_row.addWidget(eye_btn)
            grid.addLayout(field_row, row, 1)

            btn = QPushButton(self.tr("Speichern"))
            btn.clicked.connect(lambda _checked=False, p=provider: self._on_save_clicked(p))
            grid.addWidget(btn, row, 2)
            status = QLabel("")
            status.setWordWrap(True)
            grid.addWidget(status, row, 3)
            self._status_labels[provider] = status
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        outer.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_close = QPushButton(self.tr("Schließen"))
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        outer.addLayout(btn_row)

        self._refresh_all_status()

    def _source_label_text(self, source: str) -> str:
        if source == "env":
            return self.tr("Quelle: Umgebungsvariable")
        if source == "keyring":
            return self.tr("Quelle: OS-Keyring (sicher gespeichert)")
        if source == "credentials_json":
            return self.tr("Quelle: credentials.json (⚠ Klartext, nicht verschlüsselt)")
        return self.tr("Kein Key hinterlegt")

    def _refresh_all_status(self) -> None:
        for provider, _label in _PROVIDERS:
            self._refresh_status(provider)

    def _refresh_status(self, provider: str) -> None:
        source = get_provider_api_key_source(provider)
        self._status_labels[provider].setText(self._source_label_text(source))

    def _on_save_clicked(self, provider: str) -> None:
        edit = self._edits[provider]
        value = edit.text().strip()
        original = self._original_values.get(provider, "")
        if value == original:
            # Unverändertes (ggf. vorbefülltes) Feld - nichts zu tun, insbesondere
            # keinen bestehenden Key unnötig neu speichern/rotieren.
            QMessageBox.information(
                self, self.tr("API-Keys"),
                self.tr("Feld wurde nicht geändert - nichts zu speichern."),
            )
            return
        if not value:
            QMessageBox.warning(self, self.tr("API-Keys"), self.tr("Bitte einen Key eingeben."))
            return
        try:
            backend = _SAVE_FUNCS[provider](value)
        except Exception as exc:
            logger.error("Speichern des API-Keys für '%s' fehlgeschlagen: %s", provider, exc)
            QMessageBox.critical(
                self, self.tr("API-Keys"),
                self.tr("Speichern fehlgeschlagen: {err}").format(err=exc),
            )
            return
        self._original_values[provider] = value
        self._refresh_status(provider)
        logger.info("API-Key für '%s' gespeichert (Backend: %s).", provider, backend)
        if backend == "credentials_json_fallback":
            logger.warning(
                "API-Key für '%s' liegt unverschlüsselt in credentials.json (kein OS-Keyring verfügbar).",
                provider,
            )
            QMessageBox.warning(
                self,
                self.tr("API-Keys"),
                self.tr(
                    "Kein OS-Keyring verfügbar - der Key wurde stattdessen unverschlüsselt "
                    "in credentials.json gespeichert."
                ),
            )
