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

from typing import Optional

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
        # Mögliche Formen:
        # - /c/<chan>/<msg>
        # - /c/<chan>/<topic>/<msg>
        if len(parts) >= 3 and parts[1].isdigit() and parts[-1].isdigit():
            return int('-100' + parts[1]), int(parts[-1])
        raise ValueError("Ungültiger c-Link: " + link)
    return parts[0], int(parts[1])


def parse_topic_from_link(link: str) -> Tuple[Optional[int], Optional[Peer]]:
    """Extrahiert optional eine Topic-ID aus einem Telegram-Link.

    Unterstützte Formen:
      - https://t.me/c/<chan>/<topic>/<msg>
      - https://t.me/c/<chan>/<topic>

    Rückgabe:
      (topic_id, peer):
        - topic_id: Topic-/Thread-ID oder None, falls kein Topic erkennbar
        - peer: die Chat-/Channel-ID (als Peer), falls ermittelbar
    """
    try:
        u = urlparse(link.strip())
    except Exception:
        return None, None
    parts = [p for p in u.path.split('/') if p]
    if not parts:
        return None, None
    if parts[0] != 'c' or len(parts) < 3:
        return None, None
    # /c/<chan>/<topic>/[msg?]
    chan_part = parts[1]
    topic_part = parts[2]
    if not chan_part.isdigit() or not topic_part.isdigit():
        return None, None
    try:
        peer: Peer = int('-100' + chan_part)
        topic_id = int(topic_part)
        return topic_id, peer
    except Exception:
        return None, None


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

