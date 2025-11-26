from __future__ import annotations

import os
from typing import Tuple, Optional


def get_telegram_credentials() -> Tuple[int, str, Optional[str]]:
    """Liest Telegram-Credentials aus der Umgebung.

    Erwartet:
      - TELEGRAM_API_ID (int)
      - TELEGRAM_API_HASH (str)
      - optional TELEGRAM_PHONE (str)

    Bricht hart ab, wenn api_id/api_hash fehlen oder ungültig sind.
    """
    api_id_raw = os.environ.get("TELEGRAM_API_ID")
    api_hash_raw = os.environ.get("TELEGRAM_API_HASH")
    phone_raw = os.environ.get("TELEGRAM_PHONE")

    if not api_id_raw or not api_hash_raw:
        raise RuntimeError("TELEGRAM_API_ID oder TELEGRAM_API_HASH nicht gesetzt")

    api_id_raw = api_id_raw.strip()
    api_hash = api_hash_raw.strip()

    try:
        api_id = int(api_id_raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"Ungültige TELEGRAM_API_ID: {api_id_raw!r}")

    phone = phone_raw.strip() if isinstance(phone_raw, str) and phone_raw.strip() else None

    return api_id, api_hash, phone
