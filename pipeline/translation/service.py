"""
Orchestrierung: Run-Liste -> maskieren -> Provider.translate() -> demaskieren.

Das ist der Integrationspunkt, den runner_schedule.py/runner_by_ids.py für
alle NICHT-Telegram-Provider (deepl/google/chatgpt) nutzen. Für
provider == "telegram" bleibt der bestehende, TextWithEntities-basierte Pfad
(_fetch_translation) unverändert im Einsatz - siehe base.py-Docstring für die
Begründung, warum das kein TranslationProvider im selben Sinn ist.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from ..emoji_words import expand_translatable_emoji_words
from ..runs import Run
from .base import TranslationProvider, TranslationResult
from .formatting import mask_runs, unmask_to_runs


async def translate_runs(
    runs: List[Run],
    target_lang: str,
    provider: TranslationProvider,
    source_lang: Optional[str] = None,
    doc_to_letters: Optional[Dict[str, str]] = None,
    no_translate_words: Optional[Set[str]] = None,
) -> Tuple[List[Run], TranslationResult]:
    """
    doc_to_letters/no_translate_words (optional, Default None = deaktiviert):
    siehe pipeline/emoji_words.py. Wenn gesetzt, werden übersetzungspflichtige
    Emoji-Wort-Sequenzen (Custom-Emojis, die laut letter_map.json Buchstaben
    darstellen und NICHT auf der Ausnahmeliste stehen) vor dem Maskieren in
    Klartext aufgelöst, damit sie tatsächlich mitübersetzt werden - statt wie
    gewöhnliche Custom-Emojis pauschal als <ce id="N"/>-Platzhalter 1:1
    zurückgesetzt zu werden. Ergebnis erscheint als übersetzter Klartext,
    nicht als rekonstruierte Emoji-Sequenz (siehe emoji_words.py-Docstring).
    """
    if doc_to_letters:
        runs = expand_translatable_emoji_words(runs, doc_to_letters, no_translate_words or set())
    masked_text, emoji_by_id = mask_runs(runs)
    if not masked_text.strip():
        empty = TranslationResult(text="", provider=provider.name, target_lang=target_lang, source_lang=source_lang)
        return [], empty

    result = await provider.translate(masked_text, target_lang, source_lang=source_lang)
    translated_runs, found_ids = unmask_to_runs(result.text, emoji_by_id)

    missing = set(emoji_by_id) - found_ids
    if missing:
        result.warnings.append(
            f"{len(missing)} Custom-Emoji-Platzhalter nach Übersetzung ({provider.name}) nicht "
            f"wiedergefunden - Emoji(s) könnten in der Übersetzung fehlen."
        )

    return translated_runs, result
