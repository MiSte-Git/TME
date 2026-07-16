"""
Erkennung und Klassifikation von "Emoji-Wörtern": zusammenhängende Sequenzen
von Custom-Emojis, die laut letter_map.json (siehe pipeline/lettermap.py)
jeweils einen einzelnen Buchstaben/Zeichen repräsentieren - z.B. ein mit
Buchstaben-Emojis geschriebener Name oder Ausdruck in einer Telegram-
Nachricht.

Mapping ist strikt zeichenweise (Wörterbuch, kein Modell) - siehe
letter_map.json/pipeline/lettermap.py. "Wörter" entstehen hier rein durch
Aneinanderreihung: eine maximale Sequenz aufeinanderfolgender EmojiRuns mit
bekannter document_id (kein anderer Run - Text, LineBreak, unbekanntes
Emoji - dazwischen) gilt als ein Wort, auch wenn sie nur aus einem einzigen
Zeichen besteht.

Für den Übersetzungsfluss (pipeline/translation/) werden übersetzungspflichtige
Wörter (nicht auf der Ausnahmeliste, siehe no_translate_words.py) VOR
mask_runs() in echten Klartext aufgelöst, damit sie beim externen Provider
tatsächlich mitübersetzt werden statt wie gewöhnliche Custom-Emojis pauschal
maskiert und 1:1 zurückgesetzt zu werden. Die Darstellung danach ist bewusst
Klartext (übersetzter Text), nicht wieder als Emoji-Sequenz - eine
Rückübersetzung in die ursprüngliche Emoji-Buchstaben-Darstellung ist nach
einer echten Übersetzung nicht mehr sinnvoll möglich (andere Wortlänge/
-zusammensetzung, evtl. fehlende Buchstaben-Emojis für neue Zeichen).
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

from .runs import EmojiRun, Run, TextRun


def find_emoji_words(runs: List[Run], doc_to_letters: Dict[str, str]) -> List[Tuple[int, int, str]]:
    """Liefert (start, end_exklusiv, entschlüsselter_text) für jede maximale
    Sequenz aufeinanderfolgender EmojiRuns, deren document_id in
    doc_to_letters bekannt ist. start/end sind Indizes in `runs`."""
    words: List[Tuple[int, int, str]] = []
    if not doc_to_letters:
        return words
    i = 0
    n = len(runs)
    while i < n:
        r = runs[i]
        if isinstance(r, EmojiRun) and r.document_id in doc_to_letters:
            j = i
            letters: List[str] = []
            while j < n:
                rj = runs[j]
                if isinstance(rj, EmojiRun) and rj.document_id in doc_to_letters:
                    letters.append(doc_to_letters[rj.document_id])
                    j += 1
                else:
                    break
            words.append((i, j, "".join(letters)))
            i = j
        else:
            i += 1
    return words


def is_translatable(word_text: str, no_translate_words: Set[str]) -> bool:
    """True, wenn das Wort übersetzt werden soll, d.h. NICHT auf der
    Ausnahmeliste steht. Vergleich case-insensitiv (Groß-/Kleinschreibung
    von Lettermap-Zuordnungen ist konfigurierbar, siehe lettermap_case_mode)."""
    if not word_text.strip():
        return False  # nichts zu übersetzen (z.B. reine Steuerzeichen)
    normalized_exceptions = {w.strip().upper() for w in no_translate_words}
    return word_text.strip().upper() not in normalized_exceptions


def expand_translatable_emoji_words(
    runs: List[Run],
    doc_to_letters: Dict[str, str],
    no_translate_words: Set[str],
) -> List[Run]:
    """Ersetzt übersetzungspflichtige Emoji-Wort-Sequenzen durch einen
    einzelnen TextRun mit dem entschlüsselten Klartext. Der Rest der
    Übersetzungs-Pipeline (mask_runs & co.) behandelt diesen Run danach wie
    gewöhnlichen Text und übersetzt ihn mit. Nicht übersetzungspflichtige
    Wörter (Ausnahmeliste) sowie alle anderen Custom-Emojis bleiben
    unverändert EmojiRuns und werden wie bisher 1:1 maskiert/zurückgesetzt.
    """
    if not doc_to_letters:
        return runs
    words = find_emoji_words(runs, doc_to_letters)
    if not words:
        return runs
    out: List[Run] = []
    pos = 0
    for start, end, decoded in words:
        out.extend(runs[pos:start])
        if is_translatable(decoded, no_translate_words):
            out.append(TextRun(kind="TextRun", text=decoded))
        else:
            out.extend(runs[start:end])
        pos = end
    out.extend(runs[pos:])
    return out
