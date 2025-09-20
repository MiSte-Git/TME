from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import json

from .runs import RunsRecord, EmojiRun, TextRun, LineBreak
from .odt_writer import write_odt_for_records
from .fetch import parse_link


def _load_letter_map(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            doc_id = str(v.get("document_id", "")).strip()
            docs = v.get("document_ids")
            if isinstance(docs, list) and docs:
                doc_id = str(docs[0] if docs[0] is not None else "").strip() or doc_id
            if doc_id:
                out[str(k)] = doc_id
    return out


def _text_to_runs(text: str, letter_to_doc: Dict[str, str]) -> List[TextRun | EmojiRun | LineBreak]:
    runs: List[TextRun | EmojiRun | LineBreak] = []
    for ch in text:
        if ch == '\n':
            runs.append(LineBreak(kind="LineBreak"))
            continue
        doc_id = letter_to_doc.get(ch)
        if doc_id:
            runs.append(EmojiRun(kind="EmojiRun", document_id=doc_id))
        else:
            runs.append(TextRun(kind="TextRun", text=ch))
    return runs


def recompose_to_odt(links_file: Path, out_lang: str, output_dir: Path = Path("output"), letter_map_path: Path = Path("data/letter_map.json")) -> Path:
    """
    Liest data/translated/<peer>_<msg_id>.txt (wenn vorhanden) und erzeugt ein ODT mit rekonstituierten Buchstaben-Emojis.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    letter_to_doc = _load_letter_map(letter_map_path)

    records: List[RunsRecord] = []
    for raw in links_file.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        try:
            peer_raw, msg_id = parse_link(s)
            p = Path("data/translated") / f"{str(peer_raw)}_{msg_id}.txt"
            if not p.exists():
                # Fallback: versuche data/plain
                p = Path("data/plain") / f"{str(peer_raw)}_{msg_id}.txt"
                if not p.exists():
                    continue
            txt = p.read_text(encoding="utf-8")
            runs = _text_to_runs(txt, letter_to_doc)
            records.append(RunsRecord(chat=f"{str(peer_raw)} - {out_lang.upper()}", message_id=int(msg_id), runs=runs))
        except Exception:
            continue

    out_path = output_dir / f"recompose_{out_lang.upper()}.odt"
    styles = {
        "paragraph": {"base": "P.Base"},
        "text": {"base": "T.Base"},
        "graphic": {"inline_emoji": "G.InlineEmoji"},
    }
    write_odt_for_records(records, out_path, styles)
    return out_path
