from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Optional, Sequence

from telethon import TelegramClient
from zoneinfo import ZoneInfo


DEBUG_FETCH = False  # bei Bedarf auf True setzen, um Details zu sehen


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
) -> Sequence[Any]:
    """Lädt Nachrichten für einen Tag mit Zeitfenster aus einem Channel.

    Dies ist eine ausgelagerte Variante der bisherigen fetch_messages_for_day-
    Logik aus runner_schedule.py, angepasst auf Section/Day-Kontext.

    Wenn ``topic_id`` gesetzt ist und es sich um ein Forum-Topic handelt,
    werden die Nachrichten serverseitig auf genau dieses Topic gefiltert,
    indem ``reply_to=topic_id`` an ``iter_messages`` übergeben wird. Damit
    entfällt die Notwendigkeit, topic_id/top_msg_id/forum_topic_id an den
    einzelnen Nachrichtenobjekten auszuwerten.
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
    # reverse=False: neu -> alt; mit offset_date=end_utc bekommen wir nur Nachrichten
    # bis zum Tagesende/-zeitpunkt
    iter_kwargs: dict[str, Any] = {
        "reverse": False,
        "offset_date": end_utc,
        "limit": None,
    }
    # Server-seitiges Topic-Filtering für Forum-Topics, basierend auf
    # https://t.me/c/<chatId>/<topicId>/<messageId> → reply_to=topic_id
    if topic_id is not None:
        iter_kwargs["reply_to"] = int(topic_id)

    async for m in client.iter_messages(
        entity,
        **iter_kwargs,
    ):
        if not getattr(m, "date", None):
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

        # Wenn wir vor dem gesuchten Datum sind, können wir abbrechen
        if local_dt.date() < d:
            if DEBUG_FETCH:
                print("  -> BREAK (vor gesuchtem Tag)")
            break

        # Datum passt, aber Uhrzeit ist vor dem Startfenster → nur überspringen
        if local_dt < start_local:
            if DEBUG_FETCH:
                print("  -> SKIP (zu früh am Tag)")
            continue

        # Nach dem gesuchten Tag/Zeitfenster → überspringen
        if local_dt.date() > d or local_dt > end_local:
            if DEBUG_FETCH:
                print("  -> SKIP (zu spät/nach Tag)")
            continue

        if DEBUG_FETCH:
            print("  -> KEEP")
        msgs.append(m)

    # stabil nach Datum + ID sortieren
    msgs.sort(key=lambda m: (m.date, m.id))
    return msgs


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