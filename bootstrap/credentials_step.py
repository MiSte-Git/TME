"""Zugangsdaten-Schritt (Telegram + Übersetzungs-Provider), nutzt das bereits
bestehende, Top-Level `credentials.py` der App wieder, statt Speicherung ein
zweites Mal zu implementieren.

Importiert `credentials` bewusst zur Laufzeit aus dem gerade
heruntergeladenen app_source_dir (nicht eine im Bootstrapper statisch
mitgelieferte Kopie) - dieser Schritt läuft deshalb immer erst NACH
bootstrap/installer.py::run_install(). Das garantiert exakt denselben
Zugangsdaten-Code-Pfad, den die frisch installierte App selbst beim ersten
Start nutzt, ohne Risiko, dass eine ältere, zur Bootstrapper-Bauzeit
eingefrorene Kopie davon abweicht.

`credentials.py` ist bewusst Qt-frei (siehe dessen eigenen Aufbau - keine
PySide6-Imports), der Import hier zieht also kein PySide6 mit.
"""
from __future__ import annotations

import importlib
import sys
import webbrowser
from pathlib import Path
from types import ModuleType

# Reihenfolge analog zur "bootstrap.credentials_provider_*"-Textkatalog-
# Struktur und der Checkliste-zuerst-UX aus dem bereits produktiven Vorbild
# in PDF-Translator: DeepL zuerst, da schnellste/einfachste Anmeldung.
PROVIDER_ORDER = ("deepl", "google", "openai")

PROVIDER_SIGNUP_URLS = {
    "deepl": "https://www.deepl.com/pro-api",
    "google": "https://console.cloud.google.com/apis/library/translate.googleapis.com",
    "openai": "https://platform.openai.com/api-keys",
}

TELEGRAM_SIGNUP_URL = "https://my.telegram.org"


def _import_from_app_source(app_source_dir: Path, module_name: str) -> ModuleType:
    app_source_str = str(app_source_dir)
    if app_source_str not in sys.path:
        sys.path.insert(0, app_source_str)
    return importlib.import_module(module_name)


def load_credentials_module(app_source_dir: Path) -> ModuleType:
    """Das Top-Level `credentials`-Modul der heruntergeladenen App
    (get_telegram_credentials/save_telegram_credentials/
    save_provider_api_key/get_provider_api_key_source)."""
    return _import_from_app_source(app_source_dir, "credentials")


# --- Übersetzungs-Provider ---------------------------------------------


def list_providers(app_source_dir: Path) -> list[str]:
    # PROVIDER_ORDER entspricht exakt credentials.py::_PROVIDER_KEYS - keine
    # Introspektion nötig, dieses (private) Dict wird bewusst nicht direkt
    # importiert.
    return list(PROVIDER_ORDER)


def provider_status(app_source_dir: Path, provider: str) -> str:
    """"env" / "keyring" / "credentials_json" / "none" - siehe
    credentials.py::get_provider_api_key_source()."""
    credentials = load_credentials_module(app_source_dir)
    return credentials.get_provider_api_key_source(provider)


def save_provider_credential(app_source_dir: Path, provider: str, value: str) -> str:
    """Speichert `value` für `provider` über
    credentials.py::save_provider_api_key(). Rückgabe "keyring" oder
    "credentials_json_fallback" - Aufrufer:innen können damit warnen, falls
    kein verschlüsseltes Backend verfügbar war."""
    credentials = load_credentials_module(app_source_dir)
    return credentials.save_provider_api_key(provider, value)


def signup_url(provider: str) -> str | None:
    return PROVIDER_SIGNUP_URLS.get(provider)


# --- Telegram ------------------------------------------------------------


def telegram_status(app_source_dir: Path) -> str:
    """"set" wenn bereits gültige Telegram-Zugangsdaten gefunden werden
    (ENV oder credentials.json), sonst "missing". Wirft absichtlich nicht -
    fehlende Zugangsdaten sind hier kein Programmfehler, TME fragt beim
    ersten Lauf ohnehin automatisch danach (siehe ui/login_dialog.py)."""
    credentials = load_credentials_module(app_source_dir)
    try:
        credentials.get_telegram_credentials()
    except RuntimeError:
        return "missing"
    return "set"


def save_telegram_credentials(
    app_source_dir: Path, api_id: str, api_hash: str, phone: str | None = None
) -> None:
    """Speichert Telegram-API-ID/-Hash (+ optionale Telefonnummer) über
    credentials.py::save_telegram_credentials(). `api_id` kommt als String
    aus dem Eingabefeld des Assistenten und wird hier zu int konvertiert -
    ValueError (z.B. bei Nicht-Ziffern) wird bewusst nicht abgefangen, der
    Aufrufer (bootstrap/app.py) zeigt sie als Eingabefehler an, statt
    stillschweigend ungültige Werte zu speichern."""
    credentials = load_credentials_module(app_source_dir)
    credentials.save_telegram_credentials(int(api_id.strip()), api_hash.strip(), phone.strip() if phone else None)


# --- gemeinsam -------------------------------------------------------------


def open_url(url: str) -> bool:
    """Öffnet `url` im System-Standardbrowser. Liefert False (statt zu
    werfen), falls der Browser nicht gestartet werden konnte - ein
    fehlgeschlagenes Öffnen soll den restlichen Zugangsdaten-Schritt nicht
    blockieren, der bereits erhaltene Schlüssel/Wert lässt sich weiterhin von
    Hand einfügen."""
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def open_signup_page(provider: str) -> bool:
    url = signup_url(provider)
    if not url:
        return False
    return open_url(url)


def open_telegram_signup_page() -> bool:
    return open_url(TELEGRAM_SIGNUP_URL)
