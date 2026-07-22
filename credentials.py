from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple, Optional

from pipeline.logging_setup import get_logger

logger = get_logger(__name__)


def _mask_api_hash(api_hash: str) -> str:
    """Maskiert api_hash für Diagnose-Logs: nur Länge und die äußersten 2
    Zeichen je Seite bleiben erkennbar, der Rest wird durch '*' ersetzt -
    genug, um z.B. eine falsche Länge (Kopierfehler, unsichtbares Zeichen)
    zu erkennen, ohne den Wert selbst preiszugeben."""
    if len(api_hash) <= 4:
        return "*" * len(api_hash)
    return f"{api_hash[:2]}{'*' * (len(api_hash) - 4)}{api_hash[-2:]}"


# Telegram vergibt api_id-Werte aktuell typischerweise 7-8-stellig. Kein
# hartes Limit von Telegram, daher hier bewusst nur eine WARNING statt eines
# Fehlers - falls künftig laengere IDs vergeben werden, soll das nicht
# ploetzlich Logins blockieren, nur auffallen (z.B. Tippfehler mit einer
# Ziffer zu viel beim manuellen Abtippen statt Copy-Paste).
_API_ID_PLAUSIBLE_MAX_DIGITS = 8


def _warn_if_api_id_implausible(api_id: int) -> None:
    digits = len(str(abs(api_id)))
    if digits > _API_ID_PLAUSIBLE_MAX_DIGITS:
        logger.warning(
            "api_id hat %d Stellen - ungewöhnlich lang (Telegram vergibt aktuell "
            "typischerweise %d-8-stellige Werte). Möglicher Tippfehler beim "
            "manuellen Eintragen (z.B. eine Ziffer zu viel)? Wert selbst wird "
            "hier bewusst nicht geloggt.",
            digits,
            _API_ID_PLAUSIBLE_MAX_DIGITS - 1,
        )


def _log_credentials_diagnostics(source: str, api_id: int, api_hash: str) -> None:
    # Bewusst keine Secret-Werte, nur Metadaten - hilft bei
    # ApiIdInvalidError zu unterscheiden, ob die Werte aus der falschen
    # Quelle stammen, api_id versehentlich kein int ist, oder api_hash eine
    # unerwartete Länge hat (z.B. durch ein unsichtbares Zeichen beim
    # Copy-Paste, das .strip() nicht entfernt - z.B. Zero-Width-Space).
    logger.info(
        "Telegram-Credentials aus %s. api_id-Typ=%s, api_hash-Länge=%d "
        "(erwartet: 32), api_hash=%s",
        source,
        type(api_id).__name__,
        len(api_hash),
        _mask_api_hash(api_hash),
    )
    _warn_if_api_id_implausible(api_id)


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
        _log_credentials_diagnostics("Umgebungsvariablen (TELEGRAM_API_ID/TELEGRAM_API_HASH)", api_id, api_hash)
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
        _log_credentials_diagnostics(str(cfg_path), api_id, api_hash)
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


# Service-Name, unter dem alle Provider-Keys im OS-Keyring abgelegt werden
# (Windows Credential Locker / macOS Keychain / Secret Service unter Linux).
_KEYRING_SERVICE = "telegram-odt"

# provider-id -> (ENV-Var-Name, Key-Name in credentials.json / Keyring-Username)
_PROVIDER_KEYS: dict[str, Tuple[str, str]] = {
    "deepl": ("DEEPL_API_KEY", "deepl_api_key"),
    "google": ("GOOGLE_TRANSLATE_API_KEY", "google_translate_api_key"),
    "openai": ("OPENAI_API_KEY", "openai_api_key"),
}


def _get_keyring_module():
    """Importiert keyring erst bei Bedarf (nicht bei jedem credentials.py-Import),
    und liefert None statt zu werfen, falls das Paket fehlt - dann greift der
    bestehende credentials.json-Fallback unverändert."""
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None


def _get_keyring_value(key_name: str) -> Optional[str]:
    kr = _get_keyring_module()
    if kr is None:
        return None
    try:
        val = kr.get_password(_KEYRING_SERVICE, key_name)
    except Exception:
        # z.B. keyring.errors.NoKeyringError - auf headless Linux ohne laufenden
        # Secret-Service-Daemon/D-Bus-Session ist kein Backend verfügbar.
        return None
    return val.strip() if isinstance(val, str) and val.strip() else None


def _set_keyring_value(key_name: str, value: str) -> bool:
    """Versucht, value im OS-Keyring zu speichern. Gibt True bei Erfolg zurück,
    False falls kein nutzbares Keyring-Backend gefunden wurde (Aufrufer weicht
    dann auf credentials.json aus)."""
    kr = _get_keyring_module()
    if kr is None:
        return False
    try:
        kr.set_password(_KEYRING_SERVICE, key_name, value)
        return True
    except Exception:
        return False


def _get_provider_api_key(env_var: str, json_key: str) -> Optional[str]:
    """Liest einen einzelnen API-Key in dieser Priorität:
    1) Umgebungsvariable, 2) OS-Keyring (falls Backend verfügbar),
    3) Fallback credentials.json (~/.config/telegram-odt/).
    Gibt None zurück statt zu werfen - fehlender Key ist für Übersetzungs-Provider
    kein Programmfehler, sondern wird dort als TranslationError gemeldet.
    """
    env_val = os.environ.get(env_var)
    if env_val and env_val.strip():
        return env_val.strip()
    kr_val = _get_keyring_value(json_key)
    if kr_val:
        return kr_val
    data = _read_credentials_json()
    val = str(data.get(json_key, "")).strip()
    return val or None


def get_deepl_api_key() -> Optional[str]:
    """DeepL API-Key: ENV DEEPL_API_KEY, OS-Keyring oder credentials.json-Feld
    'deepl_api_key'. Wird nochmals defensiv getrimmt, unabhängig von der
    Quelle - ein führendes/nachgestelltes Leerzeichen im Key (z.B. durch
    Copy-Paste in ENV/Keyring/JSON) würde sonst den ':fx'-Suffix-Check für
    die Free/Pro-Endpoint-Wahl in deepl_provider.py unbemerkt verfälschen."""
    val = _get_provider_api_key(*_PROVIDER_KEYS["deepl"])
    return val.strip() if val else None


def get_google_translate_api_key() -> Optional[str]:
    """Google-Translate API-Key: ENV GOOGLE_TRANSLATE_API_KEY, OS-Keyring oder
    credentials.json-Feld 'google_translate_api_key'."""
    val = _get_provider_api_key(*_PROVIDER_KEYS["google"])
    return val.strip() if val else None


def get_openai_api_key() -> Optional[str]:
    """OpenAI API-Key: ENV OPENAI_API_KEY, OS-Keyring oder credentials.json-Feld
    'openai_api_key'."""
    val = _get_provider_api_key(*_PROVIDER_KEYS["openai"])
    return val.strip() if val else None


def save_provider_api_key(provider: str, value: str) -> str:
    """Speichert den API-Key für 'provider' (deepl|google|openai) persistent.

    Versucht zuerst das OS-Keyring (verschlüsselt: Windows Credential Locker /
    macOS Keychain / Secret Service unter Linux). Ist dort kein nutzbares
    Backend vorhanden (z.B. headless Linux ohne Keyring-Daemon/D-Bus-Session),
    wird als Fallback credentials.json im Klartext verwendet wie bisher.

    Rückgabe: "keyring" oder "credentials_json_fallback" - je nachdem, wo der
    Key tatsächlich gelandet ist, damit die UI den Nutzer entsprechend warnen
    kann, falls es der unverschlüsselte Fallback war.
    """
    if provider not in _PROVIDER_KEYS:
        raise ValueError(f"Unbekannter Provider: {provider!r}")
    _, key_name = _PROVIDER_KEYS[provider]
    value = value.strip()
    if not value:
        raise ValueError("Leerer API-Key kann nicht gespeichert werden")

    if _set_keyring_value(key_name, value):
        return "keyring"

    cfg_path = _credentials_json_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_credentials_json()
    data[key_name] = value
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return "credentials_json_fallback"


def save_deepl_api_key(value: str) -> str:
    """Analog zu save_telegram_credentials(): speichert den DeepL-Key
    persistent (OS-Keyring, sonst credentials.json). Siehe save_provider_api_key."""
    return save_provider_api_key("deepl", value)


def save_google_translate_api_key(value: str) -> str:
    """Analog zu save_telegram_credentials(): speichert den Google-Translate-Key
    persistent (OS-Keyring, sonst credentials.json). Siehe save_provider_api_key."""
    return save_provider_api_key("google", value)


def save_openai_api_key(value: str) -> str:
    """Analog zu save_telegram_credentials(): speichert den OpenAI-Key
    persistent (OS-Keyring, sonst credentials.json). Siehe save_provider_api_key."""
    return save_provider_api_key("openai", value)


def get_provider_api_key_source(provider: str) -> str:
    """Liefert, woher der aktuell aktive Key für 'provider' (deepl|google|openai)
    stammt: "env" | "keyring" | "credentials_json" | "none" - für die
    Backend-Anzeige im API-Keys-Dialog."""
    if provider not in _PROVIDER_KEYS:
        raise ValueError(f"Unbekannter Provider: {provider!r}")
    env_var, key_name = _PROVIDER_KEYS[provider]
    if os.environ.get(env_var, "").strip():
        return "env"
    if _get_keyring_value(key_name):
        return "keyring"
    data = _read_credentials_json()
    if str(data.get(key_name, "")).strip():
        return "credentials_json"
    return "none"


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
