from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple, Optional


def _credentials_json_path() -> Path:
    """
    Pfad zur lokalen Credentials-Datei:
      ~/.config/telegram-odt/credentials.json
      bzw. $XDG_CONFIG_HOME/telegram-odt/credentials.json
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "telegram-odt" / "credentials.json"


def get_telegram_credentials() -> Tuple[int, str, Optional[str]]:
    """
    Liest Telegram-Credentials in dieser Priorität:

      1. Umgebungsvariablen:
         - TELEGRAM_API_ID   (int, Pflicht)
         - TELEGRAM_API_HASH (str, Pflicht)
         - TELEGRAM_PHONE    (str, optional)

      2. Fallback: credentials.json unter:
         - ~/.config/telegram-odt/credentials.json
         - oder $XDG_CONFIG_HOME/telegram-odt/credentials.json

    Gibt (api_id, api_hash, phone) zurück.
    Wirft RuntimeError, wenn weder ENV noch Datei gültige Daten liefern.
    """
    # 1) ENV-Prio
    api_id_raw = os.environ.get("TELEGRAM_API_ID")
    api_hash_raw = os.environ.get("TELEGRAM_API_HASH")
    phone_raw = os.environ.get("TELEGRAM_PHONE")

    if api_id_raw and api_hash_raw:
        api_id = int(api_id_raw.strip())
        api_hash = api_hash_raw.strip()
        phone = phone_raw.strip() if isinstance(phone_raw, str) and phone_raw.strip() else None
        return api_id, api_hash, phone

    # 2) Fallback: credentials.json
    cfg_path = _credentials_json_path()
    if cfg_path.is_file():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        api_id_val = str(data.get("api_id", "")).strip()
        api_hash_val = str(data.get("api_hash", "")).strip()
        phone_val = str(data.get("phone", "")).strip()

        if not api_id_val or not api_hash_val:
            raise RuntimeError("api_id oder api_hash in credentials.json fehlen oder sind leer")

        api_id = int(api_id_val)
        api_hash = api_hash_val
        phone = phone_val or None
        return api_id, api_hash, phone

    # Weder ENV noch Datei vorhanden/valid → Fehler
    raise RuntimeError("TELEGRAM_API_ID oder TELEGRAM_API_HASH nicht gesetzt und keine gültige credentials.json gefunden")


def _read_credentials_json() -> dict:
    cfg_path = _credentials_json_path()
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_provider_api_key(env_var: str, json_key: str) -> Optional[str]:
    """Liest einen einzelnen API-Key nach demselben Muster wie Telegram-Credentials:
    1) Umgebungsvariable, 2) Fallback credentials.json (~/.config/telegram-odt/).
    Gibt None zurück statt zu werfen - fehlender Key ist für Übersetzungs-Provider
    kein Programmfehler, sondern wird dort als TranslationError gemeldet.
    """
    env_val = os.environ.get(env_var)
    if env_val and env_val.strip():
        return env_val.strip()
    data = _read_credentials_json()
    val = str(data.get(json_key, "")).strip()
    return val or None


def get_deepl_api_key() -> Optional[str]:
    """DeepL API-Key: ENV DEEPL_API_KEY oder credentials.json-Feld 'deepl_api_key'."""
    return _get_provider_api_key("DEEPL_API_KEY", "deepl_api_key")


def get_google_translate_api_key() -> Optional[str]:
    """Google-Translate API-Key: ENV GOOGLE_TRANSLATE_API_KEY oder
    credentials.json-Feld 'google_translate_api_key'."""
    return _get_provider_api_key("GOOGLE_TRANSLATE_API_KEY", "google_translate_api_key")


def get_openai_api_key() -> Optional[str]:
    """OpenAI API-Key: ENV OPENAI_API_KEY oder credentials.json-Feld 'openai_api_key'."""
    return _get_provider_api_key("OPENAI_API_KEY", "openai_api_key")


def save_telegram_credentials(api_id: int, api_hash: str, phone: Optional[str] = None) -> None:
    """
    Speichert die übergebenen Credentials als Fallback in credentials.json
    unter ~/.config/telegram-odt/credentials.json bzw. $XDG_CONFIG_HOME.
    """
    cfg_path = _credentials_json_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "api_id": int(api_id),
        "api_hash": str(api_hash),
    }
    if phone:
        data["phone"] = str(phone)

    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
