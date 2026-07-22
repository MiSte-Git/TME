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
  - href/Link-Ziel  -> <a href="ZIEL">...</a> (Ziel-URL wird nie übersetzt,
                        nur der sichtbare Linktext)
  - LineBreak       -> <lb/>  (self-closing Platzhalter wie <ce/>, bewusst
                        NICHT ein rohes "\n" - siehe unmask_to_runs für die
                        Begründung: ein Provider kann eigene Zeilenumbrüche
                        in den übersetzten Text einfügen, die dann fälschlich
                        als echte LineBreak-Runs übernommen würden)

DeepL (tag_handling="xml") und Google Translate (format="html") sind beide
darauf ausgelegt, solche Tags samt Inhalt sinnvoll zu übersetzen und die
Tag-Grenzen um den (ggf. umsortierten) übersetzten Text herum zu erhalten.
ChatGPT bekommt stattdessen eine explizite Prompt-Anweisung, Tags unverändert
zu lassen - das ist best-effort ohne API-seitige Garantie (siehe README/Commit-
Hinweis "offene Entscheidung").

Was NICHT erhalten bleibt: exakte Zeichenposition innerhalb eines Segments
(nach einer Übersetzung strukturell nicht sinnvoll definierbar).
"""
from __future__ import annotations

import re
from dataclasses import replace as _dc_replace
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

# Für href-Attributwerte zusätzlich Anführungszeichen escapen, sonst würde ein
# "-Zeichen in der URL (theoretisch möglich, wenn auch unüblich) das href-
# Attribut vorzeitig beenden.
_ATTR_ESCAPE_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}
_ATTR_UNESCAPE_MAP = {v: k for k, v in _ATTR_ESCAPE_MAP.items()}
_ATTR_ESCAPE_RE = re.compile("|".join(re.escape(k) for k in _ATTR_ESCAPE_MAP))
_ATTR_UNESCAPE_RE = re.compile("|".join(re.escape(k) for k in _ATTR_UNESCAPE_MAP))


def _escape(s: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(0)], s or "")


def _unescape(s: str) -> str:
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPE_MAP[m.group(0)], s or "")


def _escape_attr(s: str) -> str:
    return _ATTR_ESCAPE_RE.sub(lambda m: _ATTR_ESCAPE_MAP[m.group(0)], s or "")


def _unescape_attr(s: str) -> str:
    return _ATTR_UNESCAPE_RE.sub(lambda m: _ATTR_UNESCAPE_MAP[m.group(0)], s or "")


def mask_runs(runs: List[Run]) -> Tuple[str, Dict[str, EmojiRun]]:
    """Serialisiert Runs zu getaggtem Text + Emoji-Platzhalter-Map."""
    parts: List[str] = []
    emoji_by_id: Dict[str, EmojiRun] = {}
    next_id = 0
    for r in runs:
        if isinstance(r, LineBreak):
            parts.append("<lb/>")
        elif isinstance(r, EmojiRun):
            key = str(next_id)
            next_id += 1
            emoji_by_id[key] = r
            parts.append(f'<ce id="{key}"/>')
        elif isinstance(r, TextRun):
            text = _escape(r.text)
            open_tags = "".join(f"<{tag}>" for flag, tag in _TAG_FOR_FLAG if getattr(r, flag, False))
            close_tags = "".join(f"</{tag}>" for flag, tag in reversed(_TAG_FOR_FLAG) if getattr(r, flag, False))
            body = f"{open_tags}{text}{close_tags}"
            if r.href:
                body = f'<a href="{_escape_attr(r.href)}">{body}</a>'
            parts.append(body)
        # ImageRun kommt in Message-Body-Runs (build_runs_from_twe) nicht vor.
    return "".join(parts), emoji_by_id


_INLINE_TAG_RE = re.compile(
    r'<ce id="(?P<ce_id>\d+)"\s*/>'
    r'|(?P<lb><lb\s*/>)'
    r'|<a href="(?P<href>[^"]*)">(?P<link_inner>.*?)</a>'
    r'|<(?P<tag>b|i|u|s|code|spoiler)>(?P<inner>.*?)</(?P=tag)>',
    re.DOTALL,
)


def unmask_to_runs(translated_text: str, emoji_by_id: Dict[str, EmojiRun]) -> Tuple[List[Run], set]:
    """Kehrt mask_runs() um. Gibt (Runs, gefundene_emoji_ids) zurück, damit der
    Aufrufer prüfen kann, ob der Provider Platzhalter verloren/verändert hat.

    Rohe "\\n"/"\\r" im übersetzten Text werden bewusst NICHT als LineBreak
    interpretiert (früherer Bug: text.split("\\n") übernahm jeden vom
    Provider eingefügten Zeilenumbruch 1:1 als echten LineBreak-Run, obwohl
    z.B. ChatGPT beim Reformatieren gern an Run-Grenzen umbricht, die im
    Original keine Entsprechung haben). Nur das explizite <lb/>-Tag (siehe
    mask_runs) gilt als echter, aus dem Original stammender Zeilenumbruch -
    ein eingestreutes rohes "\\n" wird zu einem Leerzeichen normalisiert.
    """
    out: List[Run] = []
    found_ids: set = set()
    text = (translated_text or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    pos = 0
    for m in _INLINE_TAG_RE.finditer(text):
        if m.start() > pos:
            seg = _unescape(text[pos:m.start()])
            if seg:
                out.append(TextRun(kind="TextRun", text=seg))
        if m.group("ce_id") is not None:
            ce_id = m.group("ce_id")
            er = emoji_by_id.get(ce_id)
            if er is not None:
                found_ids.add(ce_id)
                out.append(EmojiRun(kind="EmojiRun", document_id=er.document_id, height_em=er.height_em))
        elif m.group("lb") is not None:
            out.append(LineBreak(kind="LineBreak"))
        elif m.group("href") is not None:
            href = _unescape_attr(m.group("href"))
            # Inhalt zwischen <a href="...">...</a> kann selbst noch
            # Format-Tags (b/i/...) oder <ce/>-Platzhalter enthalten (z.B.
            # ein fett dargestellter Link) - rekursiv aufgelöst, href wird
            # danach auf jeden entstandenen TextRun übertragen.
            inner_runs, inner_found = unmask_to_runs(m.group("link_inner"), emoji_by_id)
            found_ids |= inner_found
            for ir in inner_runs:
                out.append(_dc_replace(ir, href=href) if isinstance(ir, TextRun) else ir)
        else:
            tag = m.group("tag")
            inner = _unescape(m.group("inner"))
            flags = {flag: (tag == t) for flag, t in _TAG_FOR_FLAG}
            if inner:
                out.append(TextRun(kind="TextRun", text=inner, **flags))
        pos = m.end()
    if pos < len(text):
        seg = _unescape(text[pos:])
        if seg:
            out.append(TextRun(kind="TextRun", text=seg))
    return out, found_ids
