"""
Kleine, abhängigkeitsfreie HTTP-Hilfsfunktion für die Provider-Module.

Bewusst kein requests/httpx/aiohttp als neue Abhängigkeit - alle drei neuen
Provider werden per REST-Aufruf über die stdlib angesprochen, damit
Nutzer:innen, die nur einen (oder keinen) externen Provider verwenden,
keine zusätzlichen Pakete installieren müssen.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from ..logging_setup import get_logger
from .base import TranslationError

logger = get_logger(__name__)

_RETRIES = 3
_INITIAL_DELAY = 0.8
_BACKOFF = 1.8


def _do_request(
    url: str,
    *,
    method: str = "POST",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise _HttpStatusError(exc.code, body) from exc
    except urllib.error.URLError as exc:
        raise TranslationError(f"Netzwerkfehler bei Anfrage an {url}: {exc.reason}") from exc


class _HttpStatusError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


async def post_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    form_body: Optional[Dict[str, str]] = None,
    timeout: float = 20.0,
    provider_label: str = "Übersetzungsdienst",
) -> Dict[str, Any]:
    """POST mit JSON- ODER Formular-Body, Retry bei Rate-Limit (429)/5xx,
    einheitliche TranslationError bei endgültigem Fehlschlag.
    """
    hdrs = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif form_body is not None:
        body = urllib.parse.urlencode(form_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = b""

    delay = _INITIAL_DELAY
    last_err: Optional[Exception] = None
    for attempt in range(_RETRIES):
        try:
            return await asyncio.to_thread(_do_request, url, headers=hdrs, data=body, timeout=timeout)
        except _HttpStatusError as exc:
            last_err = exc
            logger.warning(
                "%s: HTTP %s Fehlerantwort (Versuch %d/%d): %s",
                provider_label, exc.status, attempt + 1, _RETRIES, exc.body,
            )
            if exc.status == 429 or exc.status >= 500:
                if attempt < _RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay *= _BACKOFF
                    continue
                raise TranslationError(
                    f"{provider_label}: Rate-Limit/Serverfehler nach {_RETRIES} Versuchen ({exc})"
                ) from exc
            if exc.status in (401, 403):
                raise TranslationError(f"{provider_label}: Zugriff verweigert (ungültiger API-Key?) - {exc}") from exc
            raise TranslationError(f"{provider_label}: Anfrage fehlgeschlagen ({exc})") from exc
        except TranslationError:
            raise
        except Exception as exc:  # pragma: no cover - defensiv
            last_err = exc
            if attempt < _RETRIES - 1:
                await asyncio.sleep(delay)
                delay *= _BACKOFF
                continue
            raise TranslationError(f"{provider_label}: unerwarteter Fehler ({exc})") from exc
    raise TranslationError(f"{provider_label}: fehlgeschlagen ({last_err})")
