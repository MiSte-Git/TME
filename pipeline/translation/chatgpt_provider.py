"""
ChatGPT/OpenAI-Provider. Nutzt die Chat-Completions-REST-API direkt
(kein openai-SDK nötig).

Formatierung: OpenAI bietet - anders als DeepL (tag_handling=xml) und Google
(format=html) - KEINEN API-seitigen Mechanismus, der Tags garantiert
unangetastet lässt. Wir instruieren das Modell per System-Prompt, die Tags
(<ce id="N"/>, <b>...</b> etc., siehe formatting.py) exakt zu erhalten und
nur den natürlichsprachlichen Inhalt zu übersetzen. Das ist best-effort ohne
harte Garantie - unmask_to_runs() erkennt fehlende/verstümmelte
<ce id="N"/>-Platzhalter und meldet das als Warnung zurück (siehe service.py),
statt Custom-Emojis stillschweigend zu verlieren.
"""
from __future__ import annotations

from typing import Dict, Optional

from credentials import get_openai_api_key

from ._http import post_json
from .base import TranslationError, TranslationResult
from .pricing import DEFAULT_PRICING, estimate_cost_usd

_API_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = (
    "You are a precise translation engine. Translate the user's message into {target_lang}. "
    "The text may contain inline tags: <ce id=\"N\"/> (self-closing, a placeholder for an "
    "embedded custom emoji), and <b>, <i>, <u>, <s>, <code>, <spoiler> (paired formatting tags). "
    "Rules: (1) Copy every <ce id=\"N\"/> tag EXACTLY as-is, in the same relative position, "
    "never translate or alter its content or attributes. (2) Keep the paired tags around the "
    "corresponding translated phrase, do not drop or rename them. (3) Preserve line breaks. "
    "(4) Output ONLY the translated text with tags intact - no explanations, no code fences, "
    "no quotes around the output."
)


class ChatGPTProvider:
    name = "chatgpt"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, pricing: Optional[Dict] = None):
        self._api_key = api_key or get_openai_api_key()
        if not self._api_key:
            raise TranslationError(
                "ChatGPT/OpenAI: kein API-Key gefunden. Bitte OPENAI_API_KEY setzen oder "
                "'openai_api_key' in credentials.json hinterlegen."
            )
        self._model = model or _DEFAULT_MODEL
        self._pricing = pricing or DEFAULT_PRICING

    async def translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text=text, provider=self.name, target_lang=target_lang, source_lang=source_lang)
        system = _SYSTEM_PROMPT.format(target_lang=target_lang)
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        data = await post_json(_API_URL, headers=headers, json_body=body, provider_label="ChatGPT/OpenAI")
        choices = data.get("choices") or []
        if not choices:
            raise TranslationError(f"ChatGPT/OpenAI: leere/ungültige Antwort: {data!r}")
        translated_text = str((choices[0].get("message") or {}).get("content") or "")
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        cost = estimate_cost_usd(self.name, input_tokens=input_tokens, output_tokens=output_tokens, pricing=self._pricing)
        return TranslationResult(
            text=translated_text,
            provider=self.name,
            target_lang=target_lang,
            source_lang=source_lang,
            char_count=len(text),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
        )
