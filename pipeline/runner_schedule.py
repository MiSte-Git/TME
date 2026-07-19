from __future__ import annotations

from pathlib import Path
import sys
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Callable
import asyncio
import json
import os
import re
import shutil
import inspect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from credentials import get_telegram_credentials
from telethon import TelegramClient, types

from . import runner_by_ids as _rbi
from .assets import get_custom_emoji_cache, load_custom_emoji_alts, load_assets
from .fetch import ensure_join_channel, parse_channel, parse_link
from .odt_writer import write_odt_for_records, write_odt_for_record_pairs
from .docx_convert import convert_odt_to_docx, DocxConversionError
from .speech_to_text import transcribe_voice, SpeechToTextError
from .runs import EmojiRun, ImageRun, LineBreak, RecordPair, RunsRecord, TextRun, build_runs_from_twe
from .message_collect import collect_messages_for_schedule
from .message_store import MessageStore, channel_key_for_entity, render_records_from_store, render_record_pairs_from_store
from .translation import TranslationCostTracker, TranslationError, get_provider, translate_runs
from .no_translate_words import load_no_translate_words_set
from .message_filters import fetch_messages_for_section_day as _fetch_messages_for_section_day_robust
from .logging_setup import get_logger
from .topic_utils import extract_topic_from_section
from .runner_base_imports import (
    CollectedMessage,
    DEFAULT_LOCAL_TZ,
    ScheduleCancelled,
    TelegramCredentialsMissing,
    TelegramSessionInvalid,
    _build_message_link,
    _format_heading,
    _with_retries,
)

from schedule_json import load_schedule_document, ScheduleDocument

_apply_config_overrides = _rbi._apply_config_overrides
_fetch_translation = _rbi._fetch_translation

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None  # type: ignore

from zoneinfo import ZoneInfo


logger = get_logger(__name__)

DEBUG_FETCH = False

# Konfigurierbare Anzeige von Forward-Infos (siehe runner_by_ids._SHOW_FORWARD_INFO)
_SHOW_FORWARD_INFO = True


def _get_display_author(msg, show_forward_info: bool) -> tuple[str | None, datetime | None]:
    """Bestimmt die anzuzeigende Autorenzeile und optional das Originaldatum.

    A) Wenn show_forward_info True und echte Weiterleitung vorhanden:
       - Nutze originalen Username (@username) oder from_name
       - Hänge "(forwarded, original_date=YYYY-MM-DD HH:MM:SS)" an, falls Datum vorhanden
    B) Wenn show_forward_info True, aber keine Forward-Infos:
       - Fallback auf aktuellen Sender
    C) Wenn show_forward_info False:
       - Immer nur aktueller Sender.

    Rückgabe: (display_author, original_date)
    """
    fwd = getattr(msg, "fwd_from", None)
    if show_forward_info and fwd is not None:
        def _add_candidate(val: Optional[str], acc: list[str], force_at: bool = False) -> None:
            if isinstance(val, str) and val.strip():
                txt = val.strip()
                if force_at:
                    txt = "@" + txt.lstrip("@")
                acc.append(txt)

        candidates: list[str] = []
        try:
            orig_username = getattr(fwd, "from_username", None)
        except Exception:
            orig_username = None
        _add_candidate(orig_username, candidates, force_at=True)
        try:
            from_name = getattr(fwd, "from_name", None)
        except Exception:
            from_name = None
        _add_candidate(from_name, candidates)
        try:
            post_author = getattr(fwd, "post_author", None)
        except Exception:
            post_author = None
        _add_candidate(post_author, candidates)

        # Falls Telethon das Forward-Objekt inkl. Chat/Sender-Entity geliefert hat, daraus einen Namen/Username ziehen.
        try:
            fwd_obj = getattr(msg, "forward", None)
            fwd_chat = None
            if fwd_obj is not None:
                fwd_chat = getattr(fwd_obj, "chat", None) or getattr(fwd_obj, "sender", None)
            if fwd_chat is not None:
                _add_candidate(getattr(fwd_chat, "username", None), candidates, force_at=True)
                title = getattr(fwd_chat, "title", None) or ""
                if title.strip():
                    _add_candidate(title, candidates)
                else:
                    first = getattr(fwd_chat, "first_name", None) or ""
                    last = getattr(fwd_chat, "last_name", None) or ""
                    combined = f"{first} {last}".strip()
                    _add_candidate(combined, candidates)
        except Exception:
            pass

        base = next((c for c in candidates if c), None)
        orig_date = getattr(fwd, "date", None)
        if base and isinstance(orig_date, datetime):
            suffix = orig_date.strftime("%Y-%m-%d %H:%M:%S")
            display = f"{base} (forwarded, original_date={suffix})"
            return display, orig_date
        if base:
            display = f"{base} (forwarded)"
            return display, None

    # Kein Forward oder show_forward_info=False → aktueller Sender
    s = getattr(msg, "sender", None)
    sender_username = None
    sender_name = ""
    if s is not None:
        try:
            sender_username = getattr(s, "username", None)
            title = getattr(s, "title", None) or ""
            first = getattr(s, "first_name", None) or ""
            last = getattr(s, "last_name", None) or ""
            sender_name = (title or (first + " " + last).strip()).strip()
        except Exception:
            sender_username = None
            sender_name = ""
    if isinstance(sender_username, str) and sender_username.strip():
        base = f"@{sender_username.strip()}"
        if sender_name:
            return f"{base} ({sender_name})", None
        return base, None
    if sender_name:
        return sender_name, None
    return "Unbekannter Absender", None


async def fetch_messages_for_day(
    client,
    entity,
    day_str: str,
    tz: str | None = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """Vertrauenswürdiger Wrapper für Tagesladungen.

    Nutzt die robuste Variante in message_filters.fetch_messages_for_section_day,
    damit auch dieser Pfad FloodWaits/Timeouts abfedert.
    """
    return await _fetch_messages_for_section_day_robust(
        client,
        entity,
        day_str,
        local_tz=tz,
        start_time_str=start_time,
        end_time_str=end_time,
        topic_id=None,
    )


def _ensure_png_from_export(doc_id: str) -> bool:
    cache_png = Path("cache/emoji") / f"{doc_id}.png"
    if cache_png.exists():
        return True
    exp_dir = Path("custom_emoji_export")
    if not exp_dir.exists():
        return False
    for p in exp_dir.glob(f"*{doc_id}*.png"):
        try:
            cache_png.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, cache_png)
            return True
        except Exception:
            continue
    return False


def _load_config(cfg_path: Path) -> Dict[str, Any]:
    if not cfg_path.exists() or yaml is None:
        return {}
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    show_fwd = data.get("show_forward_info")
    if isinstance(show_fwd, bool):
        globals()["_SHOW_FORWARD_INFO"] = show_fwd
    return data


def _normalize_default_channel(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    val = value.strip()
    return val or None


def _format_heading(date_iso: str, title: str) -> str:
    return f"{date_iso}  -  {title}".strip()


def _build_message_link(entity: Any, message: Any, original_link: Optional[str] = None, topic_id: Optional[int] = None) -> Optional[str]:
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
    # Topic-Link, falls Topic-ID bekannt ist
    if topic_id is not None:
        return f"https://t.me/c/{chan_str}/{int(topic_id)}/{msg_id}"
    return f"https://t.me/c/{chan_str}/{msg_id}"


def _build_day_time_range(date_obj, start_time_str: str | None, end_time_str: str | None) -> tuple[datetime, datetime]:
    """Erzeugt UTC-nahe Datetime-Grenzen aus Datum + Zeitstrings ("HH:MM:SS").

    Die tatsächliche Zeitzone/Umrechnung übernimmt `fetch_messages_for_day`/Telegram.
    Hier wird nur eine saubere lokale Range produziert, falls spätere Filter nötig werden.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(DEFAULT_LOCAL_TZ or "UTC")
    try:
        st_parts = [int(p) for p in (start_time_str or "00:00:00").split(":")]
        et_parts = [int(p) for p in (end_time_str or "23:59:59").split(":")]
        st_h, st_m, st_s = (st_parts + [0, 0, 0])[:3]
        et_h, et_m, et_s = (et_parts + [0, 0, 0])[:3]
        st = time(st_h, st_m, st_s)
        et = time(et_h, et_m, et_s)
    except Exception:
        st = time(0, 0, 0)
        et = time(23, 59, 59)
    start_dt = datetime.combine(date_obj, st, tzinfo=tz)
    end_dt = datetime.combine(date_obj, et, tzinfo=tz)
    return start_dt, end_dt


@dataclass
class ScheduleRunResult:
    """Rückgabewert für run_schedule; enthält alle erzeugten Artefakte."""
    odt_path: Path
    odt_translation_path: Path | None = None
    docx_path: Path | None = None
    docx_translation_path: Path | None = None
    docx_error: str | None = None
    translation_cost_summary: List[str] | None = None

    def __str__(self) -> str:  # pragma: no cover - convenience
        return str(self.odt_path)


async def run_schedule(
    schedule_path: Path,
    out_basename: str,
    output_dir: Path,
    translate: bool = False,
    translation_mode: str = "inline",
    target_lang: str = "de",
    include_images: bool = True,
    include_emojis: bool = True,
    source_lang: str | None = None,
    output_format: Optional[str] = None,
    chronological_merge: Optional[bool] = None,
    translation_provider: Optional[str] = None,
    incremental_mode: Optional[bool] = None,
    layout: Optional[str] = None,
    config_path: Path = Path("config.yaml"),
    local_tz_override: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    skip_lettermap_ui: bool = False,
    wait_for_mapping_cb: Optional[Callable[[], None]] = None,
    cancel_event: Optional[Any] = None,
) -> ScheduleRunResult:
    def _notify(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass
        else:
            print(msg)

    def _check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            logger.warning("Schedule-Lauf abgebrochen (Cancel-Event gesetzt).")
            _notify("Lauf wurde abgebrochen.")
            raise ScheduleCancelled("Lauf wurde vom Nutzer abgebrochen.")

    _apply_config_overrides(config_path)
    cfg = _load_config(config_path)

    # Ausgabeformat bestimmen: expliziter Parameter (z.B. aus UI) hat Vorrang;
    # ohne expliziten Wert greift die bestehende Config-Option output.make_docx.
    _valid_output_formats = {"odt", "docx", "both"}
    if isinstance(output_format, str) and output_format.strip().lower() in _valid_output_formats:
        effective_output_format = output_format.strip().lower()
    else:
        effective_output_format = "both" if _rbi._DOCX_OPTIONS.get("make_docx") else "odt"
    want_docx = effective_output_format in ("docx", "both")

    # Channel-übergreifendes chronologisches Interleaving: expliziter Parameter
    # (z.B. aus UI) hat Vorrang; ohne expliziten Wert greift config.yaml
    # (interleave_channels, Default false -> bestehendes Verhalten unverändert).
    if isinstance(chronological_merge, bool):
        effective_chronological_merge = chronological_merge
    else:
        effective_chronological_merge = bool(cfg.get("interleave_channels", False)) if isinstance(cfg, dict) else False

    # Layout: "linear" (bisheriges Verhalten, Default) oder "side_by_side"
    # (Original|Übersetzung als zweispaltige Tabellenzeile pro Nachricht).
    # translation_mode (inline/end/separate) wird bei side_by_side ignoriert -
    # die Platzierung "nebeneinander" ergibt sich aus den Tabellenspalten.
    _valid_layouts = {"linear", "side_by_side"}
    if isinstance(layout, str) and layout.strip().lower() in _valid_layouts:
        effective_layout = layout.strip().lower()
    else:
        effective_layout = str(cfg.get("layout") or "linear").strip().lower() if isinstance(cfg, dict) else "linear"
        if effective_layout not in _valid_layouts:
            effective_layout = "linear"

    # Übersetzungs-Provider bestimmen: expliziter Parameter (z.B. --provider/UI)
    # hat Vorrang; ohne expliziten Wert greift config.yaml (translation.provider,
    # Default "telegram" -> bestehendes Verhalten unverändert, kein API-Key nötig).
    translation_cfg = cfg.get("translation") if isinstance(cfg, dict) and isinstance(cfg.get("translation"), dict) else {}
    _valid_providers = {"telegram", "deepl", "google", "chatgpt"}
    if isinstance(translation_provider, str) and translation_provider.strip().lower() in _valid_providers:
        effective_translation_provider = translation_provider.strip().lower()
    else:
        effective_translation_provider = str(translation_cfg.get("provider") or "telegram").strip().lower()
        if effective_translation_provider not in _valid_providers:
            effective_translation_provider = "telegram"

    translation_provider_obj = None
    cost_tracker = TranslationCostTracker()
    if translate and effective_translation_provider != "telegram":
        try:
            translation_provider_obj = get_provider(effective_translation_provider, translation_cfg)
        except TranslationError as exc:
            _notify(
                f"Warnung: Übersetzungs-Provider '{effective_translation_provider}' nicht verfügbar "
                f"({exc}). Übersetzung wird für diesen Lauf übersprungen."
            )
            translate = False

    # Ohne aktive Uebersetzung ergibt die Zweispalten-Tabelle (Original|
    # Uebersetzung) keinen Sinn - immer linear schreiben, unabhaengig vom
    # zuletzt gewaehlten/gespeicherten Layout-Dropdown-Wert. Erst NACH obigem
    # Provider-Fallback ausgewertet, damit ein deaktivierter Provider (translate
    # wird dort ggf. auf False gesetzt) ebenfalls korrekt auf linear zurueckfaellt.
    want_side_by_side = effective_layout == "side_by_side" and translate

    # Ausnahmeliste für Emoji-Wörter, die NICHT übersetzt werden sollen (siehe
    # pipeline/emoji_words.py, data/no_translate_words.json). Nur relevant für
    # externe Provider (deepl/google/chatgpt) - der Telegram-Pfad übersetzt
    # ohnehin TextWithEntities nativ und läuft nicht über translate_runs().
    no_translate_words = load_no_translate_words_set() if effective_translation_provider != "telegram" else set()

    # Inkrementelles Dokument-Update (Store-Modus): expliziter Parameter hat
    # Vorrang; ohne expliziten Wert greift config.yaml (incremental_mode,
    # Default false -> bestehendes Verhalten unverändert: Vollgenerierung mit
    # Zeitstempel-Dateiname, kein Store).
    if isinstance(incremental_mode, bool):
        effective_incremental_mode = incremental_mode
    else:
        effective_incremental_mode = bool(cfg.get("incremental_mode", False)) if isinstance(cfg, dict) else False

    store: Optional[MessageStore] = None
    if effective_incremental_mode:
        store = MessageStore.load(schedule_path.stem)
        for w in store.load_warnings:
            logger.warning("Message-Store (%s): %s", schedule_path.stem, w)
            _notify(f"Warnung (Message-Store): {w}")
        logger.info(
            "Message-Store geladen (%s): %d bereits bekannte Nachricht(en), %d Warnung(en).",
            schedule_path.stem, len(store), len(store.load_warnings),
        )
        _notify(f"Store-Modus aktiv: {len(store)} bereits bekannte Nachricht(en) im Store ({schedule_path.stem}).")

    # Determine language codes for filenames
    cfg_source = str((cfg.get("source_lang") if isinstance(cfg, dict) else "") or (cfg.get("base_lang") if isinstance(cfg, dict) else "") or "").strip()
    eff_source = (source_lang or cfg_source or "EN")
    source_up = eff_source.upper()
    lang_up = target_lang.upper() if isinstance(target_lang, str) and target_lang else "DE"

    try:
        api_id, api_hash, phone = get_telegram_credentials()
    except RuntimeError as exc:
        error_msg = (
            "Telegram API-Zugangsdaten (TELEGRAM_API_ID/TELEGRAM_API_HASH) fehlen. "
            "Bitte in der App über 'Jetzt einloggen…' hinterlegen oder als "
            "Umgebungsvariablen setzen."
        )
        logger.error(error_msg)
        _notify(f"Fehler: {error_msg}")
        raise TelegramCredentialsMissing(error_msg) from exc

    _notify("Schedule wird geladen…")
    if schedule_path.suffix.lower() != ".json":
        raise RuntimeError("Nur JSON-Schedule-Dateien werden unterstützt. Bitte die Schedule zuerst nach JSON konvertieren.")
    schedule = load_schedule_document(schedule_path)

    schedule.default_channel = _normalize_default_channel(schedule.default_channel)
    def _section_channel(sec: Any) -> Optional[str]:
        chan = getattr(sec, "channel", None)
        if not chan:
            return None
        return _normalize_default_channel(str(chan))

    def _has_username_entry(sec: Any) -> bool:
        for raw in (getattr(sec, "links", None) or []):
            for seg in re.split(r"[;,]", str(raw)):
                if seg.strip().startswith("@"):
                    return True
        return False

    needs_default = [s for s in schedule.sections if s.fetch_by_date and not s.links and not _section_channel(s)]
    user_needs_default = [s for s in schedule.sections if _has_username_entry(s) and not (_section_channel(s) or schedule.default_channel)]
    missing_sections = needs_default + [s for s in user_needs_default if s not in needs_default]
    if missing_sections and not schedule.default_channel:
        # In UI/Non-CLI Kontext können wir keine input()-Eingabe erzwingen.
        # Stattdessen brechen wir mit einer klaren Fehlermeldung ab, die in der UI angezeigt wird.
        missing_list = ", ".join(f"{sec.date.isoformat()} :: {sec.title}" for sec in missing_sections)
        raise RuntimeError(f"Default-Channel fehlt für Sektionen: {missing_list}")

    local_tz = local_tz_override or cfg.get("local_tz") or DEFAULT_LOCAL_TZ

    logger.info(
        "Effektive Lauf-Optionen: mode=%s side_by_side=%s chronological_merge=%s "
        "translation_provider=%s target_lang=%s incremental_mode=%s output_format=%s local_tz=%s",
        (translation_mode or "inline").strip().lower(), want_side_by_side, effective_chronological_merge,
        effective_translation_provider, target_lang, effective_incremental_mode,
        effective_output_format, local_tz,
    )
    for sec in schedule.sections:
        if sec.links:
            resolution_kind = "links"
        elif sec.channel:
            resolution_kind = "channel"
        elif schedule.default_channel:
            resolution_kind = "default_channel"
        else:
            resolution_kind = "unresolved"
        sec_channel = _section_channel(sec) or schedule.default_channel
        sec_topic_id, _sec_topic_source = extract_topic_from_section(sec, schedule)
        logger.info(
            "Section '%s': date=%s channel=%s (resolved_via=%s) window=%s-%s topic_id=%s",
            sec.title, sec.date.isoformat(), sec_channel, resolution_kind, sec.start_time, sec.end_time,
            sec_topic_id if sec_topic_id is not None else "-",
        )

    letter_map_path = Path("data/letter_map.json")

    def _load_letter_map_data() -> tuple[Dict[str, str], set[str]]:
        letter_map: Dict[str, str] = {}
        mapped_ids: set[str] = set()
        if not letter_map_path.exists():
            return letter_map, mapped_ids
        try:
            data = json.loads(letter_map_path.read_text(encoding="utf-8"))
        except Exception:
            return letter_map, mapped_ids
        if not isinstance(data, dict):
            return letter_map, mapped_ids
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            primary = str(value.get("document_id", "")).strip()
            docs = value.get("document_ids")
            if isinstance(docs, list) and docs:
                primary = str(docs[0] if docs[0] is not None else "").strip() or primary
                for d in docs:
                    ds = str(d or "").strip()
                    if ds:
                        mapped_ids.add(ds)
            if primary:
                letter_map[str(key)] = primary
                mapped_ids.add(primary)
        return letter_map, mapped_ids

    letter_to_doc, mapped_doc_ids = _load_letter_map_data()

    try:
        _assets_cache = load_assets(Path("data/assets.json"))
    except Exception:
        _assets_cache = {}


    def _is_letter_doc(doc_id: str) -> bool:
        rec = _assets_cache.get(str(doc_id)) if isinstance(_assets_cache, dict) else None
        alt = ""
        if isinstance(rec, dict):
            alt = (rec.get("alt") or "").strip()
        return len(alt) == 1 and alt.isalpha()

    def _load_ignore_list() -> set[str]:
        try:
            ign_p = Path("data/lettermap_ignore.json")
            if ign_p.exists():
                data = json.loads(ign_p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return {str(x) for x in data if str(x)}
        except Exception:
            pass
        return set()

    client = TelegramClient(
        "tg_session",
        api_id,
        api_hash,
        request_retries=_rbi._CLIENT_REQUEST_RETRIES,
        timeout=_rbi._CLIENT_TIMEOUT,
        auto_reconnect=_rbi._CLIENT_AUTO_RECONNECT,
    )
    await client.connect()
    if not await client.is_user_authorized():
        error_msg = (
            "Telegram-Session ungültig oder abgelaufen. "
            "Bitte über scripts/telegram_login.py neu einloggen."
        )
        logger.error(error_msg)
        _notify(f"Fehler: {error_msg}")
        await client.disconnect()
        raise TelegramSessionInvalid(error_msg)

    try:
        if letter_to_doc and (_rbi._LM_IN_ORIGINAL or True):
            try:
                from telethon.tl.types import MessageEntityCustomEmoji
                from pipeline.assets import ensure_custom_emoji_pngs as _ens
                ents = []
                off = 0
                for d in sorted(set(letter_to_doc.values())):
                    try:
                        ents.append(MessageEntityCustomEmoji(offset=off, length=1, document_id=int(d)))
                        off += 1
                    except Exception:
                        pass
                if ents:
                    twe_fake = types.TextWithEntities(text="X" * len(ents), entities=ents)
                    await _with_retries("ensure_custom_emoji_pngs(prefetch_lettermap)", lambda: _ens(client, twe_fake))
            except Exception:
                pass

        _check_cancelled()
        _notify("Nachrichten werden gesammelt…")
        collected, used_doc_ids, resume_hints, section_stats = await collect_messages_for_schedule(
            client, schedule, local_tz, chronological_merge=effective_chronological_merge,
            min_id_by_fingerprint=(store.min_ids_by_fingerprint() if store is not None else None),
            cancel_event=cancel_event,
        )
        if not collected:
            logger.warning("Nachrichtensammlung: 0 Nachrichten gefunden für Schedule '%s'.", schedule_path.stem)
        else:
            logger.info("Nachrichtensammlung: %d Nachricht(en) gefunden (collected).", len(collected))
        interleave_chat_label = schedule.document_title or out_basename
        if resume_hints:
            for rh in resume_hints:
                hint = rh.get("hint", {}) if isinstance(rh, dict) else {}
                err = rh.get("error") if isinstance(rh, dict) else None
                msg_id = hint.get("last_ok_id")
                msg_dt = hint.get("last_ok_date")
                _notify(
                    f"WARN: Teil-Export für Section '{rh.get('section', '?')}' "
                    f"stoppt bei msg_id={msg_id}, date={msg_dt}; "
                    f"Resume-Hinweis: {hint} (Fehler: {err})"
                )

        out_dir = output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        # Im Store-Modus kein Zeitstempel im Dateinamen: dieselbe Datei wird
        # bei jedem Lauf komplett neu geschrieben (fortgeschrieben), nicht
        # jedes Mal eine neue erzeugt - das ist der Sinn des Store-Ansatzes.
        ts_part = "" if effective_incremental_mode else f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        # Add language code suffix to filename:
        # - separate mode (main file has only source language): _{SRC}
        # - other modes with translate=True: _{SRC}-{TGT}
        # - no translation: _{SRC}
        mode_here = (translation_mode or "inline").strip().lower()
        if mode_here == "separate":
            code_suffix = f"_{source_up}"
        else:
            code_suffix = f"_{source_up}-{lang_up}" if translate else f"_{source_up}"
        out_path = out_dir / f"{out_basename}{ts_part}{code_suffix}.odt"

        safe_img_dir = Path("media/odt_safe"); safe_img_dir.mkdir(parents=True, exist_ok=True)
        img_idx = 1
        missing_letters: set[str] = set()
        missing_png_tracker: set[str] = set()

        def _normalize_for_lettermap(text: str) -> str:
            s = text.replace("\uFE0F", "")
            keycaps = {
                "0\uFE0F\u20E3": "0", "1\uFE0F\u20E3": "1", "2\uFE0F\u20E3": "2", "3\uFE0F\u20E3": "3",
                "4\uFE0F\u20E3": "4", "5\uFE0F\u20E3": "5", "6\uFE0F\u20E3": "6", "7\uFE0F\u20E3": "7",
                "8\uFE0F\u20E3": "8", "9\uFE0F\u20E3": "9",
            }
            for k, v in keycaps.items():
                s = s.replace(k, v)
            s = s.replace("🔠", "").replace("🔡", "").replace("🔤", "").replace("🔢", "")
            return s

        def _map_textrun_to_letter_runs(tr: TextRun) -> List[TextRun | EmojiRun | LineBreak]:
            s = tr.text or ""
            if not s:
                return [tr]
            s_norm = _normalize_for_lettermap(s)

            def _lookup_doc_id(original: str, mapped: Optional[str]) -> Optional[str]:
                candidates: List[str] = []
                base_candidates = [original]
                if mapped:
                    base_candidates.append(mapped)
                base_candidates.extend([original.upper(), original.lower(), original.casefold()])
                if mapped:
                    base_candidates.extend([mapped.upper(), mapped.lower(), mapped.casefold()])
                for cand in base_candidates:
                    if not cand:
                        continue
                    if cand in candidates:
                        continue
                    candidates.append(cand)
                for cand in candidates:
                    did = letter_to_doc.get(cand)
                    if did:
                        return did
                return None

            out_runs: List[TextRun | EmojiRun | LineBreak] = []
            for orig_ch in s_norm:
                if orig_ch == "\n":
                    out_runs.append(LineBreak(kind="LineBreak"))
                    continue
                mapped_ch: Optional[str]
                if _rbi._LM_CASE_MODE == "upper":
                    mapped_ch = orig_ch.upper()
                elif _rbi._LM_CASE_MODE == "lower":
                    mapped_ch = orig_ch.lower()
                else:
                    mapped_ch = None

                if not (mapped_ch or orig_ch).strip():
                    out_runs.append(TextRun(kind="TextRun", text=orig_ch, href=tr.href, bold=tr.bold, italic=tr.italic,
                                             underline=tr.underline, strike=tr.strike, code=tr.code, spoiler=tr.spoiler))
                    continue

                did = _lookup_doc_id(orig_ch, mapped_ch)
                if did:
                    png_path = Path("cache/emoji") / f"{did}.png"
                    if png_path.exists() or _ensure_png_from_export(did):
                        out_runs.append(EmojiRun(kind="EmojiRun", document_id=did))
                        continue

                out_runs.append(TextRun(kind="TextRun", text=orig_ch, href=tr.href, bold=tr.bold, italic=tr.italic,
                                        underline=tr.underline, strike=tr.strike, code=tr.code, spoiler=tr.spoiler))
                if orig_ch.strip() and orig_ch.isalpha():
                    missing_letters.add(orig_ch)
            return out_runs

        def _apply_lettermap_to_textrun(tr: TextRun) -> List[TextRun | EmojiRun | LineBreak]:
            if not letter_to_doc or not lm_for_group:
                return [tr]
            if _rbi._LM_SCOPE == "all":
                return _map_textrun_to_letter_runs(tr)
            if _rbi._LM_SCOPE == "emoji-only":
                pattern = re.compile(r"([0-9])\uFE0F\u20E3")
                pos = 0
                txt = tr.text or ""
                mapped_runs: List[TextRun | EmojiRun | LineBreak] = []
                for m in pattern.finditer(txt):
                    a, b = m.span()
                    digit = m.group(1)
                    if a > pos:
                        seg = txt[pos:a]
                        if seg:
                            mapped_runs.append(
                                TextRun(
                                    kind="TextRun",
                                    text=seg,
                                    href=tr.href,
                                    bold=tr.bold,
                                    italic=tr.italic,
                                    underline=tr.underline,
                                    strike=tr.strike,
                                    code=tr.code,
                                    spoiler=tr.spoiler,
                                )
                            )
                    did = letter_to_doc.get(digit)
                    if did and ((Path("cache/emoji") / f"{did}.png").exists() or _ensure_png_from_export(did)):
                        mapped_runs.append(EmojiRun(kind="EmojiRun", document_id=did))
                    else:
                        mapped_runs.append(
                            TextRun(
                                kind="TextRun",
                                text=m.group(0),
                                href=tr.href,
                                bold=tr.bold,
                                italic=tr.italic,
                                underline=tr.underline,
                                strike=tr.strike,
                                code=tr.code,
                                spoiler=tr.spoiler,
                            )
                        )
                    pos = b
                if pos < len(txt):
                    tail = txt[pos:]
                    if tail:
                        mapped_runs.append(
                            TextRun(
                                kind="TextRun",
                                text=tail,
                                href=tr.href,
                                bold=tr.bold,
                                italic=tr.italic,
                                underline=tr.underline,
                                strike=tr.strike,
                                code=tr.code,
                                spoiler=tr.spoiler,
                            )
                        )
                return mapped_runs or [tr]
            return [tr]

        try:
            from .extract_ce import ensure_pngs_for_doc_ids
            ids_all = sorted(used_doc_ids)
            if ids_all:
                await ensure_pngs_for_doc_ids(client, [int(x) for x in ids_all])
        except Exception:
            pass

        def _invert_letter(letter_to_doc_map: Dict[str, str]) -> Dict[str, str]:
            inv: Dict[str, str] = {}
            for k, v in (letter_to_doc_map or {}).items():
                if v and v not in inv:
                    inv[v] = k
            return inv

        inv_map = _invert_letter(letter_to_doc)
        ignored = _load_ignore_list()

        all_mapped = set(mapped_doc_ids)
        missing_docs = sorted([d for d in used_doc_ids if (d not in all_mapped and d not in ignored)])
        if missing_docs:
            rep_path = Path("data/missing_lettermap_docs.json"); rep_path.parent.mkdir(parents=True, exist_ok=True)
            rep_path.write_text(json.dumps({"missing_doc_ids": missing_docs}, ensure_ascii=False, indent=2), encoding="utf-8")
            _notify(f"Hinweis: {len(missing_docs)} ungemappte Letter-Emojis (doc_id) → {rep_path}")
            if wait_for_mapping_cb:
                while True:
                    _notify("Bitte Lettermap ergänzen und im UI auf 'Fortsetzen' klicken…")
                    wait_for_mapping_cb()
                    letter_to_doc, mapped_doc_ids = _load_letter_map_data()
                    inv_map = _invert_letter(letter_to_doc)
                    ignored = _load_ignore_list()
                    missing_docs = sorted([d for d in used_doc_ids if (d not in inv_map and d not in ignored)])
                    if not missing_docs:
                        _notify("Alle erforderlichen Lettermap-Zuordnungen vorhanden. Fahre fort…")
                        break
                    rep_path.write_text(json.dumps({"missing_doc_ids": missing_docs}, ensure_ascii=False, indent=2), encoding="utf-8")
                    _notify(f"Es fehlen weiterhin {len(missing_docs)} doc_id-Zuordnungen. Bitte erneut anpassen und fortsetzen.")
                all_mapped = set(mapped_doc_ids)

        try:
            missing_pngs = [d for d in used_doc_ids if str(d) not in ignored and not (Path("cache/emoji") / f"{d}.png").exists()]
            if missing_pngs:
                missing_png_tracker.update(str(d) for d in missing_pngs)
                rep_png = Path("data/missing_pngs.json"); rep_png.parent.mkdir(parents=True, exist_ok=True)
                rep_png.write_text(json.dumps({"missing_pngs": sorted({str(d) for d in missing_pngs})}, ensure_ascii=False, indent=2), encoding="utf-8")
                _notify(f"Hinweis: {len(missing_pngs)} PNGs fehlen → {rep_png}")
        except Exception:
            pass
        initial_missing_pngs = set(missing_png_tracker)

        try:
            from telethon.tl.types import MessageEntityCustomEmoji
            from pipeline.assets import ensure_custom_emoji_pngs as _ens
            ents = []
            off = 0
            for d in missing_docs:
                try:
                    ents.append(MessageEntityCustomEmoji(offset=off, length=1, document_id=int(d)))
                    off += 1
                except Exception:
                    pass
            if ents:
                twe_fake = types.TextWithEntities(text="X" * len(ents), entities=ents)
                await _with_retries("ensure_custom_emoji_pngs(preload)", lambda: _ens(client, twe_fake))
        except Exception:
            pass

        # Effektive Einstellung für Reaktions-Anzeige (config-Override möglich)

        def _can_open_ui() -> bool:
            try:
                import PySide6  # type: ignore
            except Exception:
                return False
            import os
            if os.environ.get("DISPLAY"):
                return True
            import sys
            return sys.platform.startswith("win") or sys.platform == "darwin"

        if _rbi._LM_CONTINUE_WITHOUT_MAPPING:
            _notify("Konfiguration erlaubt: weiter ohne Mapping. Nicht gemappte Buchstaben bleiben als Text.")
        elif _rbi._LM_OPEN_UI_ON_MISSING and _can_open_ui() and not skip_lettermap_ui:
            import subprocess, sys
            try:
                proc = subprocess.Popen([sys.executable, "ui/app.py"])
            except Exception:
                proc = None
            if proc is not None:
                _notify("Lettermap-UI geöffnet. Bitte Mapping ergänzen und Fenster schließen…")
                while proc.poll() is None:
                    try:
                        letter_map_path = Path("data/letter_map.json")
                        if letter_map_path.exists():
                            data = json.loads(letter_map_path.read_text(encoding="utf-8"))
                            if isinstance(data, dict):
                                tmp: Dict[str, str] = {}
                                for k, v in data.items():
                                    if isinstance(v, dict):
                                        did = str(v.get("document_id", "")).strip()
                                        docs = v.get("document_ids")
                                        if isinstance(docs, list) and docs:
                                            did = str(docs[0] if docs[0] is not None else "").strip() or did
                                        if did:
                                            tmp[str(k)] = did
                                letter_to_doc = tmp
                                inv_map = _invert_letter(letter_to_doc)
                        still_missing = [d for d in used_doc_ids if d not in inv_map]
                        if not still_missing:
                            break
                    except Exception:
                        pass
                    if proc.poll() is not None:
                        _notify("Hinweis: UI wurde geschlossen, es fehlen noch Zuordnungen. Fahre ohne Unterbruch fort.")
                        break
                    await asyncio.sleep(1.0)
                _notify("Fahre fort…")
        elif skip_lettermap_ui and missing_docs:
            _notify("Lettermap-UI bereits geöffnet – bitte im Lettermap-Tab fehlende Zuordnungen ergänzen und danach erneut ausführen, falls nötig.")
        else:
            _notify("Hinweis: Interaktives Mapping ist nicht verfügbar. Nicht gemappte Buchstaben bleiben als Text.")

        records: List[RunsRecord] = []
        translations_acc: List[RunsRecord] = []
        pending_inline_translations: List[RunsRecord] = []
        record_pairs: List[RecordPair] = []
        previous_title: Optional[str] = None
        mode_norm = (translation_mode or "inline").strip().lower()

        # Zeitzonenobjekt für Zeitstempel in der ODT-Ausgabe
        from zoneinfo import ZoneInfo

        tz_name = local_tz or DEFAULT_LOCAL_TZ or "UTC"
        try:
            tzinfo = ZoneInfo(tz_name)
        except Exception:
            tzinfo = timezone.utc
        if mode_norm not in ("inline", "end", "separate"):
            mode_norm = "inline"
        lang_up = target_lang.upper()
        skipped_via_store = 0

        for item in collected:
            _check_cancelled()

            if mode_norm == "inline" and previous_title is not None and item.title != previous_title:
                if pending_inline_translations:
                    records.extend(pending_inline_translations)
                    pending_inline_translations.clear()
            msg = item.message
            channel_key = channel_key_for_entity(item.entity) if store is not None else None
            if store is not None and store.has_message(channel_key, msg.id):
                # Bereits aus einem früheren Lauf im Store bekannt: kein erneutes
                # Herunterladen von Medien/Übersetzen - spart insbesondere
                # wiederholte Übersetzungskosten bei jedem inkrementellen Lauf.
                skipped_via_store += 1
                previous_title = item.title
                continue
            header_runs: List[TextRun | EmojiRun | LineBreak | ImageRun] = []
            runs_list: List[TextRun | EmojiRun | LineBreak | ImageRun] = []

            # Autoren-/Forward-Info bestimmen
            display_author, orig_date = _get_display_author(msg, _SHOW_FORWARD_INFO)

            # Zeitstempel + Medientyp als erste Zeile pro Nachricht einfügen
            try:
                dt = getattr(msg, "date", None)
                if isinstance(dt, datetime):
                    local_dt = dt.astimezone(tzinfo)
                    # Datum + Uhrzeit in lokaler Zeitzone ausgeben
                    time_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    time_str = "--:--:--"
            except Exception:
                time_str = "--:--:--"

            media_type_parts: list[str] = []
            try:
                from telethon.tl.types import MessageMediaPhoto, DocumentAttributeSticker

                is_photo = isinstance(getattr(msg, "media", None), MessageMediaPhoto)
                is_image_doc = bool(getattr(msg, "document", None) and (getattr(msg.document, "mime_type", "") or "").startswith("image/"))
                is_video = bool(getattr(msg, "document", None) and (getattr(msg.document, "mime_type", "") or "").startswith("video/"))
                is_audio = bool(getattr(msg, "document", None) and (getattr(msg.document, "mime_type", "") or "").startswith("audio/"))
                is_voice = bool(getattr(msg, "document", None) and "voice" in (getattr(msg.document, "mime_type", "") or ""))
                is_file = bool(getattr(msg, "document", None) and not (is_image_doc or is_video or is_audio or is_voice))
                is_sticker = False
                if getattr(msg, "document", None):
                    for attr in (msg.document.attributes or []):
                        if isinstance(attr, DocumentAttributeSticker):
                            is_sticker = True
                            break

                if is_photo or is_image_doc or is_sticker:
                    media_type_parts.append("Bild")
                if is_video:
                    media_type_parts.append("Video")
                if is_audio or is_voice:
                    media_type_parts.append("Audio")
                if is_file:
                    media_type_parts.append("Datei")
                if getattr(msg, "media", None) is not None and not media_type_parts:
                    media_type_parts.append("Anhang")
            except Exception:
                pass

            if getattr(msg, "message", None) and str(msg.message).strip():
                has_text = True
            else:
                has_text = False

            if media_type_parts and not has_text:
                media_type_parts.append("(ohne Text)")

            header_text = time_str
            if media_type_parts:
                header_text += " – " + ", ".join(media_type_parts)

            link_url = item.link or _build_message_link(item.entity, msg, topic_id=item.topic_id)

            if effective_chronological_merge and item.channel_label:
                header_runs.append(TextRun(kind="TextRun", text=f"Kanal: {item.channel_label}", bold=True))
                header_runs.append(LineBreak(kind="LineBreak"))
            header_runs.append(TextRun(kind="TextRun", text=header_text))
            header_runs.append(LineBreak(kind="LineBreak"))
            if link_url:
                header_runs.append(TextRun(kind="TextRun", text=link_url, href=link_url, bold=True, underline=True))
                header_runs.append(LineBreak(kind="LineBreak"))
            if display_author:
                header_runs.append(TextRun(kind="TextRun", text=display_author))
                header_runs.append(LineBreak(kind="LineBreak"))
            lm_for_group = _rbi._LM_IN_ORIGINAL or ("[LM]" in str(item.title).upper())

            if include_images:
                try:
                    from telethon.tl.types import MessageMediaPhoto, DocumentAttributeSticker
                    is_photo = isinstance(msg.media, MessageMediaPhoto)
                    is_image_doc = getattr(msg, "document", None) and (getattr(msg.document, "mime_type", "") or "").startswith("image/")
                    is_sticker = False
                    if getattr(msg, "document", None):
                        for attr in (msg.document.attributes or []):
                            if isinstance(attr, DocumentAttributeSticker):
                                is_sticker = True
                                break
                    if is_photo or is_image_doc or is_sticker:
                        media_dir = Path("media"); media_dir.mkdir(exist_ok=True)
                        path = await _with_retries("download_media", lambda: msg.download_media(file=str(media_dir)))
                        if path and str(path).lower().endswith(".webp"):
                            try:
                                from PIL import Image as PILImage
                                p = Path(str(path))
                                png_path = p.with_suffix(".png")
                                with PILImage.open(p) as im:
                                    im.save(png_path, "PNG")
                                path = str(png_path)
                            except Exception:
                                pass
                        if path:
                            try:
                                from PIL import Image as PILImage, ImageOps
                                safe_name = f"img_{img_idx:04d}.png"; img_idx += 1
                                safe_path = safe_img_dir / safe_name
                                with PILImage.open(Path(str(path))) as im:
                                    im = ImageOps.exif_transpose(im)
                                    if im.mode not in ("RGB", "RGBA"):
                                        im = im.convert("RGB")
                                    im.save(safe_path, "PNG")
                                path = str(safe_path)
                            except Exception:
                                pass
                            runs_list.append(ImageRun(kind="ImageRun", path=str(path), width_cm=10.0))
                except Exception:
                    pass

            # Sprachnachrichten (Voice/Audio) optional mit Speech-to-Text transkribieren
            try:
                from telethon.tl.types import DocumentAttributeAudio

                mime = (getattr(getattr(msg, "document", None), "mime_type", "") or "").lower()
                attrs = list(getattr(getattr(msg, "document", None), "attributes", []) or [])
                has_voice_attr = False
                for a in attrs:
                    if isinstance(a, DocumentAttributeAudio) and getattr(a, "voice", False):
                        has_voice_attr = True
                        break

                is_audio_or_voice = bool(
                    getattr(msg, "document", None)
                    and (
                        mime.startswith("audio/")
                        or "voice" in mime
                        or has_voice_attr
                    )
                )

                if is_audio_or_voice:
                    media_dir = Path("media"); media_dir.mkdir(exist_ok=True)
                    audio_path_str = await _with_retries(
                        "download_media_voice",
                        lambda: msg.download_media(file=str(media_dir)),
                    )
                    if audio_path_str:
                        audio_path = Path(str(audio_path_str))
                        try:
                            stt_text = transcribe_voice(audio_path, language=(target_lang or "de"))
                        except SpeechToTextError:
                            stt_text = None
                        if stt_text:
                            runs_list.append(LineBreak(kind="LineBreak"))
                            runs_list.append(TextRun(kind="TextRun", text="Transkript der Sprachnachricht:"))
                            runs_list.append(LineBreak(kind="LineBreak"))
                            runs_list.append(TextRun(kind="TextRun", text=stt_text))
                            runs_list.append(LineBreak(kind="LineBreak"))
            except Exception:
                pass

            # Falls die Nachricht eine Antwort ist, Verweis auf die Ursprungnachricht einfügen
            try:
                rto = getattr(msg, "reply_to", None)
                base_reply_id = None
                if rto is not None:
                    base_reply_id = getattr(rto, "top_msg_id", None)
                    if base_reply_id is None:
                        base_reply_id = getattr(rto, "reply_to_msg_id", None)
                if base_reply_id is not None:
                    try:
                        base_reply_id_int = int(base_reply_id)
                    except Exception:
                        base_reply_id_int = None
                    if base_reply_id_int:
                        # Für Threads im gleichen Topic bauen wir einen Link zur Ursprungnachricht
                        reply_link = _build_message_link(item.entity, msg, topic_id=item.topic_id)
                        if reply_link:
                            reply_link = reply_link.rsplit("/", 1)[0] + f"/{base_reply_id_int}"
                            reply_msg = None
                            try:
                                reply_msg = await msg.get_reply_message()
                            except Exception:
                                reply_msg = None
                            reply_user = None
                            try:
                                sender = getattr(reply_msg, "sender", None) if reply_msg is not None else None
                                if sender is not None:
                                    username_raw = getattr(sender, "username", None)
                                    if username_raw:
                                        uname = str(username_raw).lstrip("@")
                                        if uname:
                                            reply_user = f"@{uname}"
                                    if not reply_user:
                                        fn = getattr(sender, "first_name", None) or ""
                                        ln = getattr(sender, "last_name", None) or ""
                                        name_combined = f"{fn} {ln}".strip()
                                        if name_combined:
                                            reply_user = name_combined
                            except Exception:
                                reply_user = None
                            try:
                                reply_dt = getattr(reply_msg, "date", None) if reply_msg is not None else None
                                if isinstance(reply_dt, datetime):
                                    reply_dt_local = reply_dt.astimezone(tzinfo)
                                    reply_dt_str = reply_dt_local.strftime("%d.%m.%Y %H:%M:%S")
                                else:
                                    reply_dt_str = "--.--.---- --:--:--"
                            except Exception:
                                reply_dt_str = "--.--.---- --:--:--"
                            reply_user = reply_user or "Unbekannt"
                            header_runs.append(TextRun(kind="TextRun", text=f"Antwort auf: {reply_user} – {reply_dt_str} – {reply_link}", href=reply_link))
                            header_runs.append(LineBreak(kind="LineBreak"))
            except Exception:
                pass

            if (msg.message or "").strip():
                twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                await _with_retries("load_custom_emoji_alts", lambda: load_custom_emoji_alts(client, twe))
                missing_after: set[str] = set()
                if include_emojis:
                    try:
                        from pipeline.assets import ensure_pngs_for_twe as _ensure_pngs_for_twe  # type: ignore[attr-defined]
                        res = await _with_retries("ensure_pngs_for_twe", lambda: _ensure_pngs_for_twe(client, twe))
                        if isinstance(res, set):
                            missing_after = {str(int(d)) for d in res if d is not None and str(int(d)) not in ignored}
                    except Exception:
                        missing_after = set()
                    if missing_after:
                        missing_png_tracker.update(missing_after)
                        _notify(
                            f"Hinweis: {len(missing_after)} Emoji-PNGs fehlen weiterhin nach Generierung für Nachricht {msg.id}:"
                            f" {', '.join(sorted(missing_after))}"
                        )
                runs = build_runs_from_twe(twe)
                ce_map = get_custom_emoji_cache()
                new_runs: List[TextRun | EmojiRun | LineBreak | ImageRun] = []
                for rr in runs:
                    if isinstance(rr, EmojiRun):
                        if not include_emojis:
                            doc_id = rr.document_id
                            alt = ce_map.get(int(doc_id)) if doc_id.isdigit() else None
                            new_runs.append(TextRun(kind="TextRun", text=alt or f"[CE:{doc_id}]"))
                        else:
                            png_path = Path("cache/emoji") / f"{rr.document_id}.png"
                            if png_path.exists() or _ensure_png_from_export(rr.document_id):
                                new_runs.append(rr)
                            else:
                                alt = ce_map.get(int(rr.document_id)) if rr.document_id.isdigit() else None
                                new_runs.append(TextRun(kind="TextRun", text=alt or f"[CE:{rr.document_id}]"))
                    elif isinstance(rr, TextRun):
                        mapped = _apply_lettermap_to_textrun(rr)
                        new_runs.extend(mapped)
                    else:
                        new_runs.append(rr)
                runs = new_runs
                runs_list.extend(runs)
            if runs_list:
                base_meta: Dict[str, Any] = {}
                if item.subheading:
                    base_meta["subheading"] = item.subheading
                if effective_chronological_merge and item.channel_label:
                    # rec.chat ist beim chronologischen Mischen für alle
                    # Nachrichten identisch (interleave_chat_label) - der
                    # rohe Kanalname wird hier auf jedem Datensatz
                    # mitgegeben; odt_writer.py entscheidet beim finalen
                    # Rendern (nicht hier), bei welchem Datensatz er als
                    # H2-Zwischenüberschrift auftaucht (erstes Auftreten in
                    # der tatsächlich geschriebenen Reihenfolge - wichtig für
                    # korrekte Wiederholungen bei Store-Neu-Rendern, siehe
                    # message_store.render_records_from_store).
                    base_meta["channel_label"] = item.channel_label
                if header_runs:
                    base_meta["header_runs"] = header_runs
                if link_url:
                    base_meta["link"] = link_url
                chat_label = interleave_chat_label if effective_chronological_merge else item.title
                original_record = RunsRecord(chat=chat_label, message_id=msg.id, runs=runs_list, meta=base_meta or None)
                records.append(original_record)
                translation_record_for_store: Optional[RunsRecord] = None

                if translate and msg and ((msg.message or "").strip()):
                    try:
                        twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                        runs_tr: List[TextRun | EmojiRun | LineBreak | ImageRun] | None = None
                        if effective_translation_provider == "telegram":
                            tr = await _fetch_translation(client, item.entity, msg.id, twe, target_lang)
                            if tr is not None:
                                tr_non_null = tr
                                await _with_retries("load_custom_emoji_alts", lambda: load_custom_emoji_alts(client, tr_non_null))
                                runs_tr = build_runs_from_twe(tr_non_null)
                        else:
                            await _with_retries("load_custom_emoji_alts", lambda: load_custom_emoji_alts(client, twe))
                            source_runs = build_runs_from_twe(twe)
                            translated_runs, tr_result = await translate_runs(
                                source_runs, target_lang, translation_provider_obj, source_lang=source_lang,
                                doc_to_letters=inv_map, no_translate_words=no_translate_words,
                            )
                            cost_tracker.add(tr_result)
                            for w in tr_result.warnings:
                                _notify(f"Warnung (Übersetzung, {effective_translation_provider}): {w}")
                            runs_tr = translated_runs
                        if runs_tr is not None:
                            ce_map = get_custom_emoji_cache()
                            new_runs_tr: List[TextRun | EmojiRun | LineBreak | ImageRun] = []
                            for rr in runs_tr:
                                if isinstance(rr, EmojiRun):
                                    png_path = Path("cache/emoji") / f"{rr.document_id}.png"
                                    if png_path.exists():
                                        new_runs_tr.append(rr)
                                    else:
                                        alt = ce_map.get(int(rr.document_id)) if rr.document_id.isdigit() else None
                                        new_runs_tr.append(TextRun(kind="TextRun", text=alt or f"[CE:{rr.document_id}]"))
                                elif isinstance(rr, TextRun):
                                    mapped = _apply_lettermap_to_textrun(rr)
                                    new_runs_tr.extend(mapped)
                                else:
                                    new_runs_tr.append(rr)
                            tr_meta: Dict[str, Any] = {}
                            if item.subheading:
                                tr_meta["subheading"] = item.subheading
                            if effective_chronological_merge and item.channel_label:
                                tr_meta["channel_label"] = item.channel_label
                            if header_runs:
                                tr_meta["header_runs"] = header_runs
                            if link_url:
                                tr_meta["link"] = link_url
                            translation_record = RunsRecord(
                                chat=f"{chat_label} - {lang_up}",
                                message_id=msg.id,
                                runs=new_runs_tr,
                                meta=tr_meta or None,
                            )
                            translation_record_for_store = translation_record
                            if mode_norm == "inline":
                                pending_inline_translations.append(translation_record)
                            else:
                                translations_acc.append(translation_record)
                    except TranslationError as exc:
                        logger.warning(
                            "Übersetzung (%s) für Nachricht %s fehlgeschlagen: %s",
                            effective_translation_provider, msg.id, exc,
                        )
                        _notify(f"Warnung: Übersetzung ({effective_translation_provider}) für Nachricht {msg.id} fehlgeschlagen: {exc}")
                    except Exception:
                        pass

                if store is not None:
                    store.add_message(channel_key, msg.id, getattr(msg, "date", None), original_record, translation_record_for_store)
                if want_side_by_side:
                    record_pairs.append(RecordPair(original=original_record, translation=translation_record_for_store))
            previous_title = item.title

        logger.info(
            "Nachrichtenverarbeitung: %d neu verarbeitet, %d via Store übersprungen (von %d gesammelt).",
            len(collected) - skipped_via_store, skipped_via_store, len(collected),
        )

        if translate and mode_norm == "end" and translations_acc:
            records.extend(translations_acc)
        if pending_inline_translations:
            records.extend(pending_inline_translations)

        if store is not None:
            _notify(f"Store-Modus: {skipped_via_store} bereits bekannte Nachricht(en) übersprungen, {len(collected) - skipped_via_store} neu verarbeitet.")
            for fp, stats in section_stats.items():
                store.update_section_state(fp, stats["channel_key"], stats["last_message_id"], stats["last_message_date"])
            try:
                store.save()
            except Exception as exc:
                _notify(f"Warnung: Message-Store konnte nicht gespeichert werden ({exc}); bereits verarbeitete neue Nachrichten werden beim nächsten Lauf erneut geholt.")


        if missing_png_tracker:
            rep_png = Path("data/missing_pngs.json"); rep_png.parent.mkdir(parents=True, exist_ok=True)
            final_sorted = sorted(missing_png_tracker)
            rep_png.write_text(json.dumps({"missing_pngs": final_sorted}, ensure_ascii=False, indent=2), encoding="utf-8")
            if missing_png_tracker != initial_missing_pngs:
                _notify(f"Hinweis: {len(final_sorted)} Emoji-PNGs fehlen weiterhin → {rep_png}")

        if store is not None:
            # Komplettes Dokument aus dem sortierten Store neu rendern statt
            # nur die Live-Ergebnisse dieses Laufs zu schreiben - so tauchen
            # auch alle in früheren Läufen bereits verarbeiteten Nachrichten
            # wieder auf (TOC/Seitenzahlen lösen sich dabei automatisch mit,
            # da write_odt_for_records ohnehin immer alles neu schreibt).
            if want_side_by_side:
                record_pairs = render_record_pairs_from_store(store, effective_chronological_merge)
            else:
                records, translations_acc = render_records_from_store(store, effective_chronological_merge, mode_norm)

        doc_title_base = schedule.document_title
        if resume_hints:
            first_hint = resume_hints[0].get("hint", {}) if isinstance(resume_hints[0], dict) else {}
            stop_id = first_hint.get("last_ok_id")
            doc_title_base = f"{doc_title_base or out_basename} (Teil-Export, Stop bei msg_id {stop_id})"

        styles = {
            "paragraph": {"base": "P.Base"},
            "text": {"base": "T.Base"},
            "graphic": {"inline_emoji": "G.InlineEmoji"},
        }

        _check_cancelled()
        final_count = len(record_pairs) if want_side_by_side else len(records)
        if final_count == 0:
            logger.warning(
                "0 finale %s für ODT-Ausgabe '%s' - Schedule/Zeitfenster/Kanal prüfen.",
                "record_pairs" if want_side_by_side else "Records", out_path,
            )
            _notify(
                "Achtung: Für diesen Lauf wurden 0 Nachrichten gefunden - Kanal, Zeitfenster oder "
                f"Telegram-Session prüfen. Es wird trotzdem ein (leeres) ODT geschrieben ('{out_path}')."
            )
        else:
            logger.info(
                "Vor ODT-Schreibvorgang: %d finale %s für '%s'.",
                final_count, "record_pairs" if want_side_by_side else "Records", out_path,
            )

        _notify("ODT wird geschrieben…")
        if want_side_by_side:
            write_odt_for_record_pairs(
                record_pairs, out_path, styles, doc_title=doc_title_base,
                original_label=f"Original ({source_up})", translation_label=f"Übersetzung ({lang_up})",
            )
        else:
            write_odt_for_records(records, out_path, styles, doc_title=doc_title_base)
        logger.info("ODT geschrieben: %s (%d Eintrag/Einträge).", out_path, final_count)

        extra_path: Optional[Path] = None
        if not want_side_by_side and mode_norm == "separate" and translations_acc:
            tr_title = f"{schedule.document_title} - {lang_up}" if schedule.document_title else f"{out_basename} - {lang_up}"
            if resume_hints:
                stop_id = (resume_hints[0].get("hint", {}) or {}).get("last_ok_id")
                tr_title = f"{tr_title} (Teil-Export, Stop bei msg_id {stop_id})"
            extra_path = out_dir / f"{out_basename}{ts_part}_{lang_up}.odt"
            _notify("Übersetzungs-ODT wird geschrieben…")
            write_odt_for_records(translations_acc, extra_path, styles, doc_title=tr_title)
            logger.info("Übersetzungs-ODT geschrieben: %s (%d Eintrag/Einträge).", extra_path, len(translations_acc))

        try:
            if missing_letters:
                rep_path = Path("data/missing_lettermap.json")
                rep_path.parent.mkdir(parents=True, exist_ok=True)
                rep_path.write_text(json.dumps({"missing": sorted(missing_letters)}, ensure_ascii=False, indent=2), encoding="utf-8")
                _notify(f"Hinweis: {len(missing_letters)} nicht zugeordnete Zeichen → {rep_path}")
        except Exception:
            pass

        docx_path: Optional[Path] = None
        docx_translation_path: Optional[Path] = None
        docx_errors: List[str] = []
        if want_docx:
            _notify("DOCX wird erzeugt…")
            prefer_raw = _rbi._DOCX_OPTIONS.get("converter")
            prefer = str(prefer_raw) if isinstance(prefer_raw, str) and prefer_raw.strip() else None
            out_dir_cfg = _rbi._DOCX_OPTIONS.get("out_dir")
            docx_outdir = Path(out_dir_cfg).expanduser() if isinstance(out_dir_cfg, str) and out_dir_cfg.strip() else out_path.parent
            ref_doc_cfg = _rbi._DOCX_OPTIONS.get("pandoc_reference_docx")
            ref_doc = Path(ref_doc_cfg).expanduser() if isinstance(ref_doc_cfg, str) and ref_doc_cfg.strip() else None

            def _convert_to_docx_safe(odt_file: Path, label: str) -> tuple[Optional[Path], Optional[str]]:
                # ODT bleibt in jedem Fall erhalten; Fehler brechen den Lauf nicht ab.
                try:
                    docx_file = convert_odt_to_docx(odt_file, outdir=docx_outdir, prefer=prefer, reference_docx=ref_doc)
                    _notify(f"{label}-DOCX erzeugt: {docx_file}")
                    return docx_file, None
                except (DocxConversionError, FileNotFoundError) as exc:
                    err = str(exc)
                    _notify(f"Warnung: {label}-DOCX-Konvertierung fehlgeschlagen: {err}")
                    return None, err
                except Exception as exc:
                    err = f"Unerwarteter Fehler: {exc}"
                    _notify(f"Warnung: {label}-DOCX-Konvertierung fehlgeschlagen: {err}")
                    return None, err

            docx_path, err_main = _convert_to_docx_safe(out_path, "Original")
            if err_main:
                docx_errors.append(err_main)
            if extra_path is not None:
                docx_translation_path, err_extra = _convert_to_docx_safe(extra_path, "Übersetzung")
                if err_extra:
                    docx_errors.append(err_extra)

        cost_summary_lines = cost_tracker.summary_lines() if cost_tracker.has_data() else None
        if cost_summary_lines:
            for line in cost_summary_lines:
                _notify(f"Übersetzungskosten: {line}")

        _notify("Fertig.")
        return ScheduleRunResult(
            odt_path=out_path,
            odt_translation_path=extra_path,
            docx_path=docx_path,
            docx_translation_path=docx_translation_path,
            docx_error="; ".join(docx_errors) if docx_errors else None,
            translation_cost_summary=cost_summary_lines,
        )
    finally:
        if client is not None:
            try:
                res = client.disconnect()
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass
