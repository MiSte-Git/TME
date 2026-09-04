"""tkinter-unabhängiger Zustand und Orchestrierung für den Bootstrapper-
Assistenten.

Aus bootstrap/app.py herausgezogen, damit diese Logik - Sprachwahl,
Modus-Wahl, Ansteuerung von bootstrap/installer.py, Zugangsdaten-Schritt,
App-Start - ohne echtes Display testbar ist. tkinter/Tk braucht eines, das
weder auf jeder Entwicklungsmaschine noch auf CI-Runnern zuverlässig
vorhanden ist; bootstrap/app.py ist eine dünne Widget-Schicht darüber und
bewusst nicht direkt unit-getestet (stattdessen über den PyInstaller-Build je
Betriebssystem in CI abgedeckt - siehe .github/workflows/build-bootstrap.yml).

Text-Lookup hier nutzt bootstrap/wizard_text.py's CATALOGUES/DE direkt
(statisch in die Bootstrapper-Executable eingebaut) - die eigenen Bildschirme
des Assistenten (Sprachwahl, Modus-Wahl, ...) müssen rendern, bevor
überhaupt etwas heruntergeladen wurde, ihre Texte können also nirgendwoher
kommen als aus einer mit dem Bootstrapper selbst mitgelieferten Kopie.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from bootstrap.wizard_text import CATALOGUES, DE

from bootstrap import credentials_step, installer, paths, system_lang


class BootstrapController:
    def __init__(self, venv_dir: Path | None = None, app_source_dir: Path | None = None) -> None:
        self.venv_dir = venv_dir if venv_dir is not None else paths.venv_dir()
        self.app_source_dir = app_source_dir if app_source_dir is not None else paths.app_source_dir()
        self.language: str = system_lang.detect_system_language()
        self.mode: Optional[installer.InstallMode] = None
        self.install_error: Optional[str] = None
        self.venv_python: Optional[Path] = None

    # --- Sprache -----------------------------------------------------------

    def set_language(self, language: str) -> None:
        if language in CATALOGUES:
            self.language = language

    def text(self, key: str, **values: object) -> str:
        catalogue = CATALOGUES.get(self.language, DE)
        template = catalogue.get(key, DE.get(key, key))
        return template.format(**values) if values else template

    def write_language_marker(self) -> Path:
        """Schreibt die JSON-Markierungsdatei für einen künftigen ersten
        App-Start (siehe paths.language_marker_file()-Docstring - ui/app.py
        liest sie aktuell noch nicht)."""
        marker = paths.language_marker_file()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"language": self.language}))
        return marker

    # --- Modus ---------------------------------------------------------

    def set_mode(self, mode: installer.InstallMode) -> None:
        self.mode = mode

    # --- Installation --------------------------------------------------

    def run_install(
        self,
        dev_source_override: str | None = None,
        progress_cb: installer.ProgressCallback | None = None,
    ) -> Path:
        if self.mode is None:
            raise RuntimeError("set_mode() muss vor run_install() aufgerufen werden")
        self.install_error = None
        try:
            self.venv_python = installer.run_install(
                self.venv_dir,
                self.app_source_dir,
                self.mode,
                dev_source_override=dev_source_override,
                progress_cb=progress_cb,
            )
        except installer.InstallError as exc:
            self.install_error = str(exc)
            raise
        return self.venv_python

    # --- Zugangsdaten: Telegram ------------------------------------------

    def telegram_status(self) -> str:
        return credentials_step.telegram_status(self.app_source_dir)

    def save_telegram_credentials(self, api_id: str, api_hash: str, phone: str | None = None) -> None:
        credentials_step.save_telegram_credentials(self.app_source_dir, api_id, api_hash, phone)

    def open_telegram_signup_page(self) -> bool:
        return credentials_step.open_telegram_signup_page()

    # --- Zugangsdaten: Übersetzungs-Provider -----------------------------

    def list_providers(self) -> list[str]:
        return credentials_step.list_providers(self.app_source_dir)

    def provider_status(self, provider: str) -> str:
        return credentials_step.provider_status(self.app_source_dir, provider)

    def save_provider_credential(self, provider: str, value: str) -> str:
        return credentials_step.save_provider_credential(self.app_source_dir, provider, value)

    def open_signup_page(self, provider: str) -> bool:
        return credentials_step.open_signup_page(provider)

    # --- Abschluss -----------------------------------------------------

    def launch_app(self) -> subprocess.Popen:
        venv_python = self.venv_python if self.venv_python is not None else paths.venv_python(self.venv_dir)
        return subprocess.Popen(
            [str(venv_python), "-m", "ui.app"], cwd=str(self.app_source_dir)
        )
