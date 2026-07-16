"""
Google-Translate-Provider. Nutzt die einfache API-Key-basierte REST-API v2
(https://translation.googleapis.com/language/translate/v2) - bewusst NICHT
die Cloud-SDK/Service-Account-Variante, damit ein einzelner API-Key genügt
(analog zum TELEGRAM_API_HASH-Muster) statt einer GCP-Projekt-Einrichtung.

Formatierung: format="html" behandelt den Text als HTML-Fragment, wodurch
Tags (<ce id="N"/>, <b>...</b> etc., siehe formatting.py) von der Übersetzung
ausgenommen bzw. um den übersetzten Inhalt herum erhalten bleiben.
"""
from __future__ import annotations

from typing import Dict, Optional

from credentials import get_google_translate_api_key

from ._http import post_json
from .base import TranslationError, TranslationResult
from .pricing import DEFAULT_PRICING, estimate_cost_usd

_API_URL = "https://translation.googleapis.com/language/translate/v2"


class GoogleTranslateProvider:
    name = "google"

    def __init__(self, api_key: Optional[str] = None, pricing: Optional[Dict] = None):
        self._api_key = api_key or get_google_translate_api_key()
        if not self._api_key:
            raise TranslationError(
                "Google Translate: kein API-Key gefunden. Bitte GOOGLE_TRANSLATE_API_KEY "
                "setzen oder 'google_translate_api_key' in credentials.json hinterlegen."
            )
        self._pricing = pricing or DEFAULT_PRICING

    async def translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text=text, provider=self.name, target_lang=target_lang, source_lang=source_lang)
        body: Dict[str, str] = {
            "q": text,
            "target": target_lang.lower(),
            "format": "html",
        }
        if source_lang:
            body["source"] = source_lang.lower()
        url = f"{_API_URL}?key={self._api_key}"
        data = await post_json(url, json_body=body, provider_label="Google Translate")
        try:
            translations = data["data"]["translations"]
        except (KeyError, TypeError):
            raise TranslationError(f"Google Translate: leere/ungültige Antwort: {data!r}")
        if not translations:
            raise TranslationError(f"Google Translate: leere/ungültige Antwort: {data!r}")
        first = translations[0]
        translated_text = str(first.get("translatedText") or "")
        detected_source = first.get("detectedSourceLanguage")
        char_count = len(text)
        cost = estimate_cost_usd(self.name, char_count=char_count, pricing=self._pricing)
        return TranslationResult(
            text=translated_text,
            provider=self.name,
            target_lang=target_lang,
            source_lang=(source_lang or detected_source),
            char_count=char_count,
            estimated_cost_usd=cost,
        )
