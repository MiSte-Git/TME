"""
DeepL-Provider. Nutzt die öffentliche REST-API direkt (kein deepl-SDK nötig).

Formatierung: DeepL unterstützt tag_handling="xml" - XML-artige Tags im Text
(<ce id="N"/>, <b>...</b> etc., siehe formatting.py) werden dabei erhalten und
um den übersetzten (ggf. umsortierten) Inhalt herum platziert. Das ist
API-seitig dokumentiertes Verhalten, keine Bastellösung unsererseits.
"""
from __future__ import annotations

from typing import Dict, Optional

from credentials import get_deepl_api_key

from ..logging_setup import get_logger
from ._http import get_json, post_json
from .base import TranslationError, TranslationResult
from .deepl_quota import DEEPL_FREE_CHARACTER_LIMIT
from .pricing import DEFAULT_PRICING, estimate_cost_usd

logger = get_logger(__name__)


class DeepLProvider:
    name = "deepl"

    def __init__(self, api_key: Optional[str] = None, api_url: Optional[str] = None, pricing: Optional[Dict] = None):
        self._api_key = api_key or get_deepl_api_key()
        if not self._api_key:
            raise TranslationError(
                "DeepL: kein API-Key gefunden. Bitte DEEPL_API_KEY setzen oder "
                "'deepl_api_key' in credentials.json hinterlegen."
            )
        if api_url:
            self._api_url = api_url
        else:
            # DeepL-Konvention: Keys mit ":fx"-Suffix gehören zum kostenlosen Tier.
            is_free = self._api_key.endswith(":fx")
            self._api_url = (
                "https://api-free.deepl.com/v2/translate"
                if is_free
                else "https://api.deepl.com/v2/translate"
            )
            logger.info(
                "DeepL: Endpoint '%s' gewählt (Key %s auf ':fx')",
                "api-free" if is_free else "api",
                "endet" if is_free else "endet nicht",
            )
        self._pricing = pricing or DEFAULT_PRICING

    async def translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text=text, provider=self.name, target_lang=target_lang, source_lang=source_lang)
        form: Dict[str, str] = {
            "text": text,
            "target_lang": target_lang.upper(),
            "tag_handling": "xml",
        }
        if source_lang:
            form["source_lang"] = source_lang.upper()
        headers = {"Authorization": f"DeepL-Auth-Key {self._api_key}"}
        data = await post_json(self._api_url, headers=headers, form_body=form, provider_label="DeepL")
        translations = data.get("translations") or []
        if not translations:
            raise TranslationError(f"DeepL: leere/ungültige Antwort: {data!r}")
        first = translations[0]
        translated_text = str(first.get("text") or "")
        detected_source = first.get("detected_source_language")
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

    async def get_usage(self) -> tuple[int, int]:
        """GET /v2/usage - (character_count, character_limit) der aktuellen
        DeepL-Abrechnungsperiode (siehe deepl_quota.py für die Einordnung/
        Persistierung). Bewusst NICHT Teil von translate() - wird einmal pro
        Lauf abgefragt (siehe runner_schedule.py), nicht einmal pro
        übersetzter Nachricht."""
        headers = {"Authorization": f"DeepL-Auth-Key {self._api_key}"}
        usage_url = self._api_url.rsplit("/v2/", 1)[0] + "/v2/usage"
        data = await get_json(usage_url, headers=headers, provider_label="DeepL-Kontingent")
        character_count = int(data.get("character_count") or 0)
        character_limit = int(data.get("character_limit") or DEEPL_FREE_CHARACTER_LIMIT)
        return character_count, character_limit
