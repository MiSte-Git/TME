"""
Provider-Abstraktion für externe Übersetzungsdienste (DeepL, Google, ChatGPT).

Wichtig: Dies ist eine ZUSÄTZLICHE Abstraktion neben der bestehenden
Telegram-eigenen Übersetzung (siehe pipeline/runner_by_ids.py::_fetch_translation).
Telegrams TranslateTextRequest arbeitet auf types.TextWithEntities und benötigt
Peer/Message-ID (Session-Kontext) - das passt nicht in ein reines Text-Interface
und bleibt deshalb bewusst ein eigener Pfad ("provider: telegram" in config.yaml
ruft weiterhin direkt _fetch_translation auf, siehe runner_schedule.py).
Für DeepL/Google/ChatGPT ist die generische Text-Schnittstelle dagegen exakt das,
was die jeweiligen APIs nativ anbieten.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable


class TranslationError(Exception):
    """Einheitliche Fehler-Exception für alle Provider (fehlender API-Key,
    Rate-Limit, Netzwerkfehler, ungültige Antwort etc.)."""


@dataclass
class TranslationResult:
    """Ergebnis eines Übersetzungsaufrufs, providerunabhängig.

    char_count / input_tokens / output_tokens dienen ausschließlich der
    Kostenschätzung (siehe pricing.py) - keine Live-Preisabfrage.
    """
    text: str
    provider: str
    target_lang: str
    source_lang: Optional[str] = None
    char_count: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost_usd: float = 0.0
    warnings: List[str] = field(default_factory=list)


@runtime_checkable
class TranslationProvider(Protocol):
    """Minimalinterface, das jeder Provider (deepl/google/chatgpt) erfüllt."""

    name: str

    async def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: Optional[str] = None,
    ) -> TranslationResult:
        ...
