"""Utilities for reading and writing structured schedule files used by the
Telegram ODT generator."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ISO_DATE_FMT = "%Y-%m-%d"
EU_DOT_FMT = "%d.%m.%Y"
EU_SLASH_FMT = "%d/%m/%Y"
HEADING_SEP = "  -  "


def _as_path(path: Any) -> Path:
    if isinstance(path, Path):
        return path
    return Path(str(path))


def _parse_date(text: str) -> date:
    if not text:
        raise ValueError("Leeres Datum im Schedule-Eintrag")
    text = text.strip()
    for fmt in (ISO_DATE_FMT, EU_DOT_FMT, EU_SLASH_FMT):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Ungültiges Datumsformat: '{text}'. Erwartet YYYY-MM-DD oder DD.MM.YYYY")


def _dump_date(value: date) -> str:
    return value.strftime(ISO_DATE_FMT)


def _parse_time_optional(text: Any) -> Optional[str]:
    """Akzeptiert None oder Zeitstrings 'HH:MM[:SS]' und gibt normalisierte 'HH:MM:SS'-Strings zurück.

    Für leere oder ungültige Werte wird None zurückgegeben – der Aufrufer setzt dann Defaults.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            return t.strftime("%H:%M:%S")
        except ValueError:
            continue
    # Ungültiges Format → None (Aufrufer interpretiert als voller Tag)
    return None



def _normalize_links(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [seg.strip() for seg in raw.split(";")]
    elif isinstance(raw, Iterable):
        parts = [str(seg).strip() for seg in raw]
    else:
        raise TypeError("Links müssen als String oder Sequenz vorliegen")
    return [p for p in parts if p]


def _links_to_string(links: Sequence[str]) -> str:
    return ";".join(link.strip() for link in links if link.strip())


@dataclass
class ScheduleSection:
    date: date
    title: str
    subheading: Optional[str] = None
    # Optionale Zeitfenster; intern als normalisierte "HH:MM:SS"-Strings gehalten.
    # Legacy-Schedules ohne diese Felder werden als voller Tag interpretiert
    # (00:00:00–23:59:59) – diese Defaults werden beim Laden gesetzt.
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    links: List[str] = field(default_factory=list)
    fetch_by_date: bool = True
    heading_override: Optional[str] = None
    channel: Optional[str] = None

    def heading_text(self) -> str:
        if self.heading_override:
            return self.heading_override
        return f"{self.date.strftime(ISO_DATE_FMT)}{HEADING_SEP}{self.title.strip()}"


@dataclass
class ScheduleDocument:
    document_title: Optional[str]
    default_channel: Optional[str]
    sections: List[ScheduleSection] = field(default_factory=list)

    def to_json_dict(self) -> Dict[str, Any]:
        return {
            "document_title": self.document_title,
            "default_channel": self.default_channel,
            "sections": [
                self._section_to_json_dict(section) for section in self.sections
            ],
        }

    @staticmethod
    def _section_to_json_dict(section: ScheduleSection) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "date": _dump_date(section.date),
            "title": section.title,
            "subheading": section.subheading,
            "links": _links_to_string(section.links),
            "fetch_by_date": bool(section.fetch_by_date),
        }
        # Zeitfenster immer explizit mitschreiben, damit das Schema klar ist.
        # Falls None, werden die Defaults (ganzer Tag) beim erneuten Laden gesetzt.
        data["startTime"] = section.start_time
        data["endTime"] = section.end_time
        return data


def load_schedule_document(path: Any) -> ScheduleDocument:
    """Liest eine Schedule-JSON-Datei und gibt ein `ScheduleDocument` zurück."""
    p = _as_path(path)
    payload = json.loads(p.read_text(encoding="utf-8"))
    title = payload.get("document_title")
    default_channel = payload.get("default_channel") or None
    sections_data = payload.get("sections") or []
    sections: List[ScheduleSection] = []
    for idx, raw in enumerate(sections_data):
        if not isinstance(raw, dict):
            raise ValueError(f"sections[{idx}] muss ein Objekt sein")
        date_raw = raw.get("date")
        if date_raw is None:
            raise ValueError(f"sections[{idx}] benötigt ein 'date'-Feld")
        date_value = _parse_date(str(date_raw))
        title_value = (raw.get("title") or "").strip()
        if not title_value:
            raise ValueError(f"sections[{idx}] benötigt ein 'title'-Feld")
        links = _normalize_links(raw.get("links"))
        fetch_by_date = raw.get("fetch_by_date")
        if fetch_by_date is None:
            fetch_flag = not bool(links)
        else:
            fetch_flag = bool(fetch_by_date)
        # Zeitfenster laden; fehlende Felder werden auf Defaults für "ganzer Tag" gesetzt.
        start_norm = _parse_time_optional(raw.get("startTime"))
        end_norm = _parse_time_optional(raw.get("endTime"))
        if start_norm is None:
            start_norm = time(0, 0, 0).strftime("%H:%M:%S")
        if end_norm is None:
            end_norm = time(23, 59, 59).strftime("%H:%M:%S")
        section = ScheduleSection(
            date=date_value,
            title=title_value,
            subheading=(raw.get("subheading") or None),
            start_time=start_norm,
            end_time=end_norm,
            links=links,
            fetch_by_date=fetch_flag,
        )
        sections.append(section)
    return ScheduleDocument(document_title=title, default_channel=default_channel, sections=sections)


def save_schedule_document(schedule: ScheduleDocument, path: Any) -> None:
    p = _as_path(path)
    p.write_text(json.dumps(schedule.to_json_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def schedule_to_blocks(schedule: ScheduleDocument) -> Tuple[Optional[str], List[Tuple[Optional[str], List[List[Any]]]]]:
    """Konvertiert ein Schedule-Dokument in die Blockstruktur des Legacy-Parsers."""
    blocks: List[Tuple[Optional[str], List[List[Any]]]] = []
    current_channel: Optional[str] = None
    current_items: List[List[Any]] = []

    def flush() -> None:
        nonlocal current_items, current_channel
        if current_items:
            blocks.append((current_channel, current_items))
        current_channel = None
        current_items = []

    if not schedule.sections:
        return schedule.document_title, blocks

    for section in schedule.sections:
        channel = section.channel if section.channel is not None else schedule.default_channel
        if section.fetch_by_date and not channel:
            raise ValueError(
                f"Abschnitt '{section.title}' benötigt einen default_channel, da keine Links angegeben sind"
            )
        if current_items and channel != current_channel:
            flush()
        if not current_items:
            current_channel = channel
        metadata: Dict[str, Any] = {
            "heading": section.heading_text(),
            "title": section.title,
            "date_iso": section.date.strftime(ISO_DATE_FMT),
        }
        if section.subheading:
            metadata["subheading"] = section.subheading
        if section.links:
            metadata["links_count"] = len(section.links)
        if channel:
            metadata["channel"] = channel
        links_only = not section.fetch_by_date
        day_str = section.date.strftime(EU_SLASH_FMT)
        entry = [day_str, section.title, list(section.links), links_only, True, metadata]
        current_items.append(entry)

    flush()
    return schedule.document_title, blocks


def blocks_to_schedule(
    doc_title: Optional[str],
    blocks: Sequence[Tuple[Optional[Any], Sequence[Sequence[Any]]]],
    default_channel: Optional[str] = None,
) -> ScheduleDocument:
    """Erzeugt ein Schedule-Dokument aus der bestehenden Blockstruktur."""
    sections: List[ScheduleSection] = []
    for channel, items in blocks:
        for item in items:
            if not item:
                continue
            day_raw = item[0] if len(item) >= 1 else None
            title = item[1] if len(item) >= 2 else ""
            links = list(item[2]) if len(item) >= 3 else []
            fetch_flag = not (len(item) >= 4 and bool(item[3]))
            if day_raw is None:
                raise ValueError("Block-Eintrag ohne Datum (item[0]) ist ungültig")
            date_obj = _parse_date(str(day_raw))
            metadata = item[5] if len(item) >= 6 and isinstance(item[5], dict) else {}
            heading = metadata.get("heading")
            subheading = metadata.get("subheading")
            section_channel = metadata.get("channel") or channel
            start_norm = _parse_time_optional(metadata.get("startTime"))
            end_norm = _parse_time_optional(metadata.get("endTime"))
            if start_norm is None:
                start_norm = time(0, 0, 0).strftime("%H:%M:%S")
            if end_norm is None:
                end_norm = time(23, 59, 59).strftime("%H:%M:%S")
            section = ScheduleSection(
                date=date_obj,
                title=title,
                subheading=subheading,
                start_time=start_norm,
                end_time=end_norm,
                links=links,
                fetch_by_date=fetch_flag,
                heading_override=heading,
                channel=section_channel,
            )
            sections.append(section)
    return ScheduleDocument(document_title=doc_title, default_channel=default_channel, sections=sections)


def load_legacy_schedule(path: Any) -> ScheduleDocument:
    """Importiert eine Legacy-TXT-Datei über den bestehenden Parser."""
    from tg_by_date_to_odt_modes import parse_schedule_file_v2  # Lazy import

    p = _as_path(path)
    doc_title, blocks = parse_schedule_file_v2(str(p))
    return blocks_to_schedule(doc_title, blocks)
