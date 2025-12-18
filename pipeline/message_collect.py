from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from telethon import types
from telethon.client.telegramclient import TelegramClient

from schedule_json import ScheduleDocument
from .fetch import parse_channel, parse_link, is_message_link
from .message_filters import (
    FetchMessagesResult,
    build_day_time_range,
    fetch_messages_for_section_day,
    filter_messages_for_topic,
    _extract_actual_topic_id,
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
from .fetch import parse_topic_from_link

DEBUG_FETCH = True  # Debug-Ausgaben für Sammellogik (für aktuelle Analyse auf True)

async def _ensure_entity(client: TelegramClient, raw: Any) -> Any:
    entity = await _with_retries("get_entity", lambda: client.get_entity(raw))
    if entity:
        await ensure_join_channel(client, entity)
    return entity


def _get_default_entity_key(channel: Optional[str]) -> Optional[str]:
    if not channel:
        return None
    return str(channel)


def _split_links_and_users(raw_links: List[str]) -> tuple[List[str], List[str]]:
    """Splits the links column into message links and @user entries (comma/semicolon separated)."""
    link_entries: List[str] = []
    user_entries: List[str] = []
    for raw in raw_links:
        for seg in re.split(r"[;,]", str(raw)):
            val = seg.strip()
            if not val:
                continue
            if val.startswith("@"):
                user_entries.append(val)
            else:
                link_entries.append(val)
    return link_entries, user_entries


def _message_key(entity: Any, msg: Any) -> Optional[tuple[Any, Any]]:
    try:
        mid = getattr(msg, "id", None)
    except Exception:
        mid = None
    if mid is None:
        return None
    try:
        chat_id = getattr(entity, "id", None)
    except Exception:
        chat_id = None
    return (chat_id, mid)


def _track_custom_emoji_ids(msg: Any, used_doc_ids: Set[str]) -> None:
    for e in (getattr(msg, "entities", None) or []):
        if isinstance(e, types.MessageEntityCustomEmoji):
            did = getattr(e, "document_id", None)
            if did:
                used_doc_ids.add(str(did))


def _get_sender_user_id(msg: Any) -> Optional[int]:
    from_id = getattr(msg, "from_id", None)
    try:
        return int(getattr(from_id, "user_id", from_id))
    except Exception:
        return None


def _mentions_username(msg: Any, username: str, user_id: Optional[int] = None) -> bool:
    """Checks Message-Entities and falls back to plain-text search for @username."""
    uname = username.strip().lstrip("@")
    if not uname:
        return False
    text = getattr(msg, "message", None) or ""
    mention_classes = (
        types.MessageEntityMentionName,
        getattr(types, "MessageEntityTextMention", types.MessageEntityMentionName),
    )
    for ent in (getattr(msg, "entities", None) or []):
        if isinstance(ent, mention_classes):
            try:
                ent_uid = getattr(ent, "user_id", None)
                if user_id is not None and ent_uid is not None and int(ent_uid) == int(user_id):
                    return True
            except Exception:
                continue
        if isinstance(ent, types.MessageEntityMention):
            try:
                start = int(getattr(ent, "offset", 0))
                length = int(getattr(ent, "length", 0))
                segment = text[start : start + length]
                if segment.lstrip("@").lower() == uname.lower():
                    return True
            except Exception:
                continue
    if text:
        pattern = re.compile(rf"(?<!\\w)@{re.escape(uname)}(?!\\w)", re.IGNORECASE)
        if pattern.search(text):
            return True
    return False


def _sort_collected_messages(msgs: List[CollectedMessage]) -> List[CollectedMessage]:
    def _key(cm: CollectedMessage) -> tuple[datetime, int]:
        msg = cm.message
        dt = getattr(msg, "date", None)
        if not isinstance(dt, datetime):
            dt = datetime.min.replace(tzinfo=timezone.utc)
        mid = getattr(msg, "id", 0) or 0
        return (dt, mid)

    return sorted(msgs, key=_key)


async def _resolve_user_id(client: TelegramClient, username: str) -> Optional[int]:
    uname = username.strip().lstrip("@")
    if not uname:
        return None
    ent = await _with_retries("get_entity(username)", lambda: client.get_entity(uname))
    if not ent:
        return None
    try:
        return int(getattr(ent, "id", None))
    except Exception:
        return None


async def collect_messages_for_schedule(
    client: TelegramClient,
    schedule: ScheduleDocument,
    local_tz: Optional[str],
) -> Tuple[List[CollectedMessage], Set[str], list[dict[str, Any]]]:
    from zoneinfo import ZoneInfo

    tz_name = local_tz or DEFAULT_LOCAL_TZ or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc  # aktuell nicht weiter genutzt, aber vorbereitet

    sections_payload: List[List[CollectedMessage]] = []
    used_doc_ids: Set[str] = set()
    resume_hints: list[dict[str, Any]] = []
    debug_dir = Path("data/debug")
    if _DEBUG_DUMP_ENTITIES:
        debug_dir.mkdir(parents=True, exist_ok=True)

    default_entity_cache: Dict[str, Any] = {}

    def _resolve_section_channel(sec: Any, sched_default: Optional[str]) -> tuple[Optional[str], Optional[int], str]:
        """Ermittelt Channel-String und Topic-ID für eine Section.

        Priorität:
        1) section.channel (falls gesetzt)
        2) sched_default
        """
        # Section-spezifischer Kanal bevorzugt
        raw_chan = getattr(sec, "channel", None)
        source = "section-channel" if raw_chan else "default-channel"
        chan_val = (str(raw_chan).strip() if raw_chan else "") or (sched_default or "")
        chan_val = chan_val.strip() or None
        topic_id_val: Optional[int] = None
        if chan_val:
            tid, _ = parse_topic_from_link(str(chan_val))
            if tid is not None:
                topic_id_val = tid
        return chan_val, topic_id_val, source

    for section in schedule.sections:
        heading = _format_heading(section.date.strftime("%Y-%m-%d"), section.title)
        subheading = section.subheading or None
        seen_message_keys: set[tuple[Any, Any]] = set()
        section_acc: List[CollectedMessage] = []
        base_count = 0
        reply_count = 0
        reply_seeds: list[tuple[Any, Any, Optional[int]]] = []
        reply_seed_keys: set[tuple[Any, Any]] = set()

        def _add_collected_msg(
            entity: Any,
            msg: Any,
            *,
            link: Optional[str] = None,
            topic_id: Optional[int] = None,
            actual_topic_id: Optional[int] = None,
            is_reply: bool = False,
        ) -> bool:
            nonlocal base_count, reply_count
            key = _message_key(entity, msg)
            if key and key in seen_message_keys:
                return False
            if key:
                seen_message_keys.add(key)
            _track_custom_emoji_ids(msg, used_doc_ids)
            cm = CollectedMessage(
                title=heading,
                entity=entity,
                message=msg,
                subheading=subheading,
                link=link,
                topic_id=topic_id,
                actual_topic_id=actual_topic_id,
            )
            section_acc.append(cm)
            if is_reply:
                reply_count += 1
            else:
                base_count += 1
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
                dp = debug_dir / f"entities_{str(peer_id).replace('-', 'm')}_{getattr(msg, 'id', 'unknown')}.json"
                try:
                    dp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            return True

        def _add_reply_seed(entity: Any, msg: Any, topic_id: Optional[int] = None) -> None:
            key = _message_key(entity, msg)
            if key is None or key in reply_seed_keys:
                return
            reply_seed_keys.add(key)
            reply_seeds.append((entity, msg, topic_id))

        # 1. Links-Spalte kann jetzt Links UND @user enthalten (Komma/Semikolon getrennt).
        raw_links = [lnk for lnk in (section.links or []) if lnk]
        link_entries, user_entries = _split_links_and_users(raw_links)

        # Direkt verlinkte Nachrichten (bestehende Logik bleibt bestehen)
        if link_entries:
            for link in link_entries:
                if not is_message_link(link):
                    continue
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
                    actual_tid = _extract_actual_topic_id(msg)
                    link_url = _build_message_link(entity, msg, original_link=link, topic_id=actual_tid)
                    _add_collected_msg(
                        entity,
                        msg,
                        link=link_url,
                        topic_id=None,
                        actual_topic_id=actual_tid,
                    )
                    _add_reply_seed(entity, msg, topic_id=actual_tid)
                except Exception:
                    continue

            if not user_entries:
                # Nur Links → kein zusätzlicher Date-Fetch (Legacy-Verhalten)
                section_sorted = _sort_collected_messages(section_acc)
                sections_payload.append(section_sorted)
                if DEBUG_FETCH:
                    print(f"Abschnitt '{heading}': basis={base_count}, replies={reply_count}, total={len(section_sorted)}")
                continue

        needs_date_fetch = section.fetch_by_date or bool(user_entries)
        if not needs_date_fetch:
            section_sorted = _sort_collected_messages(section_acc)
            sections_payload.append(section_sorted)
            if DEBUG_FETCH:
                print(f"Abschnitt '{heading}': basis={base_count}, replies={reply_count}, total={len(section_sorted)}")
            continue

        # 2. Fetch by date für diese Section (auch wenn user_entries gesetzt sind)
        chan_val, topic_id_explicit, source_kind = _resolve_section_channel(section, schedule.default_channel)
        topic_id, topic_source = extract_topic_from_section(section, schedule)
        if topic_id_explicit is not None:
            topic_id = topic_id_explicit
            topic_source = chan_val
        key = _get_default_entity_key(chan_val)
        if key is None:
            section_sorted = _sort_collected_messages(section_acc)
            sections_payload.append(section_sorted)
            if DEBUG_FETCH:
                print(f"Abschnitt '{heading}': basis={base_count}, replies={reply_count}, total={len(section_sorted)}")
            continue

        if key not in default_entity_cache:
            raw = parse_channel(chan_val)
            entity = await _ensure_entity(client, raw)
            if not entity:
                print(f"Hinweis: Kanal '{chan_val}' konnte nicht geladen werden.")
                default_entity_cache[key] = None
            else:
                default_entity_cache[key] = entity

        entity = default_entity_cache.get(key)
        if not entity:
            section_sorted = _sort_collected_messages(section_acc)
            sections_payload.append(section_sorted)
            if DEBUG_FETCH:
                print(f"Abschnitt '{heading}': basis={base_count}, replies={reply_count}, total={len(section_sorted)}")
            continue

        day_str = section.date.strftime("%d/%m/%Y")
        if DEBUG_FETCH:
            print(f"[DEBUG] Section '{section.title}': using peer={chan_val} topic_id={topic_id} (from {source_kind})")

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
            print(
                f"[DEBUG] Section '{section.title}': "
                f"section.channel={getattr(section, 'channel', None)!r}, "
                f"default_channel={getattr(schedule, 'default_channel', None)!r}, "
                f"resolved_chan_val={chan_val!r}, topic_id={topic_id!r}, "
                f"date_from={start_dt}, date_to={end_dt}, "
                f"username_filter={(user_entries or [])}"
            )

        # Nachrichten für den Tag + Zeitfenster holen
        result: FetchMessagesResult = await fetch_messages_for_section_day(
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
                len(result.messages) if result and result.messages is not None else 0,
                "messages for",
                heading,
            )
        if DEBUG_FETCH:
            try:
                count_msg = len(result.messages) if result and result.messages is not None else 0
            except Exception:
                count_msg = 0
            print(f"[DEBUG] Section '{section.title}': loaded {count_msg} messages in date mode")

        if result.resume_hint:
            resume_hints.append(
                {
                    "section": heading,
                    "hint": result.resume_hint,
                    "error": result.error_info,
                }
            )

        msgs = result.messages
        if DEBUG_FETCH:
            base_raw_count = len(msgs) if msgs is not None else 0
            print(f"[DEBUG] Section '{section.title}': base date fetch (ohne User-Filter) -> {base_raw_count} messages")
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

        msgs = msgs or []
        if not msgs:
            print(f"Hinweis: Keine Nachrichten für {heading} gefunden.")
            section_sorted = _sort_collected_messages(section_acc)
            sections_payload.append(section_sorted)
            if DEBUG_FETCH:
                print(f"Abschnitt '{heading}': basis={base_count}, replies={reply_count}, total={len(section_sorted)}")
            continue

        # Option A: klassische fetch_by_date Sektion → alle Messages sammeln (Legacy)
        if section.fetch_by_date:
            for msg in msgs:
                actual_tid = _extract_actual_topic_id(msg)
                link_url = _build_message_link(entity, msg, topic_id=actual_tid)
                _add_collected_msg(
                    entity,
                    msg,
                    link=link_url,
                    topic_id=topic_id,
                    actual_topic_id=actual_tid,
                )

        # Option B: neue User-Filter (@username in Links-Spalte)
        resolved_users: Dict[str, int] = {}
        for uname in user_entries:
            if uname in resolved_users:
                continue
            uid = await _resolve_user_id(client, uname)
            if uid is None:
                print(f"Hinweis: Benutzer '{uname}' konnte nicht aufgelöst werden.")
                continue
            resolved_users[uname] = uid

        if resolved_users:
            base_seen: set[tuple[Any, Any]] = set()
            target_users_dbg = {uname: uid for uname, uid in resolved_users.items()}
            for msg in msgs:
                msg_key = _message_key(entity, msg)
                if msg_key and msg_key in base_seen:
                    continue
                sender_uid = _get_sender_user_id(msg)
                matched = False
                for uname, target_uid in resolved_users.items():
                    if sender_uid is not None and sender_uid == target_uid:
                        matched = True
                        break
                    if _mentions_username(msg, uname, target_uid):
                        matched = True
                        break
                if matched:
                    if msg_key:
                        base_seen.add(msg_key)
                    actual_tid = _extract_actual_topic_id(msg)
                    link_url = _build_message_link(entity, msg, topic_id=actual_tid)
                    _add_collected_msg(
                        entity,
                        msg,
                        link=link_url,
                        topic_id=topic_id,
                        actual_topic_id=actual_tid,
                    )
                    _add_reply_seed(entity, msg, topic_id=actual_tid)
            if DEBUG_FETCH:
                filtered_count = len(base_seen)
                print(f"[DEBUG] Section '{section.title}': user/mention filter -> {filtered_count} messages (target_users={target_users_dbg})")

        # Replies für alle relevanten Basis-Nachrichten laden (Links + User)
        for seed_entity, base_msg, seed_topic in reply_seeds:
            base_id = getattr(base_msg, "id", None)
            if base_id is None:
                continue
            queue: list[tuple[Any, Optional[int]]] = [(base_msg, seed_topic)]
            processed: set[tuple[Any, Any]] = set()
            while queue:
                current_msg, current_topic = queue.pop(0)
                current_key = _message_key(seed_entity, current_msg)
                if current_key is None or current_key in processed:
                    continue
                processed.add(current_key)
                try:
                    async for rep in client.iter_messages(seed_entity, reply_to=getattr(current_msg, "id", None), reverse=True, limit=None):
                        if DEBUG_FETCH:
                            print(f"[DEBUG] get_replies for msg_id={getattr(current_msg, 'id', None)} in peer={chan_val} topic_id={current_topic or seed_topic}")
                        rep_topic = _extract_actual_topic_id(rep)
                        rep_link = _build_message_link(seed_entity, rep, topic_id=rep_topic)
                        added = _add_collected_msg(
                            seed_entity,
                            rep,
                            link=rep_link,
                            topic_id=current_topic or rep_topic,
                            actual_topic_id=rep_topic,
                            is_reply=True,
                        )
                        rep_key = _message_key(seed_entity, rep)
                        if rep_key and rep_key not in processed:
                            queue.append((rep, current_topic or rep_topic))
                        if not added and rep_key in processed:
                            continue
                except Exception as e:
                    print(f"Hinweis: Replies zu msg_id={base_id} konnten nicht vollständig geladen werden: {e}")
                    continue

        # Chronologisch sortiert pro Section an die ODT-Ausgabe übergeben.
        section_sorted = _sort_collected_messages(section_acc)
        sections_payload.append(section_sorted)
        if DEBUG_FETCH:
            print(f"Abschnitt '{heading}': basis={base_count}, replies={reply_count}, total={len(section_sorted)}")

    collected_flat: List[CollectedMessage] = []
    for sec in sections_payload:
        collected_flat.extend(sec)
    if DEBUG_FETCH:
        print(f"Gesamt: {sum(len(sec) for sec in sections_payload)} Nachrichten in {len(sections_payload)} Abschnitten für ODT.")

    return collected_flat, used_doc_ids, resume_hints
