"""
Format-Erhalt für externe Text-Übersetzungs-APIs (DeepL/Google/ChatGPT).

Diese Provider verstehen nur Plaintext - im Unterschied zu Telegrams eigener
TranslateTextRequest, die auf TextWithEntities arbeitet und Formatierung sowie
Custom-Emojis nativ erhält. Um trotzdem nichts zu verlieren, wird die
bestehende Run-Repräsentation (pipeline.runs: TextRun/EmojiRun/LineBreak, exakt
das, was odt_writer.py & Co. ohnehin verwenden) vor der Übersetzung in einen
getaggten Text serialisiert und danach wieder zurückgebaut:

  - Custom-Emojis  -> <ce id="N"/>   (self-closing Platzhalter, NIE übersetzt;
                                       document_id wird nie an den Provider
                                       geschickt, sondern 1:1 zurückgemappt)
  - bold/italic/... -> <b>...</b>, <i>...</i>, <u>...</u>, <s>...</s>,
                        <code>...</code>, <spoiler>...</spoiler>
  - LineBreak       -> "\n"

DeepL (tag_handling="xml") und Google Translate (format="html") sind beide
darauf ausgelegt, solche Tags samt Inhalt sinnvoll zu übersetzen und die
Tag-Grenzen um den (ggf. umsortierten) übersetzten Text herum zu erhalten.
ChatGPT bekommt stattdessen eine explizite Prompt-Anweisung, Tags unverändert
zu lassen - das ist best-effort ohne API-seitige Garantie (siehe README/Commit-
Hinweis "offene Entscheidung").

Was NICHT erhalten bleibt: exakte Zeichenposition innerhalb eines Segments
(nach einer Übersetzung strukturell nicht sinnvoll definierbar) sowie
Href/Link-Ziele auf Inline-Textspannen (nur die Nachrichten-Kopfzeile mit
Permalink ist davon nicht betroffen, die läuft nie durch die Übersetzung).
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..runs import EmojiRun, LineBreak, Run, TextRun

_TAG_FOR_FLAG: List[Tuple[str, str]] = [
    ("bold", "b"),
    ("italic", "i"),
    ("underline", "u"),
    ("strike", "s"),
    ("code", "code"),
    ("spoiler", "spoiler"),
]

_ESCAPE_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
_UNESCAPE_MAP = {v: k for k, v in _ESCAPE_MAP.items()}
_ESCAPE_RE = re.compile("|".join(re.escape(k) for k in _ESCAPE_MAP))
_UNESCAPE_RE = re.compile("|".join(re.escape(k) for k in _UNESCAPE_MAP))


def _escape(s: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(0)], s or "")


def _unescape(s: str) -> str:
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPE_MAP[m.group(0)], s or "")


def mask_runs(runs: List[Run]) -> Tuple[str, Dict[str, EmojiRun]]:
    """Serialisiert Runs zu getaggtem Text + Emoji-Platzhalter-Map."""
    parts: List[str] = []
    emoji_by_id: Dict[str, EmojiRun] = {}
    next_id = 0
    for r in runs:
        if isinstance(r, LineBreak):
            parts.append("\n")
        elif isinstance(r, EmojiRun):
            key = str(next_id)
            next_id += 1
            emoji_by_id[key] = r
            parts.append(f'<ce id="{key}"/>')
        elif isinstance(r, TextRun):
            text = _escape(r.text)
            open_tags = "".join(f"<{tag}>" for flag, tag in _TAG_FOR_FLAG if getattr(r, flag, False))
            close_tags = "".join(f"</{tag}>" for flag, tag in reversed(_TAG_FOR_FLAG) if getattr(r, flag, False))
            parts.append(f"{open_tags}{text}{close_tags}")
        # ImageRun kommt in Message-Body-Runs (build_runs_from_twe) nicht vor.
    return "".join(parts), emoji_by_id


_INLINE_TAG_RE = re.compile(
    r'<ce id="(?P<ce_id>\d+)"\s*/>'
    r'|<(?P<tag>b|i|u|s|code|spoiler)>(?P<inner>.*?)</(?P=tag)>',
    re.DOTALL,
)


def unmask_to_runs(translated_text: str, emoji_by_id: Dict[str, EmojiRun]) -> Tuple[List[Run], set]:
    """Kehrt mask_runs() um. Gibt (Runs, gefundene_emoji_ids) zurück, damit der
    Aufrufer prüfen kann, ob der Provider Platzhalter verloren/verändert hat.
    """
    out: List[Run] = []
    found_ids: set = set()
    lines = (translated_text or "").split("\n")
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            out.append(LineBreak(kind="LineBreak"))
        pos = 0
        for m in _INLINE_TAG_RE.finditer(line):
            if m.start() > pos:
                seg = _unescape(line[pos:m.start()])
                if seg:
                    out.append(TextRun(kind="TextRun", text=seg))
            if m.group("ce_id") is not None:
                ce_id = m.group("ce_id")
                er = emoji_by_id.get(ce_id)
                if er is not None:
                    found_ids.add(ce_id)
                    out.append(EmojiRun(kind="EmojiRun", document_id=er.document_id, height_em=er.height_em))
            else:
                tag = m.group("tag")
                inner = _unescape(m.group("inner"))
                flags = {flag: (tag == t) for flag, t in _TAG_FOR_FLAG}
                if inner:
                    out.append(TextRun(kind="TextRun", text=inner, **flags))
            pos = m.end()
        if pos < len(line):
            seg = _unescape(line[pos:])
            if seg:
                out.append(TextRun(kind="TextRun", text=seg))
    return out, found_ids
