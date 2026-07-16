"""
runs: Aus Telegram-Text+Entities Runs erzeugen und speichern
Ziel: data/runs.original/<chat>_<msg_id>.json
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from typing import List, Dict, Any, Tuple

from telethon import types
from telethon.utils import add_surrogate, del_surrogate

@dataclass
class ImageRun:
    kind: str
    path: str
    width_cm: float = 15.0

@dataclass
class TextRun:
    kind: str
    text: str
    href: str | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    spoiler: bool = False

@dataclass
class EmojiRun:
    kind: str
    document_id: str
    height_em: float = 1.1

@dataclass
class LineBreak:
    kind: str

Run = TextRun | EmojiRun | LineBreak | ImageRun


def _segment_bounds(text: str, entities: List[Any]) -> List[Tuple[int, int]]:
    s = add_surrogate(text)
    bounds = {0, len(s)}
    for e in entities or []:
        a, b = int(getattr(e, "offset", 0)), int(getattr(e, "offset", 0) + getattr(e, "length", 0))
        if 0 <= a < b <= len(s):
            bounds.add(a); bounds.add(b)
    idx = sorted(bounds)
    return list(zip(idx, idx[1:]))


def build_runs_from_twe(twe: types.TextWithEntities, custom_emoji_map: Dict[int, str] | None = None, default_emoji_height_em: float = 1.1) -> List[Run]:
    text = twe.text or ""
    ents = list(twe.entities or [])
    runs: List[Run] = []

    for i, j in _segment_bounds(text, ents):
        if i == j:
            continue
        seg_sur = add_surrogate(text)[i:j]
        seg = del_surrogate(seg_sur)

        # Prüfen, ob Segment ein Custom-Emoji vollständig abdeckt
        ce = None
        for e in ents:
            if getattr(e, "offset", 0) <= i and (getattr(e, "offset", 0) + getattr(e, "length", 0)) >= j:
                if type(e).__name__ == "MessageEntityCustomEmoji":
                    ce = e
                    break
        if ce is not None:
            doc_id = getattr(ce, "document_id", None)
            if doc_id is not None:
                runs.append(EmojiRun(kind="EmojiRun", document_id=str(doc_id), height_em=default_emoji_height_em))
            else:
                # Fallback als Text
                runs.append(TextRun(kind="TextRun", text=seg))
            continue

        # Style-Flags und Link ermitteln
        names = set()
        href = None
        for e in ents:
            if getattr(e, "offset", 0) <= i and (getattr(e, "offset", 0) + getattr(e, "length", 0)) >= j:
                names.add(type(e).__name__)
                if type(e).__name__ in ("MessageEntityTextUrl", "TextEntityTextUrl"):
                    href = getattr(e, "url", None) or None
        if ("MessageEntityUrl" in names or "TextEntityUrl" in names) and href is None:
            href = seg
        flags = dict(
            bold=("MessageEntityBold" in names or "TextEntityBold" in names),
            italic=("MessageEntityItalic" in names or "TextEntityItalic" in names),
            underline=("MessageEntityUnderline" in names or "TextEntityUnderline" in names),
            strike=("MessageEntityStrike" in names or "TextEntityStrike" in names),
            code=any(n in names for n in ("MessageEntityCode","TextEntityCode","MessageEntityPre","TextEntityPre")),
            spoiler=("MessageEntitySpoiler" in names or "TextEntitySpoiler" in names),
        )

        # Normale Texte mit \n in TextRun/LineBreak zerlegen
        parts = seg.split("\n")
        pending_break = False
        for idx, part in enumerate(parts):
            if part:
                runs.append(TextRun(kind="TextRun", text=part, href=href,
                                    bold=flags["bold"], italic=flags["italic"], underline=flags["underline"],
                                    strike=flags["strike"], code=flags["code"], spoiler=flags["spoiler"]))
                pending_break = False
            if idx < len(parts) - 1:
                if not pending_break:
                    runs.append(LineBreak(kind="LineBreak"))
                    pending_break = True

    return runs

@dataclass
class RunsRecord:
    chat: str
    message_id: int
    runs: List[Run]
    meta: Dict[str, Any] | None = None


@dataclass
class RecordPair:
    """Original + (optionale) Übersetzung derselben Nachricht, explizit
    gepaart über message_id - Grundlage für das side_by_side-Layout
    (odt_writer.write_odt_for_record_pairs). translation ist None, wenn keine
    Übersetzung vorliegt (Provider-Fehler, Nachricht war schon in Zielsprache)."""
    original: RunsRecord
    translation: RunsRecord | None = None


_RUN_KIND_CLASSES = {
    "TextRun": TextRun,
    "EmojiRun": EmojiRun,
    "LineBreak": LineBreak,
    "ImageRun": ImageRun,
}


def run_to_dict(r: Run) -> Dict[str, Any]:
    return asdict(r)


def run_from_dict(d: Dict[str, Any]) -> Run:
    kind = d.get("kind")
    cls = _RUN_KIND_CLASSES.get(kind)
    if cls is None:
        raise ValueError(f"Unbekannte Run-kind: {kind!r}")
    # Nur bekannte Felder übernehmen (robust gegenüber älteren/neueren Store-Versionen)
    valid_fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kwargs = {k: v for k, v in d.items() if k in valid_fields}
    return cls(**kwargs)  # type: ignore[return-value]


def _meta_to_dict(meta: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """Wandelt meta in ein JSON-taugliches Dict; Run-Listen (z.B. header_runs)
    werden dabei rekursiv wie rec.runs serialisiert."""
    if not meta:
        return None
    out: Dict[str, Any] = {}
    for k, v in meta.items():
        if isinstance(v, list) and v and all(hasattr(x, "kind") for x in v):
            out[k] = [run_to_dict(x) for x in v]
        else:
            out[k] = v
    return out or None


def _meta_from_dict(meta: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not meta:
        return None
    out: Dict[str, Any] = {}
    for k, v in meta.items():
        if isinstance(v, list) and v and all(isinstance(x, dict) and x.get("kind") in _RUN_KIND_CLASSES for x in v):
            out[k] = [run_from_dict(x) for x in v]
        else:
            out[k] = v
    return out or None


def record_to_dict(rec: RunsRecord) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "chat": rec.chat,
        "message_id": rec.message_id,
        "runs": [run_to_dict(r) for r in rec.runs],
    }
    meta_dict = _meta_to_dict(rec.meta)
    if meta_dict:
        data["meta"] = meta_dict
    return data


def record_from_dict(data: Dict[str, Any]) -> RunsRecord:
    return RunsRecord(
        chat=str(data.get("chat", "")),
        message_id=int(data.get("message_id", 0)),
        runs=[run_from_dict(r) for r in (data.get("runs") or [])],
        meta=_meta_from_dict(data.get("meta")),
    )


def save_runs_json(dst_dir: Path, rec: RunsRecord) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    p = dst_dir / f"{rec.chat}_{rec.message_id}.json"
    p.write_text(json.dumps(record_to_dict(rec), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_runs_json(path: Path) -> RunsRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    return record_from_dict(data)
