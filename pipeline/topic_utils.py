from __future__ import annotations

from typing import Any, Optional, Tuple

from .fetch import parse_topic_from_link
from schedule_json import ScheduleDocument  # falls dieser Typ an anderer Stelle nötig wäre


def extract_topic_from_section(
    section: Any,
    schedule: Any,
) -> Tuple[Optional[int], Optional[Any]]:
    """Ermittelt Topic-bezogene Informationen für eine Section.

    Rückgabe:
        (topic_id, source):
        - topic_id: Telegram-Thread-ID (top_msg_id) oder None
        - source: der Link/Channel-String, aus dem das Topic ermittelt wurde
    """
    # 1) Section-spezifischer Channel kann ein Topic-Link sein
    if getattr(section, "channel", None):
        tid, _ = parse_topic_from_link(str(section.channel))
        if tid is not None:
            return tid, section.channel

    # 2) Default-Channel aus dem Schedule kann ein Topic-Link sein.
    #    Jede erkannte Topic-ID (einschließlich 1) bedeutet: spezifischer Thread.
    if getattr(schedule, "default_channel", None):
        tid, _ = parse_topic_from_link(str(schedule.default_channel))
        if tid is not None:
            return tid, schedule.default_channel

    # 3) Falls genau ein Link in der Section vorhanden ist, der ein Topic referenziert,
    #    verwenden wir diesen als Quelle für das Topic, ignorieren aber seine konkrete msg_id
    links = [lnk for lnk in (getattr(section, "links", None) or []) if lnk]
    if len(links) == 1:
        tid, _ = parse_topic_from_link(str(links[0]))
        if tid is not None:
            return tid, links[0]

    return None, None