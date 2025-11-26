from __future__ import annotations
import os
from typing import Tuple, Optional

def get_telegram_credentials() -> Tuple[int, str, Optional[str]]:
    api_id_raw = os.environ.get("TELEGRAM_API_ID")
    api_hash_raw = os.environ.get("TELEGRAM_API_HASH")
    phone_raw = os.environ.get("TELEGRAM_PHONE")

    if not api_id_raw or not api_hash_raw:
        raise RuntimeError("TELEGRAM_API_ID oder TELEGRAM_API_HASH nicht gesetzt")

    api_id = int(api_id_raw.strip())
    api_hash = api_hash_raw.strip()
    phone = phone_raw.strip() if isinstance(phone_raw, str) and phone_raw.strip() else None

    return api_id, api_hash, phone
