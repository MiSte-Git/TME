"""Pro-Benutzer-Installationsorte für die Stufe-2-Payload des Bootstrappers.

Stufe 1 (dieses Paket) braucht nie Administrator-/root-Rechte - das ist der
eigentliche Grund, warum ein klassischer systemweiter Installer (.deb/.rpm/
.msi) unnötig ist, analog zum bereits produktiven Vorbild in PDF-Translator
(siehe dessen bootstrap/paths.py-Docstring). Stufe 2 (venv + heruntergeladener
App-Code) liegt deshalb immer unter einem Pro-Benutzer-Datenverzeichnis, je
Plattform nach deren Konvention.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

# Bewusst ein eigener Slug, unabhängig vom bereits bestehenden Ablageort der
# Zugangsdaten (credentials.py::_credentials_json_path() nutzt
# "telegram-odt" - historisch gewachsener Name, unverändert belassen). Beide
# Verzeichnisse haben unterschiedliche Zwecke (Installation/venv hier,
# Zugangsdaten dort) und müssen nicht denselben Namen tragen.
APP_SLUG = "tme"
# Windows-Ordnername unter %LOCALAPPDATA% - folgt der Groß-/Kleinschreibung
# des Produktnamens (APP_NAME in ui/app.py), nicht APP_SLUG.
_WINDOWS_DIR_NAME = "TME"


def install_root() -> Path:
    """Pro-Benutzer-Verzeichnis mit venv, heruntergeladenem App-Code und der
    Sprachmarkierungsdatei. Wird bei Bedarf von ensure_install_root() angelegt.
    """
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        return Path(base) / _WINDOWS_DIR_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_SLUG
    # Linux und andere POSIX-Systeme: XDG Base Directory Spec.
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_SLUG


def ensure_install_root() -> Path:
    """install_root(), legt es (inkl. Elternverzeichnisse) an, falls es noch
    nicht existiert."""
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def venv_dir() -> Path:
    """Verzeichnis der vom Installer (bootstrap/installer.py) angelegten
    Pro-Benutzer-venv. Getrennt von app_source_dir(), damit ein erneuter
    Download des App-Codes (ein Update) nie die bereits funktionierende
    Umgebung anfassen und damit riskieren muss."""
    return install_root() / "venv"


def venv_python(venv: Path | None = None) -> Path:
    """Pfad zum eigenen Python-Interpreter der venv, plattformübergreifend."""
    venv = venv if venv is not None else venv_dir()
    if platform.system() == "Windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def app_source_dir() -> Path:
    """Verzeichnis, in das der heruntergeladene/entpackte App-Code (ui/,
    pipeline/, requirements*.txt, credentials.py, ...) installiert wird -
    siehe bootstrap/release_source.py."""
    return install_root() / "app"


def language_marker_file() -> Path:
    """JSON-Datei mit der während der Installation gewählten Sprache.

    Analog zum bereits produktiven Vorbild in PDF-Translator geschrieben,
    damit ein künftiger erster App-Start diese Sprache übernehmen könnte -
    ui/app.py liest diese Datei aktuell noch NICHT (siehe TME-Backlog.md,
    offener Folgeschritt); die Datei wird trotzdem bereits geschrieben, damit
    diese Anbindung später ohne Änderung am Bootstrapper nachgezogen werden
    kann. Bewusst eine einfache JSON-Datei statt eines direkten Zugriffs auf
    QSettings' natives Speicherformat (das je Plattform unterschiedlich ist -
    INI-Datei vs. Windows-Registry), was dieses dependency-freie Paket (siehe
    Modul-Docstring von bootstrap/__init__.py; kein PySide6-Import erlaubt)
    ohnehin nicht zuverlässig/portabel könnte.
    """
    return install_root() / "language.json"


def is_frozen() -> bool:
    """True, wenn aus einer per PyInstaller gebauten Executable heraus
    ausgeführt wird statt aus dem Quellcode - relevant für den lokalen
    Dev-Override-Fallback von release_source.py, der nur für Entwickler:innen
    greifen soll, die aus dem Quellcode heraus arbeiten."""
    return getattr(sys, "frozen", False)
