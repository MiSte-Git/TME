from __future__ import annotations
from pathlib import Path
from typing import Dict, Tuple
import json

from telethon import TelegramClient, types

from .fetch import parse_link, ensure_join_channel
from .assets import load_custom_emoji_alts, get_custom_emoji_cache
from .runs import build_runs_from_twe, EmojiRun, TextRun, LineBreak, RunsRecord


def _load_letter_map(path: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Lädt letter_map.json und liefert (letter->doc_id, doc_id->letter)."""
    letters_to_doc: Dict[str, str] = {}
    doc_to_letters: Dict[str, str] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in data.items():
            doc_id = str(v.get("document_id", "")).strip()
            if not doc_id:
                continue
            letters_to_doc[str(k)] = doc_id
            doc_to_letters[doc_id] = str(k)
    return letters_to_doc, doc_to_letters


def _record_to_plain_text(rec: RunsRecord, doc_to_letters: Dict[str, str], ce_alt_cache: Dict[int, str]) -> str:
    parts: list[str] = []
    for r in rec.runs:
        if isinstance(r, LineBreak):
            parts.append("\n")
        elif isinstance(r, TextRun):
            parts.append(r.text)
        elif isinstance(r, EmojiRun):
            # doc_id -> letter if present
            doc_id = r.document_id
            letter = doc_to_letters.get(doc_id)
            if letter:
                parts.append(letter)
            else:
                # Fallback: Alt-Text, sonst leeres Zeichen
                try:
                    alt = ce_alt_cache.get(int(doc_id))
                except Exception:
                    alt = None
                parts.append(alt or "")
    return "".join(parts)


async def extract_plain_from_links(api_id: int, api_hash: str, links_file: Path, out_dir: Path = Path("data/plain"), letter_map_path: Path = Path("data/letter_map.json")) -> int:
    """
    Erzeugt Plaintext-Dateien unter data/plain/<peer>_<msg_id>.txt aus den Runs der Original-Nachrichten.
    Nutzt letter_map.json (doc_id->letter) für CE→Buchstabe; sonst Alt-Text.
    Rückgabe: Anzahl erzeugter Dateien.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _, doc_to_letters = _load_letter_map(letter_map_path)

    count = 0
    async with TelegramClient("tg_session", api_id, api_hash) as client:
        for raw in links_file.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if not s or s.startswith('#'):
                continue
            try:
                peer_raw, msg_id = parse_link(s)
                entity = await client.get_entity(peer_raw)
                await ensure_join_channel(client, entity)
                msg = await client.get_messages(entity, ids=msg_id)
                if not msg:
                    continue
                twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                await load_custom_emoji_alts(client, twe)
                runs = build_runs_from_twe(twe)
                rec = RunsRecord(chat=str(peer_raw), message_id=msg.id, runs=runs)
                plain = _record_to_plain_text(rec, doc_to_letters, get_custom_emoji_cache())
                dst = out_dir / f"{str(peer_raw)}_{msg.id}.txt"
                dst.write_text(plain, encoding="utf-8")
                count += 1
            except Exception:
                continue
    return count
