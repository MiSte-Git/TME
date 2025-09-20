"""
fetch: Nachrichten sammeln und als JSON speichern
Ziel gemäß docs/emoji-odt-kontext.md → data/messages/<chat>_<msg_id>.json

Dies ist ein Platzhalter mit Schnittstellen; Implementierung kann später Telethon nutzen.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json, re
from typing import List, Dict, Any, Tuple, Union
from urllib.parse import urlparse

from telethon import TelegramClient, functions

Peer = Union[str, int]

@dataclass
class Entity:
    type: str
    offset: int
    length: int
    url: str | None = None
    document_id: str | None = None

@dataclass
class MessageRecord:
    chat: str
    message_id: int
    date_iso: str
    text: str
    entities: List[Entity]


def save_message_json(dst_dir: Path, rec: MessageRecord) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    p = dst_dir / f"{rec.chat}_{rec.message_id}.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(asdict(rec), f, ensure_ascii=False, indent=2)
    return p


def parse_channel(s: str) -> Peer:
    s = s.strip()
    if s.startswith("@"):
        return s[1:]
    if "t.me/" in s:
        u = urlparse(s)
        parts = [p for p in u.path.split("/") if p]
        if not parts:
            return s
        if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
            return int("-100" + parts[1])
        return parts[0]
    return s


def is_message_link(s: str) -> bool:
    try:
        u = urlparse(s.strip())
    except Exception:
        return False
    parts = [p for p in u.path.split('/') if p]
    if not parts:
        return False
    # t.me/c/<chanId>/<msgId>
    if parts[0] == 'c' and len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
        return True
    # t.me/<username>/<msgId>
    return len(parts) >= 2 and parts[-1].isdigit()


def parse_link(link: str) -> Tuple[Peer, int]:
    u = urlparse(link.strip())
    parts = [p for p in u.path.split('/') if p]
    if not parts:
        raise ValueError("Ungültiger Link: " + link)
    if parts[0] == 'c':
        return int('-100' + parts[1]), int(parts[2])
    return parts[0], int(parts[1])


async def ensure_join_channel(client: TelegramClient, entity: Any) -> None:
    """Versucht, dem Kanal beizutreten; Fehler werden ignoriert."""
    try:
        await client(functions.channels.JoinChannelRequest(channel=entity))
    except Exception:
        pass


def collect_from_urls(urls: List[str]) -> List[MessageRecord]:
    """
    Platzhalter: Hier später Telethon einbauen. Aktuell nur Interface.
    """
    raise NotImplementedError("fetch.collect_from_urls: Telethon-Implementierung fehlt")
