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
  - href/Link-Ziel  -> <a id="N">...</a>  (Ziel-URL wird NICHT im Tag
                        mitgeschickt, sondern separat in link_by_id
                        gehalten - dieselbe id-statt-Wert-Strategie wie bei
                        Custom-Emojis, aus demselben Grund: manche Provider
                        (v.a. LLM-basierte wie ChatGPT) interpretieren ein
                        URL-artiges Linktoken wie "lobstr.co" als zwei
                        Wörter, getrennt durch den Punkt, und reißen dabei
                        das <a>-Tag versehentlich in zwei separate <a>-Tags
                        mit identischer id/href auseinander. unmask_to_runs
                        prüft deshalb nach der Übersetzung, ob jede id GENAU
                        EINMAL vorkommt - kommt sie mehrfach vor, gilt der
                        Link als vom Provider zerrissen und wird durch den
                        unveränderten (unübersetzten) Original-Run ersetzt,
                        statt das zerrissene Teilergebnis zu rekonstruieren)
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

from ..logging_setup import get_logger
from ..runs import EmojiRun, LineBreak, Run, TextRun

logger = get_logger(__name__)

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


def mask_runs(runs: List[Run]) -> Tuple[str, Dict[str, EmojiRun], Dict[str, TextRun]]:
    """Serialisiert Runs zu getaggtem Text + Emoji-Platzhalter-Map + Link-Map
    (siehe Moduldocstring für die id-statt-Wert-Strategie bei Links)."""
    parts: List[str] = []
    emoji_by_id: Dict[str, EmojiRun] = {}
    link_by_id: Dict[str, TextRun] = {}
    next_emoji_id = 0
    next_link_id = 0
    for r in runs:
        if isinstance(r, LineBreak):
            parts.append("<lb/>")
        elif isinstance(r, EmojiRun):
            key = str(next_emoji_id)
            next_emoji_id += 1
            emoji_by_id[key] = r
            parts.append(f'<ce id="{key}"/>')
        elif isinstance(r, TextRun):
            text = _escape(r.text)
            open_tags = "".join(f"<{tag}>" for flag, tag in _TAG_FOR_FLAG if getattr(r, flag, False))
            close_tags = "".join(f"</{tag}>" for flag, tag in reversed(_TAG_FOR_FLAG) if getattr(r, flag, False))
            body = f"{open_tags}{text}{close_tags}"
            if r.href:
                key = str(next_link_id)
                next_link_id += 1
                link_by_id[key] = r
                body = f'<a id="{key}">{body}</a>'
            parts.append(body)
        # ImageRun kommt in Message-Body-Runs (build_runs_from_twe) nicht vor.
    return "".join(parts), emoji_by_id, link_by_id


_INLINE_TAG_RE = re.compile(
    r'<ce id="(?P<ce_id>\d+)"\s*/>'
    r'|(?P<lb><lb\s*/>)'
    r'|<a-fallback id="(?P<fallback_id>\d+)"\s*/>'
    r'|<a id="(?P<link_id>\d+)">(?P<link_inner>.*?)</a>'
    r'|<(?P<tag>b|i|u|s|code|spoiler)>(?P<inner>.*?)</(?P=tag)>',
    re.DOTALL,
)

_LINK_TAG_SCAN_RE = re.compile(r'<a id="(?P<id>\d+)">.*?</a>', re.DOTALL)


def _repair_split_link_tags(text: str, link_by_id: Dict[str, TextRun]) -> str:
    """Prüft, ob jedes <a id="N">...</a>-Tag GENAU EINMAL im übersetzten Text
    vorkommt. Ein Provider (v.a. LLM-basiert, z.B. ChatGPT) kann ein
    URL-artiges Linktoken wie "lobstr.co" als zwei Wörter (getrennt durch den
    Punkt) interpretieren und dabei das <a>-Tag versehentlich in zwei
    separate <a>-Tags mit identischer id auftrennen - typischerweise mit
    vom Provider selbst eingestreuten <lb/>-Tags zwischen den Bruchstücken.
    Kommt eine id nicht genau einmal vor, gilt der gesamte Bereich vom ersten
    bis zum letzten Fundort (inklusive allem dazwischen, z.B. jener <lb/>-
    Tags) als zerrissen und wird durch einen einzigen
    <a-fallback id="N"/>-Platzhalter ersetzt, der beim Parsen den
    unveränderten (unübersetzten) Original-Run einsetzt - statt zu
    versuchen, das zerrissene Teilergebnis samt Umgebung zu rekonstruieren."""
    matches_by_id: Dict[str, List["re.Match[str]"]] = {}
    for m in _LINK_TAG_SCAN_RE.finditer(text):
        matches_by_id.setdefault(m.group("id"), []).append(m)
    broken = {lid: ms for lid, ms in matches_by_id.items() if len(ms) > 1}
    if not broken:
        return text
    spans = []
    for lid, ms in broken.items():
        spans.append((ms[0].start(), ms[-1].end(), lid, len(ms)))
        logger.warning(
            "Link (id=%s) wurde vom Übersetzungs-Provider fehlerhaft aufgetrennt "
            "(%d Fundstellen statt 1) - Original wird unverändert (unübersetzt) übernommen.",
            lid, len(ms),
        )
    # Rückwärts nach Startposition ersetzen, damit bereits verarbeitete
    # Indizes durch vorherige Ersetzungen nicht ungültig werden.
    for start, end, lid, _n in sorted(spans, key=lambda s: s[0], reverse=True):
        text = text[:start] + f'<a-fallback id="{lid}"/>' + text[end:]
    return text


def unmask_to_runs(
    translated_text: str,
    emoji_by_id: Dict[str, EmojiRun],
    link_by_id: Dict[str, TextRun],
) -> Tuple[List[Run], set]:
    """Kehrt mask_runs() um. Gibt (Runs, gefundene_ids) zurück, damit der
    Aufrufer prüfen kann, ob der Provider Platzhalter verloren/verändert hat -
    Emoji-ids bare ("0", "1", ...), Link-ids mit "link:"-Präfix (z.B.
    "link:0"), um Namenskollisionen zu vermeiden.

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
    text = _repair_split_link_tags(text, link_by_id)
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
        elif m.group("fallback_id") is not None:
            lid = m.group("fallback_id")
            run = link_by_id.get(lid)
            if run is not None:
                found_ids.add(f"link:{lid}")
                # Zusätzlicher Marker (siehe translate_runs in service.py),
                # damit der Aufrufer eine sichtbare Warnung ausgeben kann -
                # das Logging in _repair_split_link_tags landet nur in der
                # Log-Datei, nicht in der UI.
                found_ids.add(f"link-repaired:{lid}")
                out.append(run)
        elif m.group("link_id") is not None:
            lid = m.group("link_id")
            run = link_by_id.get(lid)
            href = run.href if run is not None else None
            # Inhalt zwischen <a id="N">...</a> kann selbst noch Format-Tags
            # (b/i/...) oder <ce/>-Platzhalter enthalten (z.B. ein fett
            # dargestellter Link) - rekursiv aufgelöst, href wird danach auf
            # jeden entstandenen TextRun übertragen.
            inner_runs, inner_found = unmask_to_runs(m.group("link_inner"), emoji_by_id, link_by_id)
            found_ids |= inner_found
            found_ids.add(f"link:{lid}")
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
