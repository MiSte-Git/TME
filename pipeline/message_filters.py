from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Optional, Sequence

from telethon import TelegramClient
from zoneinfo import ZoneInfo


DEBUG_FETCH = True


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
) -> Sequence[Any]:
    """Lädt Nachrichten für einen Tag mit Zeitfenster aus einem Channel.

    Dies ist eine ausgelagerte Variante der bisherigen fetch_messages_for_day-
    Logik aus runner_schedule.py, angepasst auf Section/Day-Kontext.
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
    async for m in client.iter_messages(
        entity,
        reverse=False,
        offset_date=end_utc,
        limit=None,
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

    out: list[Any] = []
    for m in msgs:
        reply_to = getattr(m, "reply_to", None)
        # Für Forum-Topics in /c/-Links ist reply_to.reply_to_msg_id häufig die Topic-ID
        t_id = getattr(reply_to, "top_msg_id", None)
        if t_id is None:
            t_id = getattr(reply_to, "reply_to_top_id", None)
        if t_id is None:
            t_id = getattr(reply_to, "reply_to_msg_id", None)
        # Fallbacks für andere Typen / ältere Nachrichten
        if t_id is None:
            t_id = getattr(m, "top_msg_id", None)
        if t_id is None:
            t_id = getattr(m, "topic_id", None)

        from_id = getattr(m, "from_id", None)
        peer_id = getattr(m, "peer_id", None)

        try:
            print(
                "DEBUG filter_messages_for_topic:",
                "msg_id=", getattr(m, "id", None),
                "t_id=", t_id,
                "topic_id=", topic_id,
                "reply_to=", reply_to,
                "from_id=", from_id,
                "peer_id=", peer_id,
            )
        except Exception:
            pass

        try:
            if t_id is not None and int(t_id) == int(topic_id):
                out.append(m)
            else:
                # Nachrichten ohne passende Topic-Info werden ausgeschlossen,
                # um das Verhalten klar zu halten.
                pass
        except Exception:
            # Wenn irgendeine Konvertierung schiefgeht, Nachricht lieber ausschließen
            pass

    return out