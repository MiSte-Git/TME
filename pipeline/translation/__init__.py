"""
pipeline.translation: austauschbare Übersetzungs-Provider (DeepL, Google,
ChatGPT/OpenAI) neben der bestehenden Telegram-eigenen Übersetzung.

Öffentliche API:
  - TranslationProvider / TranslationResult / TranslationError (base.py)
  - get_provider(name, translation_cfg) -> TranslationProvider (Factory für
    deepl/google/chatgpt; 'telegram' ist bewusst kein TranslationProvider in
    diesem Sinn, siehe base.py-Docstring)
  - translate_runs(runs, target_lang, provider, source_lang=None) -> Runs +
    TranslationResult (Format-erhaltende Orchestrierung, service.py)
  - TranslationCostTracker / estimate_cost_usd (pricing.py)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .base import TranslationError, TranslationProvider, TranslationResult
from .pricing import DEFAULT_PRICING, TranslationCostTracker, estimate_cost_usd
from .service import translate_runs

VALID_PROVIDER_NAMES = ("telegram", "deepl", "google", "chatgpt")


def get_provider(name: str, translation_cfg: Optional[Dict[str, Any]] = None) -> TranslationProvider:
    """Factory für die externen Provider (deepl/google/chatgpt).

    'telegram' wird hier bewusst NICHT instanziiert - der Telegram-eigene Pfad
    (_fetch_translation in runner_by_ids.py) braucht Peer/Message-ID-Kontext
    und bleibt deshalb ein separater Aufrufzweig in runner_schedule.py /
    runner_by_ids.py statt einer Factory-Instanz.
    """
    cfg = translation_cfg or {}
    pricing = cfg.get("pricing") or DEFAULT_PRICING
    name_norm = (name or "").strip().lower()
    if name_norm == "deepl":
        from .deepl_provider import DeepLProvider
        sub = cfg.get("deepl") or {}
        return DeepLProvider(api_url=sub.get("api_url"), pricing=pricing)
    if name_norm == "google":
        from .google_provider import GoogleTranslateProvider
        return GoogleTranslateProvider(pricing=pricing)
    if name_norm == "chatgpt":
        from .chatgpt_provider import ChatGPTProvider
        sub = cfg.get("chatgpt") or {}
        return ChatGPTProvider(model=sub.get("model"), pricing=pricing)
    raise TranslationError(
        f"Unbekannter oder nicht instanziierbarer Übersetzungs-Provider: '{name}' "
        f"(gültig für diese Factory: deepl, google, chatgpt; 'telegram' läuft separat)."
    )


__all__ = [
    "TranslationError",
    "TranslationProvider",
    "TranslationResult",
    "TranslationCostTracker",
    "estimate_cost_usd",
    "DEFAULT_PRICING",
    "VALID_PROVIDER_NAMES",
    "get_provider",
    "translate_runs",
]
