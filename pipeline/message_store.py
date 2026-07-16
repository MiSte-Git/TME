"""
Persistenter Message-Store für inkrementelles Dokument-Update (Store-Ansatz,
siehe Backlog: Rendern ist billig, nur das Fetchen soll inkrementell werden).

Ein Store gehört zu genau EINER Schedule-Datei (schedule_stem = Path.stem der
Schedule-JSON, identisch zu out_basename) und liegt unter
data/message_store/<schedule_stem>.json. Bewusst NICHT global pro Kanal:
derselbe Kanal kann in mehreren Schedules mit unterschiedlichen Zeitfenstern
auftauchen; ein pro-Schedule-Store macht "welche Kanäle/Nachrichten gehören
zu diesem Dokument" trivial (identisch zur bisherigen 1:1-Beziehung
Schedule-Datei -> Ausgabedokument) statt eine schedule-übergreifende
Scope-Frage lösen zu müssen.

Datenmodell (siehe _SCHEMA_VERSION-Kommentar unten):
  channels[<channel_key>].messages[<message_id>] = {date, record, translation_record}
  section_state[<fingerprint>] = {channel_key, last_message_id, last_message_date}

- channel_key: str(entity.id) der Telegram-Entity (stabil, unabhängig von
  Username-Änderungen) - siehe channel_key_for_entity().
- fingerprint: identifiziert eine konkrete (Kanal, Datum, Zeitfenster, Topic)-
  Kombination einer Schedule-Section - siehe section_fingerprint(). Getrennt
  pro Section (nicht nur pro Kanal), damit eine rückwirkend hinzugefügte
  frühere Section nicht fälschlich durch den min_id-Floor einer späteren
  Section ausgefiltert wird (Message-IDs sind pro Kanal monoton steigend,
  aber nur INNERHALB der bereits abgedeckten, zusammenhängenden Zeitspanne
  einer Section ist ein einzelner "last_id"-Wert als Untergrenze sicher).

Fehlerbehandlung: Eine fehlende Store-Datei ist der Normalfall beim ersten
Lauf (leerer Store). Eine beschädigte/unvollständige Store-Datei führt NICHT
zum Absturz - sie wird als *.corrupt-<timestamp>.json gesichert (kein
stiller Datenverlust) und durch einen leeren Store ersetzt; der Aufrufer
bekommt das über `load_warnings` sichtbar zurückgemeldet, damit es z.B. via
_notify() im UI/CLI auftaucht statt in einem breiten except zu verschwinden.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .runs import RecordPair, RunsRecord, record_from_dict, record_to_dict

_SCHEMA_VERSION = 1
_STORE_DIR = Path("data/message_store")


def channel_key_for_entity(entity: Any) -> str:
    """Stabiler Kanal-Schlüssel: numerische Telegram-Entity-ID (unabhängig von
    Username-Änderungen). Fällt auf str(entity) zurück, falls keine id
    verfügbar ist (sollte praktisch nicht vorkommen)."""
    eid = getattr(entity, "id", None)
    if eid is not None:
        try:
            return str(int(eid))
        except Exception:
            pass
    return str(entity)


def section_fingerprint(
    chan_val: Optional[str],
    date_iso: str,
    start_time: Optional[str],
    end_time: Optional[str],
    topic_id: Optional[int],
) -> str:
    return "|".join([
        str(chan_val or ""),
        str(date_iso or ""),
        str(start_time or ""),
        str(end_time or ""),
        str(topic_id) if topic_id is not None else "",
    ])


def _store_path(schedule_stem: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in schedule_stem)
    return _STORE_DIR / f"{safe}.json"


@dataclass
class StoredMessage:
    channel_key: str
    message_id: int
    date: datetime
    record: RunsRecord
    translation_record: Optional[RunsRecord] = None


@dataclass
class SectionState:
    channel_key: str
    last_message_id: int
    last_message_date: Optional[datetime] = None


class MessageStore:
    def __init__(self, schedule_stem: str) -> None:
        self.schedule_stem = schedule_stem
        self._channels: Dict[str, Dict[int, StoredMessage]] = {}
        self._section_state: Dict[str, SectionState] = {}
        self.load_warnings: List[str] = []

    # ---- Laden / Speichern ----

    @classmethod
    def load(cls, schedule_stem: str) -> "MessageStore":
        store = cls(schedule_stem)
        path = _store_path(schedule_stem)
        if not path.exists():
            return store  # Erststart: leerer Store, kein Fehler
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            store._load_from_dict(raw)
        except Exception as exc:
            # Beschädigte/unvollständige Datei: sichern statt verlieren, mit
            # leerem Store weiterarbeiten (sicherer Fallback, kein Absturz).
            backup = path.with_name(f"{path.stem}.corrupt-{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
            try:
                path.replace(backup)
                store.load_warnings.append(
                    f"Message-Store beschädigt/unlesbar ({exc}); Datei gesichert nach {backup}, starte mit leerem Store."
                )
            except Exception as backup_exc:
                store.load_warnings.append(
                    f"Message-Store beschädigt/unlesbar ({exc}); Sicherung fehlgeschlagen ({backup_exc}), starte mit leerem Store."
                )
            store._channels = {}
            store._section_state = {}
        return store

    def _load_from_dict(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            raise ValueError("Store-Wurzel ist kein Objekt")
        channels_raw = raw.get("channels")
        if not isinstance(channels_raw, dict):
            channels_raw = {}
        for channel_key, chan_data in channels_raw.items():
            if not isinstance(chan_data, dict):
                continue
            messages_raw = chan_data.get("messages")
            if not isinstance(messages_raw, dict):
                continue
            bucket: Dict[int, StoredMessage] = {}
            for msg_id_str, entry in messages_raw.items():
                try:
                    msg_id = int(msg_id_str)
                    date = _parse_iso(entry.get("date"))
                    record = record_from_dict(entry["record"])
                    tr_raw = entry.get("translation_record")
                    translation_record = record_from_dict(tr_raw) if tr_raw else None
                    bucket[msg_id] = StoredMessage(
                        channel_key=str(channel_key), message_id=msg_id, date=date,
                        record=record, translation_record=translation_record,
                    )
                except Exception as exc:
                    # Einzelne kaputte Nachricht überspringen statt den ganzen Store zu verwerfen.
                    self.load_warnings.append(f"Store-Eintrag {channel_key}/{msg_id_str} übersprungen ({exc}).")
                    continue
            if bucket:
                self._channels[str(channel_key)] = bucket

        section_raw = raw.get("section_state")
        if isinstance(section_raw, dict):
            for fp, entry in section_raw.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    self._section_state[str(fp)] = SectionState(
                        channel_key=str(entry.get("channel_key", "")),
                        last_message_id=int(entry.get("last_message_id", 0)),
                        last_message_date=_parse_iso(entry.get("last_message_date")),
                    )
                except Exception:
                    continue

    def save(self) -> None:
        path = _store_path(self.schedule_stem)
        path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {
            "version": _SCHEMA_VERSION,
            "schedule": self.schedule_stem,
            "channels": {
                chan_key: {
                    "messages": {
                        str(mid): {
                            "date": sm.date.isoformat() if sm.date else None,
                            "record": record_to_dict(sm.record),
                            "translation_record": record_to_dict(sm.translation_record) if sm.translation_record else None,
                        }
                        for mid, sm in bucket.items()
                    }
                }
                for chan_key, bucket in self._channels.items()
            },
            "section_state": {
                fp: {
                    "channel_key": st.channel_key,
                    "last_message_id": st.last_message_id,
                    "last_message_date": st.last_message_date.isoformat() if st.last_message_date else None,
                }
                for fp, st in self._section_state.items()
            },
        }
        # Atomarer Write: erst ins .tmp schreiben, dann umbenennen - vermeidet
        # eine kaputte/halb geschriebene Store-Datei bei Absturz mitten im Schreiben.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)

    # ---- Zugriff ----

    def has_message(self, channel_key: str, message_id: int) -> bool:
        return message_id in self._channels.get(channel_key, {})

    def add_message(
        self,
        channel_key: str,
        message_id: int,
        date: Optional[datetime],
        record: RunsRecord,
        translation_record: Optional[RunsRecord] = None,
    ) -> None:
        bucket = self._channels.setdefault(channel_key, {})
        bucket[message_id] = StoredMessage(
            channel_key=channel_key, message_id=message_id,
            date=date or datetime.min.replace(tzinfo=timezone.utc),
            record=record, translation_record=translation_record,
        )

    def min_id_for_fingerprint(self, fingerprint: str) -> int:
        st = self._section_state.get(fingerprint)
        return st.last_message_id if st else 0

    def min_ids_by_fingerprint(self) -> Dict[str, int]:
        return {fp: st.last_message_id for fp, st in self._section_state.items()}

    def update_section_state(
        self, fingerprint: str, channel_key: str, last_message_id: int, last_message_date: Optional[datetime],
    ) -> None:
        existing = self._section_state.get(fingerprint)
        if existing and existing.last_message_id >= last_message_id:
            return  # nie rückwärts laufen (z.B. bei leerem/fehlgeschlagenem Teil-Fetch)
        self._section_state[fingerprint] = SectionState(
            channel_key=channel_key, last_message_id=last_message_id, last_message_date=last_message_date,
        )

    def all_messages(self) -> Iterable[StoredMessage]:
        for bucket in self._channels.values():
            yield from bucket.values()

    def __len__(self) -> int:
        return sum(len(b) for b in self._channels.values())


def render_records_from_store(
    store: MessageStore,
    chronological_merge: bool,
    mode_norm: str,
) -> tuple[List[RunsRecord], List[RunsRecord]]:
    """Baut aus dem Store eine vollständige, sortierte Records-Liste für ein
    komplettes Neu-Rendern des Dokuments (nicht Anhängen - siehe Backlog-
    Begründung: TOC/Seitenzahlen werden dadurch automatisch korrekt
    mitgelöst, ohne ODT-interne Felder manuell nachführen zu müssen).

    Nutzt aus, dass RunsRecord.chat bereits mit dem ISO-Datum beginnt
    (_format_heading in runner_base_imports.py: "YYYY-MM-DD  -  Titel"):
    alphabetisches Gruppieren nach chat ergibt automatisch chronologische
    Section-Reihenfolge, ohne separate Index-Verwaltung im Store.

    mode_norm bestimmt die Platzierung der Übersetzung, analog zum
    Live-Lauf in runner_schedule.py:
      - "inline": Original + Übersetzung abwechselnd
      - "end": alle Originale zuerst, dann alle Übersetzungen am Ende
      - "separate": Originale und Übersetzungen als zwei getrennte Listen
        (zweites Rückgabeelement = Übersetzungs-Dokument)
    """
    all_msgs = _sorted_stored_messages(store, chronological_merge)

    main_records: List[RunsRecord] = []
    translation_records: List[RunsRecord] = []
    for sm in all_msgs:
        main_records.append(sm.record)
        if sm.translation_record is not None:
            if mode_norm == "inline":
                main_records.append(sm.translation_record)
            else:
                translation_records.append(sm.translation_record)
    if mode_norm == "end":
        main_records.extend(translation_records)
        translation_records = []
    return main_records, translation_records


def _sorted_stored_messages(store: MessageStore, chronological_merge: bool) -> List[StoredMessage]:
    """Gemeinsame Sortierlogik für render_records_from_store() und
    render_record_pairs_from_store() - siehe dort für die Begründung
    (chat-Präfix-Trick bzw. chronologische Reihenfolge bei Interleaving)."""
    all_msgs = list(store.all_messages())
    if chronological_merge:
        all_msgs.sort(key=lambda sm: (sm.date, sm.channel_key, sm.message_id))
    else:
        all_msgs.sort(key=lambda sm: (sm.record.chat, sm.date, sm.message_id))
    return all_msgs


def render_record_pairs_from_store(store: MessageStore, chronological_merge: bool) -> List[RecordPair]:
    """Wie render_records_from_store(), aber für das side_by_side-Layout:
    liefert Original+Übersetzung explizit gepaart pro Nachricht (der Store
    hält beide ohnehin schon zusammen in einem StoredMessage - siehe
    MessageStore.add_message), statt sie in getrennte Original-/
    Übersetzungslisten aufzuteilen. Es gibt hier keinen mode_norm-Parameter:
    die Platzierung "nebeneinander" ist beim side_by_side-Layout durch die
    Tabellenspalten bereits festgelegt."""
    all_msgs = _sorted_stored_messages(store, chronological_merge)
    return [RecordPair(original=sm.record, translation=sm.translation_record) for sm in all_msgs]


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None
