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

Für den Telegram-nativen Übersetzungspfad (_fetch_translation in
runner_by_ids.py) gibt es kein Runs-Modell, sondern nur TextWithEntities mit
Offset-basierten MessageEntity-Objekten (UTF-16-Codeunits). Dafür existiert
mit find_emoji_words_in_entities()/expand_translatable_emoji_words_twe() ein
Äquivalent, das direkt auf Entities statt auf Runs arbeitet - siehe dort.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Set, Tuple

from telethon import types
from telethon.tl.types import MessageEntityCustomEmoji
from telethon.utils import add_surrogate, del_surrogate

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


def find_emoji_words_in_entities(
    entities: List[Any], doc_to_letters: Dict[str, str]
) -> List[Tuple[int, int, str, List[Any]]]:
    """TextWithEntities-Äquivalent zu find_emoji_words(): sucht maximale
    Sequenzen direkt aneinandergrenzender MessageEntityCustomEmoji (Offset in
    UTF-16-Codeunits, keine Lücke zwischen Ende eines Entity und Beginn des
    nächsten) mit bekannter document_id. Liefert (start, end_exklusiv,
    entschlüsselter_text, [zugehörige Entities])."""
    words: List[Tuple[int, int, str, List[Any]]] = []
    if not doc_to_letters:
        return words
    ce = sorted(
        (e for e in (entities or []) if isinstance(e, MessageEntityCustomEmoji)),
        key=lambda e: e.offset,
    )
    i = 0
    n = len(ce)
    while i < n:
        e = ce[i]
        doc_id = str(e.document_id)
        if doc_id not in doc_to_letters:
            i += 1
            continue
        start = e.offset
        end = e.offset + e.length
        letters = [doc_to_letters[doc_id]]
        group = [e]
        j = i + 1
        while j < n:
            nxt = ce[j]
            nxt_doc_id = str(nxt.document_id)
            if nxt.offset == end and nxt_doc_id in doc_to_letters:
                end = nxt.offset + nxt.length
                letters.append(doc_to_letters[nxt_doc_id])
                group.append(nxt)
                j += 1
            else:
                break
        words.append((start, end, "".join(letters), group))
        i = j
    return words


def expand_translatable_emoji_words_twe(
    twe: types.TextWithEntities,
    doc_to_letters: Dict[str, str],
    no_translate_words: Set[str],
) -> types.TextWithEntities:
    """TextWithEntities-Äquivalent zu expand_translatable_emoji_words(): ersetzt
    übersetzungspflichtige Custom-Emoji-Wort-Sequenzen direkt im Telegram-
    Entity-Modell durch Klartext, statt über den Runs-Umweg - Grundlage für
    den Telegram-nativen Übersetzungspfad (_fetch_translation), der mit
    TextWithEntities statt mit Runs arbeitet.

    Entfernt die betroffenen MessageEntityCustomEmoji-Entities, fügt an deren
    Stelle den entschlüsselten Klartext ein und verschiebt alle Entities mit
    Offset/Länge NACH dem jeweiligen Wort um den kumulierten UTF-16-Längen-
    Delta (add_surrogate/del_surrogate, Telethons eigene Offset-Konvention -
    kein neuer Encoder nötig). Nicht übersetzungspflichtige Wörter
    (Ausnahmeliste) sowie alle anderen Entities bleiben unverändert.

    Gibt bei doc_to_letters=None/leer oder wenn keine übersetzungspflichtigen
    Wörter gefunden werden, DASSELBE twe-Objekt unverändert zurück (bewusst,
    für einen billigen Identitätsvergleich beim Aufrufer - siehe
    _fetch_translation in runner_by_ids.py)."""
    if not doc_to_letters:
        return twe

    entities = list(twe.entities or [])
    words = find_emoji_words_in_entities(entities, doc_to_letters)
    translatable = [w for w in words if is_translatable(w[2], no_translate_words)]
    if not translatable:
        return twe
    translatable.sort(key=lambda w: w[0])

    text_su = add_surrogate(twe.text or "")
    removed_ids = {id(e) for w in translatable for e in w[3]}

    new_text_parts: List[str] = []
    cursor = 0
    for start, end, decoded, _group in translatable:
        new_text_parts.append(text_su[cursor:start])
        new_text_parts.append(decoded)
        cursor = end
    new_text_parts.append(text_su[cursor:])
    new_text = del_surrogate("".join(new_text_parts))

    def shifted_offset(orig_offset: int) -> int:
        delta = 0
        for start, end, decoded, _group in translatable:
            if orig_offset >= end:
                delta += len(decoded) - (end - start)
        return orig_offset + delta

    new_entities: List[Any] = []
    for e in entities:
        if id(e) in removed_ids:
            continue
        new_offset = shifted_offset(e.offset)
        new_length = shifted_offset(e.offset + e.length) - new_offset
        e2 = copy.copy(e)
        e2.offset = new_offset
        e2.length = new_length
        new_entities.append(e2)

    return types.TextWithEntities(text=new_text, entities=new_entities)
