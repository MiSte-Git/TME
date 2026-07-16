from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from pathlib import Path
from telethon import TelegramClient

DEFAULT_LOCAL_TZ = "Europe/Zurich"


@dataclass
class CollectedMessage:
    title: str
    entity: Any
    message: Any
    subheading: Optional[str] = None
    link: Optional[str] = None
    topic_id: Optional[int] = None  # gewünschtes Topic laut Section/Schedule
    actual_topic_id: Optional[int] = None  # tatsächlich erkannter Thread der Nachricht
    channel_label: Optional[str] = None  # section.title; für Kanal-Label beim chronologischen Interleaving


def _format_heading(date_iso: str, title: str) -> str:
    return f"{date_iso}  -  {title}".strip()


def _build_message_link(
    entity: Any,
    message: Any,
    original_link: Optional[str] = None,
    topic_id: Optional[int] = None,
) -> Optional[str]:
    """Aus runner_schedule.py ausgelagert."""
    if original_link:
        return original_link
    try:
        msg_id = int(getattr(message, "id", 0))
    except Exception:
        msg_id = 0
    if not msg_id:
        return original_link
    username = getattr(entity, "username", None) or getattr(entity, "usernames", None)
    if isinstance(username, (list, tuple)) and username:
        username = getattr(username[0], "username", None)
    if isinstance(username, str) and username:
        return f"https://t.me/{username}/{msg_id}"
    try:
        channel_id = getattr(entity, "id", None)
        if channel_id is None:
            return original_link
        channel_id_int = int(channel_id)
    except Exception:
        return original_link
    if channel_id_int < 0:
        channel_id_int = -channel_id_int
    chan_str = str(channel_id_int)
    if chan_str.startswith("100") and len(chan_str) > 3:
        chan_str = chan_str[3:]
    if topic_id is not None:
        return f"https://t.me/c/{chan_str}/{int(topic_id)}/{msg_id}"
    return f"https://t.me/c/{chan_str}/{msg_id}"


# Die folgenden drei Funktionen binden wir einfach wieder an runner_by_ids,
# so dass sie weiterhin von dort kontrolliert werden.

from . import runner_by_ids as _rbi  # noqa: E402


_with_retries = _rbi._with_retries
ensure_join_channel = _rbi.ensure_join_channel  # falls dort definiert, sonst anpassen
_DEBUG_DUMP_ENTITIES = False  # bei Bedarf auf True setzen