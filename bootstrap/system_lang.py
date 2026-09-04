"""Erkennt die System-UI-Sprache und normalisiert sie auf eine unterstützte
Katalog-Sprache.

Nur "de" und "en" sind aktuell unterstützt (siehe bootstrap/wizard_text.py's
CATALOGUES) - jede andere Systemsprache fällt auf Englisch zurück, analog
zur selben Design-Entscheidung im bereits produktiven Vorbild
(PDF-Translator, siehe dessen bootstrap/system_lang.py-Docstring). Betrifft
ausschließlich die Texte des Assistenten selbst; TMEs eigene Qt-Übersetzungen
(ui/translations/*.qm) sind davon unabhängig und decken deutlich mehr
Sprachen ab.
"""
from __future__ import annotations

import locale
import os
import platform
import subprocess

_FALLBACK_LANGUAGE = "en"
_SUPPORTED_LANGUAGES = ("de", "en")

_MACOS_LOCALE_TIMEOUT_SECONDS = 5


def raw_system_locale() -> str | None:
    """Best-effort roher Locale-/Sprachcode für das aktuelle Betriebssystem,
    z.B. "de_DE", "en_US", "de-CH". None, falls nicht bestimmbar."""
    system = platform.system()
    if system == "Windows":
        return _raw_windows_locale()
    if system == "Darwin":
        return _raw_macos_locale()
    return _raw_posix_locale()


def _raw_windows_locale() -> str | None:
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        if not lang_id:
            return None
        code = locale.windows_locale.get(lang_id)
        return code
    except Exception:
        # ctypes.windll existiert nur unter Windows; jeder Fehler hier
        # bedeutet einfach "nicht bestimmbar".
        return None


def _raw_macos_locale() -> str | None:
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleLocale"],
            capture_output=True,
            text=True,
            timeout=_MACOS_LOCALE_TIMEOUT_SECONDS,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _raw_posix_locale() -> str | None:
    for env_name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(env_name)
        if value:
            # LANGUAGE kann mehrere, durch ":" getrennte Präferenzen listen;
            # die erste ist die bevorzugte.
            return value.split(":", 1)[0]
    try:
        code, _encoding = locale.getlocale()
    except (ValueError, TypeError):
        return None
    return code


def normalize_locale(raw: str | None, fallback: str = _FALLBACK_LANGUAGE) -> str:
    """Bildet einen rohen Locale-Code auf einen unterstützten Katalog ab
    (aktuell "de"/"en"), mit Rückfall auf `fallback` (standardmäßig
    Englisch) für alles Unbekannte/Unbestimmbare."""
    if not raw:
        return fallback
    primary = raw.strip().lower().replace("_", "-").split("-", 1)[0]
    if primary in _SUPPORTED_LANGUAGES:
        return primary
    return fallback


def detect_system_language() -> str:
    """Komfort-Wrapper: raw_system_locale() in einem Aufruf normalisiert."""
    return normalize_locale(raw_system_locale())
