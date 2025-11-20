from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from telethon import types
from telethon.client.telegramclient import TelegramClient

from schedule_json import ScheduleDocument
from .fetch import parse_channel, parse_link
from .message_filters import (
    build_day_time_range,
    fetch_messages_for_section_day,
    filter_messages_for_topic,
)
from .runner_base_imports import (
    CollectedMessage,
    DEFAULT_LOCAL_TZ,
    _build_message_link,
    _format_heading,
    _with_retries,
    ensure_join_channel,
    _DEBUG_DUMP_ENTITIES,
)
from .topic_utils import extract_topic_from_section

DEBUG_FETCH = False  # Debug-Ausgaben für Sammellogik standardmäßig aus

async def _ensure_entity(client: TelegramClient, raw: Any) -> Any:
    entity = await _with_retries("get_entity", lambda: client.get_entity(raw))
    if entity:
        await ensure_join_channel(client, entity)
    return entity


def _get_default_entity_key(channel: Optional[str]) -> Optional[str]:
    if not channel:
        return None
    return str(channel)


async def collect_messages_for_schedule(
    client: TelegramClient,
    schedule: ScheduleDocument,
    local_tz: Optional[str],
) -> Tuple[List[CollectedMessage], Set[str]]:
    from zoneinfo import ZoneInfo

    tz_name = local_tz or DEFAULT_LOCAL_TZ or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc  # aktuell nicht weiter genutzt, aber vorbereitet

    collected: List[CollectedMessage] = []
    used_doc_ids: Set[str] = set()
    debug_dir = Path("data/debug")
    if _DEBUG_DUMP_ENTITIES:
        debug_dir.mkdir(parents=True, exist_ok=True)

    default_entity_cache: Dict[str, Any] = {}

    for section in schedule.sections:
        heading = _format_heading(section.date.strftime("%Y-%m-%d"), section.title)
        subheading = section.subheading or None

        # 1. Direkt verlinkte Nachrichten (section.links)
        links = [lnk for lnk in (section.links or []) if lnk]
        if links:
            for link in links:
                try:
                    peer_raw, msg_id = parse_link(link)
                    entity = await _ensure_entity(client, peer_raw)
                    if not entity:
                        continue

                    msg = await _with_retries(
                        "get_messages",
                        lambda: client.get_messages(entity, ids=msg_id),
                    )
                    if not msg:
                        continue

                    link_url = _build_message_link(entity, msg, original_link=link)
                    collected.append(
                        CollectedMessage(
                            title=heading,
                            entity=entity,
                            message=msg,
                            subheading=subheading,
                            link=link_url,
                            topic_id=None,
                        )
                    )

                    # Custom-Emoji-Dokument-IDs sammeln (für spätere PNG-Erzeugung)
                    for e in (msg.entities or []):
                        if isinstance(e, types.MessageEntityCustomEmoji):
                            did = getattr(e, "document_id", None)
                            if did:
                                used_doc_ids.add(str(did))

                    if _DEBUG_DUMP_ENTITIES:
                        out = {
                            "title": heading,
                            "peer": str(peer_raw),
                            "message_id": int(getattr(msg, "id", 0)),
                            "text": msg.message or "",
                            "entities": [
                                {
                                    "type": type(ent).__name__,
                                    "offset": int(getattr(ent, "offset", 0)),
                                    "length": int(getattr(ent, "length", 0)),
                                    **(
                                        {
                                            "document_id": str(
                                                getattr(ent, "document_id", "")
                                            )
                                        }
                                        if isinstance(
                                            ent, types.MessageEntityCustomEmoji
                                        )
                                        else {}
                                    ),
                                }
                                for ent in (msg.entities or [])
                            ],
                        }
                        dp = debug_dir / f"entities_{str(peer_raw).replace('/', '_')}_{msg_id}.json"
                        try:
                            dp.write_text(
                                json.dumps(out, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass
                except Exception:
                    continue

            # Wenn Links vorhanden sind, wird für diese Section nicht zusätzlich
            # per Datum geladen (entspricht deiner bisherigen Logik).
            continue

        # 2. Fetch by date für diese Section
        default_channel = section.channel or schedule.default_channel
        topic_id, topic_source = extract_topic_from_section(section, schedule)
        key = _get_default_entity_key(default_channel)
        if key is None:
            continue

        if key not in default_entity_cache:
            raw = parse_channel(default_channel)
            entity = await _ensure_entity(client, raw)
            if not entity:
                print(f"Hinweis: Kanal '{default_channel}' konnte nicht geladen werden.")
                default_entity_cache[key] = None
            else:
                default_entity_cache[key] = entity

        entity = default_entity_cache.get(key)
        if not entity:
            continue

        day_str = section.date.strftime("%d/%m/%Y")

        # Zeitfenster pro Sektion zur tatsächlichen Filterung der Nachrichten
        start_time_val = (
            getattr(section, "start_time", None)
            or getattr(section, "startTime", None)
        )
        end_time_val = (
            getattr(section, "end_time", None)
            or getattr(section, "endTime", None)
        )

        # Dynamisches Startfenster, falls keine start_time gesetzt, aber first_message_time vorhanden
        first_msg_time = getattr(section, "first_message_time", None)
        if not start_time_val and first_msg_time is not None:
            start_time_val = first_msg_time.strftime("%H:%M:%S")

        # Nur zur Anzeige / Debug, die eigentliche Filterung macht fetch_messages_for_section_day
        start_dt, end_dt = build_day_time_range(section.date, start_time_val, end_time_val)
        start_time_str = start_time_val or start_dt.strftime("%H:%M:%S")
        end_time_str = end_time_val or end_dt.strftime("%H:%M:%S")

        if DEBUG_FETCH:
            print(
                "DEBUG _collect_messages_for_schedule:",
                "heading=", heading,
                "day_str=", day_str,
                "start_time_str=", start_time_str,
                "end_time_str=", end_time_str,
                "tz=", local_tz,
            )

        # Nachrichten für den Tag + Zeitfenster holen
        # Wenn ein Forum-Topic aus dem Default-Channel-Link extrahiert wurde
        # (https://t.me/c/<chatId>/<topicId>), filtern wir serverseitig direkt
        # auf dieses Topic über reply_to=topic_id. Damit entfallen alle
        # Heuristiken auf topic_id/top_msg_id/forum_topic_id in den Nachrichten.
        msgs = await fetch_messages_for_section_day(
            client,
            entity,
            day_str,
            local_tz=local_tz,
            start_time_str=start_time_str,
            end_time_str=end_time_str,
            topic_id=topic_id,
        )

        if DEBUG_FETCH:
            print(
                "DEBUG _collect_messages_for_schedule: fetched",
                len(msgs) if msgs is not None else 0,
                "messages for",
                heading,
            )

        # Optionaler Topic-Filter auf Nachrichtenebene wird für Forum-Topics
        # nicht mehr benötigt, wenn serverseitig bereits über reply_to=topic_id
        # gefiltert wurde. Um bestehende Logik nicht zu beeinträchtigen, lassen
        # wir den Aufruf nur noch laufen, falls kein topic_id gesetzt ist.
        if topic_id is None and msgs:
            before = len(msgs)
            msgs = filter_messages_for_topic(
                msgs,
                topic_id=topic_id,
                topic_source=topic_source,
                schedule_default_channel=getattr(schedule, "default_channel", None),
            )
            if DEBUG_FETCH:
                print(
                    "DEBUG _collect_messages_for_schedule: after topic filter:",
                    len(msgs), "kept of", before,
                    "for topic_id=", topic_id,
                )

        if not msgs:
            print(f"Hinweis: Keine Nachrichten für {heading} gefunden.")
            continue

        from .message_filters import _extract_actual_topic_id

        for msg in msgs:
            actual_tid = _extract_actual_topic_id(msg)
            link_url = _build_message_link(entity, msg, topic_id=actual_tid)
            collected.append(
                CollectedMessage(
                    title=heading,
                    entity=entity,
                    message=msg,
                    subheading=subheading,
                    link=link_url,
                    topic_id=topic_id,
                    actual_topic_id=actual_tid,
                )
            )

            # Custom-Emoji-Dokument-IDs sammeln (für spätere PNG-Erzeugung)
            for e in (msg.entities or []):
                if isinstance(e, types.MessageEntityCustomEmoji):
                    did = getattr(e, "document_id", None)
                    if did:
                        used_doc_ids.add(str(did))

            if _DEBUG_DUMP_ENTITIES:
                peer_id = getattr(entity, "id", "")
                out = {
                    "title": heading,
                    "peer": str(peer_id),
                    "message_id": int(getattr(msg, "id", 0)),
                    "text": msg.message or "",
                    "entities": [
                        {
                            "type": type(ent).__name__,
                            "offset": int(getattr(ent, "offset", 0)),
                            "length": int(getattr(ent, "length", 0)),
                            **(
                                {
                                    "document_id": str(
                                        getattr(ent, "document_id", "")
                                    )
                                }
                                if isinstance(ent, types.MessageEntityCustomEmoji)
                                else {}
                            ),
                        }
                        for ent in (msg.entities or [])
                    ],
                }
                dp = debug_dir / f"entities_{str(peer_id).replace('-', 'm')}_{msg.id}.json"
                try:
                    dp.write_text(
                        json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

    return collected, used_doc_ids