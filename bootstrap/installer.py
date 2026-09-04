"""Orchestriert Stufe 2: venv-Erstellung, Installation der Abhängigkeiten,
App-Code-Download und den Eintrag im Anwendungsmenü.

Reihenfolge ist Code -> venv -> Abhängigkeiten -> Menüeintrag: der App-Code
muss vor dem Lesen seiner requirements*.txt-Dateien vorhanden sein, die venv
muss existieren bevor irgendetwas hineininstalliert werden kann, und der
Menüeintrag ist erst sinnvoll, wenn alles, worauf er zeigt (venv_python,
app_source_dir), existiert.

Abhängigkeiten kommen ausschließlich von PyPI über die requirements*.txt der
App - `pip install -r requirements.txt` hier ist derselbe Befehl, den auch
ein:e Entwickler:in von Hand ausführt (siehe README.md "Voraussetzungen"),
nur von der grafischen Oberfläche aus gestartet.

Anders als das Vorbild in PDF-Translator (dessen requirements-gpu.txt einen
CUDA-Generation-passenden Wheel-Index von pytorch.org braucht) installiert
TMEs requirements-stt.txt (torch + openai-whisper) ohne speziellen
Index-URL - PyPIs Standard-Wheels für torch bringen unter Windows/Linux
bereits CUDA-Unterstützung mit, pipeline/speech_to_text.py::get_torch_device()
erkennt eine nutzbare GPU zur Laufzeit selbst und fällt sonst automatisch auf
CPU zurück (kein Programmfehler, nur langsamer). Diese Vereinfachung ist
NICHT durch einen echten Installations-/Lauf-Zyklus verifiziert (siehe
TME-Backlog.md) - PDF-Translators eigene requirements-gpu.txt-Sonderbehandlung
existiert gerade WEIL simple-lama-inpainting dort strengere Versions-Pins
hat, die TMEs Abhängigkeiten laut aktuellem requirements-stt.txt nicht haben.
"""
from __future__ import annotations

import subprocess
import venv as venv_module
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from bootstrap import desktop_integration, paths, release_source

_PIP_INSTALL_TIMEOUT_SECONDS = 60 * 30  # torch/whisper-Wheels sind groß und brauchen Zeit


class InstallMode(str, Enum):
    STANDARD = "standard"
    WITH_STT = "with_stt"


class InstallStep(str, Enum):
    """Entspricht den "bootstrap.install_step_*"-Textkatalog-Schlüsseln."""

    SOURCE = "source"
    VENV = "venv"
    DEPS = "deps"
    SHORTCUT = "shortcut"


class InstallError(RuntimeError):
    """Wird geworfen, wenn ein Teil der Stufe-2-Installation fehlschlägt."""


@dataclass(frozen=True)
class InstallProgress:
    step: InstallStep
    detail: str = ""


ProgressCallback = Callable[[InstallProgress], None]


def _report(progress_cb: Optional[ProgressCallback], step: InstallStep, detail: str = "") -> None:
    if progress_cb is not None:
        progress_cb(InstallProgress(step=step, detail=detail))


def requirements_files_for_mode(mode: InstallMode, app_source_dir: Path) -> list[Path]:
    """Zu installierende requirements*.txt-Dateien, in Reihenfolge, für den
    gewählten Modus.

    Eine fehlende requirements-stt.txt wird stillschweigend übersprungen
    statt einen Fehler zu werfen - ein gegen ein älteres App-Source-Release
    gebauter Bootstrapper könnte sie schlicht noch nicht kennen.
    """
    candidates = [app_source_dir / "requirements.txt"]
    if mode is InstallMode.WITH_STT:
        candidates.append(app_source_dir / "requirements-stt.txt")
    return [path for path in candidates if path.is_file()]


def create_venv(venv_dir: Path) -> None:
    try:
        venv_module.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    except Exception as exc:  # venv/ensurepip können mehrere Fehlertypen werfen
        raise InstallError(f"Die virtuelle Umgebung konnte nicht angelegt werden: {exc}") from exc


def pip_install(venv_python: Path, requirements_file: Path) -> None:
    command = [str(venv_python), "-m", "pip", "install", "-r", str(requirements_file)]
    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_PIP_INSTALL_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise InstallError(
            f"Installation von {requirements_file.name} fehlgeschlagen: {exc.stderr.strip() if exc.stderr else exc}"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"Installation von {requirements_file.name} fehlgeschlagen: {exc}") from exc


def run_install(
    venv_dir: Path,
    app_source_dir: Path,
    mode: InstallMode,
    dev_source_override: str | None = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> Path:
    """Führt die vollständige Stufe-2-Sequenz aus und gibt den Pfad zum
    venv-eigenen Python zurück."""
    _report(progress_cb, InstallStep.SOURCE)
    try:
        release_source.download_app_source(app_source_dir, dev_source_override=dev_source_override)
    except release_source.ReleaseSourceError as exc:
        raise InstallError(str(exc)) from exc

    _report(progress_cb, InstallStep.VENV)
    create_venv(venv_dir)
    venv_python = paths.venv_python(venv_dir)

    for requirements_file in requirements_files_for_mode(mode, app_source_dir):
        _report(progress_cb, InstallStep.DEPS, detail=requirements_file.name)
        pip_install(venv_python, requirements_file)

    _report(progress_cb, InstallStep.SHORTCUT)
    try:
        # Icon wird in create_desktop_entry() aus dem heruntergeladenen
        # App-Code aufgelöst - nichts explizit zu übergeben.
        desktop_integration.create_desktop_entry(app_source_dir, venv_python)
    except desktop_integration.DesktopIntegrationError as exc:
        raise InstallError(str(exc)) from exc

    return venv_python
