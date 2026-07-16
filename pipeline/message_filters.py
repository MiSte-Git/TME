from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Optional, Sequence

from telethon import TelegramClient
from telethon.errors import BadRequestError, FloodWaitError, RPCError
from zoneinfo import ZoneInfo


DEBUG_FETCH = False  # bei Bedarf auf True setzen, um Details zu sehen
_BATCH_SIZE = 200
_RETRY_DELAYS = (1.0, 3.0, 7.0)


@dataclass
class FetchMessagesResult:
    messages: list[Any]
    resume_hint: Optional[dict[str, Any]] = None
    error_info: Optional[str] = None
    stats: Optional[dict[str, float]] = None


def build_day_time_range(
    date_obj,
    start_time_str: Optional[str],
    end_time_str: Optional[str],
) -> tuple[datetime, datetime]:
    """Erzeugt eine lokale Datetime-Range aus Datum + Zeitstrings ("HH:MM:SS").

    Wird primär für Debug-/Anzeigezwecke verwendet; die eigentliche
    Zeitfilterung übernimmt fetch_messages_for_section_day.
    """
    def _parse_time(value: Optional[str], default: time) -> time:
        if not value:
            return default
        try:
            parts = [int(p) for p in value.split(":")]
            while len(parts) < 3:
                parts.append(0)
            return time(*parts[:3])
        except Exception:
            return default

    try:
        st = _parse_time(start_time_str, time(0, 0, 0))
        et = _parse_time(end_time_str, time(23, 59, 59))
    except Exception:
        st = time(0, 0, 0)
        et = time(23, 59, 59)

    # Zeitzone hier ist nur "lokal" informativ; echte Umrechnung macht Telethon
    tz = timezone.utc
    start_dt = datetime.combine(date_obj, st, tzinfo=tz)
    end_dt = datetime.combine(date_obj, et, tzinfo=tz)
    return start_dt, end_dt


def _parse_time(value: Optional[str], default: time) -> time:
    if not value:
        return default
    try:
        parts = [int(p) for p in value.split(":")]
        while len(parts) < 3:
            parts.append(0)
        return time(*parts[:3])
    except Exception:
        return default


async def fetch_messages_for_section_day(
    client: TelegramClient,
    entity: Any,
    day_str: str,
    local_tz: Optional[str],
    start_time_str: Optional[str],
    end_time_str: Optional[str],
    *,
    topic_id: Optional[int] = None,
    min_id: int = 0,
) -> FetchMessagesResult:
    """Lädt Nachrichten für einen Tag/Topic robust in Batches und bricht
    kontrolliert beim ersten nicht behebbaren Fehler ab.

    - FloodWait wird ausgesessen.
    - Timeout/RPC/BadRequest bekommen Backoff-Retries.
    - Bei nicht behebbaren Fehlern: Abbruch + resume_hint + error_info,
      keine stillen Batch-Skips.
    - min_id (Telethon iter_messages-Parameter, direkt durchgereicht): liefert
      nur Nachrichten mit id > min_id. Für den inkrementellen Store-Modus
      (siehe message_store.py) wird hier der zuletzt bekannte Stand pro
      Section übergeben, damit der Server bereits bekannte Nachrichten gar
      nicht erst zurückschickt, statt sie clientseitig zu filtern. 0 (Default)
      = keine Untergrenze, bestehendes Verhalten unverändert.
    """
    tz_name = local_tz or "Europe/Zurich"  # ggf. an DEFAULT_LOCAL_TZ angleichen
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc

    d = datetime.strptime(day_str, "%d/%m/%Y").date()
    start_t = _parse_time(start_time_str, time(0, 0, 0))
    end_t = _parse_time(end_time_str, time(23, 59, 59))

    start_local = datetime.combine(d, start_t, tzinfo)
    end_local = datetime.combine(d, end_t, tzinfo)
    end_utc = end_local.astimezone(timezone.utc)

    msgs: list[Any] = []
    iter_kwargs: dict[str, Any] = {
        "reverse": False,
        "offset_date": end_utc,
        "limit": _BATCH_SIZE,
    }
    if topic_id is not None:
        iter_kwargs["reply_to"] = int(topic_id)
    if min_id:
        iter_kwargs["min_id"] = int(min_id)

    stats = {
        "skipped_messages": 0,
        "flood_waits": 0,
        "flood_wait_seconds": 0.0,
    }

    offset_id = 0  # Pagination: älteste ID der letzten erfolgreichen Batch
    stop_reached = False
    resume_hint: Optional[dict[str, Any]] = None
    error_info: Optional[str] = None
    last_ok_msg: Any = None

    while not stop_reached:
        batch, stop_reached, last_seen_id, err_info, last_kept = await _fetch_batch_with_retries(
            client,
            entity,
            iter_kwargs,
            offset_id=offset_id,
            tzinfo=tzinfo,
            start_local=start_local,
            end_local=end_local,
            day=d,
            stats=stats,
        )
        offset_id = last_seen_id
        if err_info:
            last_ok_msg = last_kept or last_ok_msg
            error_info = err_info
            resume_hint = _build_resume_hint(entity, topic_id, last_ok_msg)
            if last_ok_msg:
                warn_dt = getattr(last_ok_msg, "date", None)
                print(
                    f"WARN: Export abgebrochen bei msg_id={getattr(last_ok_msg, 'id', '?')}, "
                    f"date={warn_dt}; bitte ab diesem Punkt neu starten."
                )
            else:
                print(
                    "WARN: Export abgebrochen vor erster Nachricht; keine Daten geladen."
                )
            break
        if not batch:
            break
        msgs.extend(batch)
        if batch:
            last_ok_msg = batch[-1]

    # stabil nach Datum + ID sortieren
    msgs.sort(key=lambda m: (m.date, m.id))

    print(
        f"Info: {len(msgs)} Nachrichten geladen "
        f"(übersprungene Nachrichten: {stats['skipped_messages']}; "
        f"FloodWaits: {stats['flood_waits']} / {stats['flood_wait_seconds']:.1f}s gewartet)"
    )

    return FetchMessagesResult(
        messages=msgs,
        resume_hint=resume_hint,
        error_info=error_info,
        stats=stats,
    )


def _build_resume_hint(entity: Any, topic_id: Optional[int], last_ok_msg: Any) -> Optional[dict[str, Any]]:
    if last_ok_msg is None:
        return None
    try:
        chat_id = getattr(entity, "id", None)
    except Exception:
        chat_id = None
    last_ok_id = getattr(last_ok_msg, "id", None)
    last_ok_date = getattr(last_ok_msg, "date", None)
    try:
        last_ok_date_iso = last_ok_date.isoformat()
    except Exception:
        last_ok_date_iso = None
    return {
        "chat_id": chat_id,
        "topic_id": int(topic_id) if topic_id is not None else None,
        "last_ok_id": last_ok_id,
        "last_ok_date": last_ok_date_iso,
        "direction": "backward",  # iter_messages reverse=False läuft von neu nach alt
    }


async def _fetch_batch_with_retries(
    client: TelegramClient,
    entity: Any,
    iter_kwargs: dict[str, Any],
    *,
    offset_id: int,
    tzinfo: ZoneInfo,
    start_local: datetime,
    end_local: datetime,
    day,
    stats: dict[str, float],
) -> tuple[Optional[list[Any]], bool, int, Optional[str], Optional[Any]]:
    """Lädt eine Batch Nachrichten robust mit Retries/Backoff.

    Liefert zusätzlich error_info und last_kept_msg für Resume-Hinweise.
    """
    delays = list(_RETRY_DELAYS)
    last_seen_id = offset_id
    last_kept_msg = None

    attempt = 0
    while True:
        try:
            batch: list[Any] = []
            stop_reached = False
            async for m in client.iter_messages(
                entity,
                offset_id=offset_id,
                **iter_kwargs,
            ):
                last_seen_id = getattr(m, "id", last_seen_id)
                if not getattr(m, "date", None):
                    stats["skipped_messages"] += 1
                    continue
                local_dt = m.date.astimezone(tzinfo)

                if DEBUG_FETCH:
                    print(
                        "DEBUG fetch_messages_for_section_day:",
                        "msg_id=", getattr(m, "id", None),
                        "local_dt=", local_dt,
                        "start_local=", start_local,
                        "end_local=", end_local,
                    )

                if local_dt.date() < day:
                    stop_reached = True
                    if DEBUG_FETCH:
                        print("  -> BREAK (vor gesuchtem Tag)")
                    break

                if local_dt < start_local:
                    stats["skipped_messages"] += 1
                    if DEBUG_FETCH:
                        print("  -> SKIP (zu früh am Tag)")
                    continue

                if local_dt.date() > day or local_dt > end_local:
                    stats["skipped_messages"] += 1
                    if DEBUG_FETCH:
                        print("  -> SKIP (zu spät/nach Tag)")
                    continue

                if DEBUG_FETCH:
                    print("  -> KEEP")
                batch.append(m)
                last_kept_msg = m

            return batch, stop_reached, last_seen_id, None, last_kept_msg
        except FloodWaitError as e:
            wait_s = max(int(getattr(e, "seconds", 1)), 1)
            stats["flood_waits"] += 1
            stats["flood_wait_seconds"] += wait_s
            print(f"Hinweis: FloodWait {wait_s}s beim Laden, warte und versuche Batch erneut…")
            await asyncio.sleep(wait_s + random.uniform(0, 0.4))
            continue
        except (asyncio.TimeoutError, TimeoutError, RPCError, BadRequestError) as e:
            if attempt < len(delays):
                delay = delays[attempt]
                attempt += 1
                print(f"Hinweis: {type(e).__name__} beim Laden, Versuch {attempt}/{len(delays)} – warte {delay}s…")
                await asyncio.sleep(delay)
                continue
            err_info = f"{type(e).__name__}: {e}"
            return None, False, last_seen_id, err_info, last_kept_msg
        except Exception as e:
            if attempt < len(delays):
                delay = delays[attempt]
                attempt += 1
                print(f"Hinweis: {type(e).__name__} beim Laden, Versuch {attempt}/{len(delays)} – warte {delay}s…")
                await asyncio.sleep(delay)
                continue
            err_info = f"{type(e).__name__}: {e}"
            return None, False, last_seen_id, err_info, last_kept_msg


def _debug_dump_msg_topic_info(msg: Any) -> None:
    """Debug: Gibt Topic-relevante Felder einer Nachricht aus."""
    if not DEBUG_FETCH:
        return
    mid = getattr(msg, "id", None)
    reply_to = getattr(msg, "reply_to", None)
    print("DEBUG TOPIC MSG", mid)
    print("  date:", getattr(msg, "date", None))
    print("  topic_id:", getattr(msg, "topic_id", None))
    print("  top_msg_id:", getattr(msg, "top_msg_id", None))
    print("  forum_topic_id:", getattr(msg, "forum_topic_id", None))
    print("  raw reply_to:", repr(reply_to))
    if reply_to is not None:
        print("    reply_to_msg_id:", getattr(reply_to, "reply_to_msg_id", None))
        print("    top_msg_id:", getattr(reply_to, "top_msg_id", None))
        print("    reply_to_top_id:", getattr(reply_to, "reply_to_top_id", None))
    d = getattr(msg, "__dict__", None)
    if isinstance(d, dict):
        print("  __dict__ keys:", list(d.keys()))


def _extract_actual_topic_id(msg: Any) -> Optional[int]:
    """Versucht, die tatsächliche Topic-/Thread-ID einer Nachricht zu bestimmen.

    Nutzt primär reply_to.top_msg_id / reply_to.reply_to_top_id usw.
    """
    reply_to = getattr(msg, "reply_to", None)
    t_id = getattr(reply_to, "top_msg_id", None)
    if t_id is None:
        t_id = getattr(reply_to, "reply_to_top_id", None)
    if t_id is None:
        t_id = getattr(reply_to, "reply_to_msg_id", None)
    if t_id is None:
        t_id = getattr(msg, "top_msg_id", None)
    if t_id is None:
        t_id = getattr(msg, "topic_id", None)
    try:
        return int(t_id) if t_id is not None else None
    except Exception:
        return None


def filter_messages_for_topic(
    msgs: Sequence[Any],
    topic_id: Optional[int],
    topic_source: Optional[Any],
    schedule_default_channel: Optional[str],
) -> Sequence[Any]:
    """Filtert Nachrichten optional nach Topic.

    Für Forum-Topics in privaten /c/-Links (z.B. https://t.me/c/1740008473/5437)
    verwenden wir in erster Linie reply_to.top_msg_id als Thread-ID.

    Wenn kein topic_id gesetzt ist, wird nichts herausgefiltert.
    """
    if not msgs:
        return msgs
    if topic_id is None:
        return msgs

    wanted_tid: Optional[int]
    try:
        wanted_tid = int(topic_id)
    except Exception:
        wanted_tid = None
    if wanted_tid is None:
        return msgs

    out: list[Any] = []
    interesting_ids = {7289, 7295, 7298, 7301, 7304}

    for m in msgs:
        if getattr(m, "id", None) in interesting_ids:
            _debug_dump_msg_topic_info(m)

        actual_tid = _extract_actual_topic_id(m)

        # Fall A: explizite Topics (nicht 1) → hart filtern
        if wanted_tid != 1:
            if actual_tid is None:
                continue
            if actual_tid == wanted_tid:
                out.append(m)
            continue

        # Fall B: Topic 1 ("General") → aktueller Stand der Telethon-Daten:
        # Alle relevanten Nachrichten haben topic_id/top_msg_id/forum_topic_id = None.
        # Wir können also nicht zwischen "Topic 1" und "topiclos" unterscheiden und
        # müssen alle Nachrichten im Zeitfenster akzeptieren.
        out.append(m)

    # Nach erfolgreichem Topic-Filter innerhalb des Topics stabil nach Zeit und ID sortieren
    try:
        out.sort(key=lambda mm: (mm.date, mm.id))
    except Exception:
        pass

    return out
