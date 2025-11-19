from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

import asyncio
import json
import re
import shutil

from telethon import TelegramClient, types
import os

from . import runner_by_ids as _rbi
from .assets import get_custom_emoji_cache, load_custom_emoji_alts, load_assets
from .fetch import ensure_join_channel, parse_channel, parse_link
from .odt_writer import write_odt_for_records
from .runs import EmojiRun, ImageRun, LineBreak, RunsRecord, TextRun, build_runs_from_twe

from schedule_json import load_legacy_schedule, load_schedule_document, ScheduleDocument
from tg_by_date_to_odt_modes import LOCAL_TZ as DEFAULT_LOCAL_TZ

_apply_config_overrides = _rbi._apply_config_overrides
_with_retries = _rbi._with_retries
_fetch_translation = _rbi._fetch_translation


@dataclass
class CollectedMessage:
    title: str
    entity: Any
    message: Any
    subheading: Optional[str] = None
    link: Optional[str] = None


try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None  # type: ignore

try:
    from tg_by_date_to_odt_modes import fetch_messages_for_day  # type: ignore
except Exception:  # pragma: no cover - legacy script missing
    fetch_messages_for_day = None  # type: ignore


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
    return data if isinstance(data, dict) else {}


def _normalize_default_channel(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    val = value.strip()
    return val or None


def _format_heading(date_iso: str, title: str) -> str:
    return f"{date_iso}  -  {title}".strip()


def _build_message_link(entity: Any, message: Any, original_link: Optional[str] = None) -> Optional[str]:
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
        st = time(*st_parts[:3])
        et = time(*et_parts[:3])
    except Exception:
        st = time(0, 0, 0)
        et = time(23, 59, 59)
    start_dt = datetime.combine(date_obj, st, tzinfo=tz)
    end_dt = datetime.combine(date_obj, et, tzinfo=tz)
    return start_dt, end_dt


async def _collect_messages_for_schedule(
    client: TelegramClient,
    schedule: ScheduleDocument,
    local_tz: Optional[str],
) -> tuple[List[CollectedMessage], set[str]]:
    collected: List[CollectedMessage] = []
    used_doc_ids: set[str] = set()
    debug_dir = Path("data/debug")
    if _rbi._DEBUG_DUMP_ENTITIES:
        debug_dir.mkdir(parents=True, exist_ok=True)

    default_entity_cache: Dict[str, Any] = {}

    def _get_default_entity_key(channel: Optional[str]) -> Optional[str]:
        if not channel:
            return None
        return str(channel)

    async def _ensure_entity(raw: Any) -> Any:
        entity = await _with_retries("get_entity", lambda: client.get_entity(raw))
        if entity:
            await ensure_join_channel(client, entity)
        return entity

    for section in schedule.sections:
        heading = _format_heading(section.date.strftime("%Y-%m-%d"), section.title)
        subheading = section.subheading or None
        links = [lnk for lnk in section.links if lnk]
        if links:
            for link in links:
                try:
                    peer_raw, msg_id = parse_link(link)
                    entity = await _ensure_entity(peer_raw)
                    if not entity:
                        continue
                    msg = await _with_retries("get_messages", lambda: client.get_messages(entity, ids=msg_id))
                    if not msg:
                        continue
                    link_url = _build_message_link(entity, msg, original_link=link)
                    collected.append(CollectedMessage(title=heading, entity=entity, message=msg, subheading=subheading, link=link_url))
                    twe_tmp = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                    try:
                        await _with_retries("load_custom_emoji_alts(pre)", lambda: load_custom_emoji_alts(client, twe_tmp))
                    except Exception:
                        pass
                    for e in (msg.entities or []):
                        if isinstance(e, types.MessageEntityCustomEmoji):
                            did = getattr(e, "document_id", None)
                            if did:
                                used_doc_ids.add(str(did))
                    if _rbi._DEBUG_DUMP_ENTITIES:
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
                                    **({"document_id": str(getattr(ent, "document_id", ""))} if isinstance(ent, types.MessageEntityCustomEmoji) else {}),
                                }
                                for ent in (msg.entities or [])
                            ],
                        }
                        dp = debug_dir / f"entities_{str(peer_raw).replace('/', '_')}_{msg_id}.json"
                        try:
                            dp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                except Exception:
                    continue
            continue

        # fetch by date
        default_channel = section.channel or schedule.default_channel
        key = _get_default_entity_key(default_channel)
        if key is None:
            continue
        if key not in default_entity_cache:
            raw = parse_channel(default_channel)
            entity = await _ensure_entity(raw)
            if not entity:
                _notify(f"Hinweis: Kanal '{default_channel}' konnte nicht geladen werden.")
                default_entity_cache[key] = None
            else:
                default_entity_cache[key] = entity
        entity = default_entity_cache.get(key)
        if not entity:
            continue
        if fetch_messages_for_day is None:
            raise RuntimeError("fetch_messages_for_day ist nicht verfügbar.")
        day_str = section.date.strftime("%d/%m/%Y")
        # Zeitfenster pro Sektion zur tatsächlichen Filterung der Nachrichten
        start_dt, end_dt = _build_day_time_range(section.date, section.start_time, section.end_time)
        start_time_str = section.start_time or start_dt.strftime("%H:%M:%S")
        end_time_str = section.end_time or end_dt.strftime("%H:%M:%S")
        msgs = await fetch_messages_for_day(
            client,
            entity,
            day_str,
            tz=local_tz,
            start_time=start_time_str,
            end_time=end_time_str,
        )  # type: ignore[arg-type]
        if not msgs:
            _notify(f"Hinweis: Keine Nachrichten für {heading} gefunden.")
            continue
        for msg in msgs:
            link_url = _build_message_link(entity, msg)
            collected.append(CollectedMessage(title=heading, entity=entity, message=msg, subheading=subheading, link=link_url))
            twe_tmp = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
            try:
                await _with_retries("load_custom_emoji_alts(pre)", lambda: load_custom_emoji_alts(client, twe_tmp))
            except Exception:
                pass
            for e in (msg.entities or []):
                if isinstance(e, types.MessageEntityCustomEmoji):
                    did = getattr(e, "document_id", None)
                    if did:
                        used_doc_ids.add(str(did))
            if _rbi._DEBUG_DUMP_ENTITIES:
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
                            **({"document_id": str(getattr(ent, "document_id", ""))} if isinstance(ent, types.MessageEntityCustomEmoji) else {}),
                        }
                        for ent in (msg.entities or [])
                    ],
                }
                dp = debug_dir / f"entities_{str(peer_id).replace('-', 'm')}_{msg.id}.json"
                try:
                    dp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
    return collected, used_doc_ids


async def run_schedule(
    schedule_path: Path,
    out_basename: str,
    output_dir: Path,
    translate: bool = False,
    translation_mode: str = "inline",
    target_lang: str = "de",
    include_images: bool = True,
    include_emojis: bool = True,
    config_path: Path = Path("config.yaml"),
    local_tz_override: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    skip_lettermap_ui: bool = False,
    wait_for_mapping_cb: Optional[Callable[[], None]] = None,
) -> Path | tuple[Path, Path]:
    def _notify(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass
        else:
            print(msg)

    _apply_config_overrides(config_path)
    cfg = _load_config(config_path)

    # Determine language codes for filenames
    source_lang = str((cfg.get("source_lang") if isinstance(cfg, dict) else "") or (cfg.get("base_lang") if isinstance(cfg, dict) else "") or "EN").strip()
    source_up = (source_lang or "EN").upper()
    lang_up = target_lang.upper() if isinstance(target_lang, str) and target_lang else "DE"

    # Resolve Telegram API credentials (env → config)
    api_id = os.environ.get("TELEGRAM_API_ID") or str(
        (cfg.get("telegram_api_id") if isinstance(cfg, dict) else "")
        or (cfg.get("api_id") if isinstance(cfg, dict) else "")
        or ((cfg.get("telegram") or {}).get("api_id") if isinstance(cfg.get("telegram"), dict) else "")
        or ""
    ).strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or str(
        (cfg.get("telegram_api_hash") if isinstance(cfg, dict) else "")
        or (cfg.get("api_hash") if isinstance(cfg, dict) else "")
        or ((cfg.get("telegram") or {}).get("api_hash") if isinstance(cfg.get("telegram"), dict) else "")
        or ""
    ).strip()
    # XDG config (~/.config/telegram-odt/credentials.{json,yaml}) or env file
    if (not api_id or not api_hash):
        try:
            xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
            candidates = [
                Path(xdg) / "telegram-odt" / "credentials.json",
                Path(xdg) / "telegram-odt" / "credentials.yaml",
                Path(xdg) / "telegram-odt" / "credentials.yml",
                Path(xdg) / "telegram-odt" / "credentials.env",
                Path(xdg) / "telegram-odt.env",
            ]
            for p in candidates:
                if not p.exists():
                    continue
                if p.suffix.lower() == ".json":
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            api_id = api_id or str(data.get("api_id") or data.get("TELEGRAM_API_ID") or "").strip()
                            api_hash = api_hash or str(data.get("api_hash") or data.get("TELEGRAM_API_HASH") or "").strip()
                    except Exception:
                        pass
                elif p.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
                    try:
                        data = yaml.safe_load(p.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            api_id = api_id or str(data.get("api_id") or data.get("TELEGRAM_API_ID") or "").strip()
                            api_hash = api_hash or str(data.get("api_hash") or data.get("TELEGRAM_API_HASH") or "").strip()
                    except Exception:
                        pass
                else:
                    # .env style
                    try:
                        for line in p.read_text(encoding="utf-8").splitlines():
                            if "=" not in line:
                                continue
                            k, v = line.split("=", 1)
                            k = k.strip(); v = v.strip().strip("'\"")
                            if k == "TELEGRAM_API_ID" and not api_id:
                                api_id = v
                            elif k == "TELEGRAM_API_HASH" and not api_hash:
                                api_hash = v
                    except Exception:
                        pass
                if api_id and api_hash:
                    break
        except Exception:
            pass
    if not api_id or not api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID/TELEGRAM_API_HASH fehlen. Setze sie als Umgebungsvariablen ODER lege ~/.config/telegram-odt/credentials.json "
            "mit {\"api_id\":123456, \"api_hash\":\"...\"} an."
        )

    _notify("Schedule wird geladen…")
    if schedule_path.suffix.lower() == ".json":
        schedule = load_schedule_document(schedule_path)
    else:
        schedule = load_legacy_schedule(schedule_path)

    needs_default = [s for s in schedule.sections if s.fetch_by_date and not s.links]
    schedule.default_channel = _normalize_default_channel(schedule.default_channel)
    if needs_default and not schedule.default_channel:
        _notify("Folgende Sektionen benötigen einen Default-Channel:")
        for sec in needs_default:
            _notify(f"  - {sec.date.isoformat()} :: {sec.title}")
        user_input = input("Bitte Default-Channel (@name oder Link) eingeben: ").strip()
        if not user_input:
            raise SystemExit("Abbruch: Default-Channel erforderlich.")
        schedule.default_channel = user_input

    local_tz = local_tz_override or cfg.get("local_tz") or DEFAULT_LOCAL_TZ

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

    async with TelegramClient(
        "tg_session", api_id, api_hash,
        request_retries=_rbi._CLIENT_REQUEST_RETRIES,
        timeout=_rbi._CLIENT_TIMEOUT,
        auto_reconnect=_rbi._CLIENT_AUTO_RECONNECT,
    ) as client:
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

        _notify("Nachrichten werden gesammelt…")
        collected, used_doc_ids = await _collect_messages_for_schedule(client, schedule, local_tz)

        out_dir = output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Add language code suffix to filename:
        # - separate mode (main file has only source language): _{SRC}
        # - other modes with translate=True: _{SRC}-{TGT}
        # - no translation: _{SRC}
        mode_here = (translation_mode or "inline").strip().lower()
        if mode_here == "separate":
            code_suffix = f"_{source_up}"
        else:
            code_suffix = f"_{source_up}-{lang_up}" if translate else f"_{source_up}"
        out_path = out_dir / f"{out_basename}_{ts}{code_suffix}.odt"

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
        previous_title: Optional[str] = None
        mode_norm = (translation_mode or "inline").strip().lower()
        if mode_norm not in ("inline", "end", "separate"):
            mode_norm = "inline"
        lang_up = target_lang.upper()
        for item in collected:
            if mode_norm == "inline" and previous_title is not None and item.title != previous_title:
                if pending_inline_translations:
                    records.extend(pending_inline_translations)
                    pending_inline_translations.clear()
            msg = item.message
            runs_list: List[TextRun | EmojiRun | LineBreak | ImageRun] = []
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
                                p = Path(path)
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
                                with PILImage.open(Path(path)) as im:
                                    im = ImageOps.exif_transpose(im)
                                    if im.mode not in ("RGB", "RGBA"):
                                        im = im.convert("RGB")
                                    im.save(safe_path, "PNG")
                                path = str(safe_path)
                            except Exception:
                                pass
                            runs_list.append(ImageRun(kind="ImageRun", path=path, width_cm=10.0))
                except Exception:
                    pass

            if (msg.message or "").strip():
                twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                await _with_retries("load_custom_emoji_alts", lambda: load_custom_emoji_alts(client, twe))
                missing_after: set[str] = set()
                if include_emojis:
                    try:
                        from pipeline.assets import ensure_pngs_for_twe as _ensure_pngs_for_twe
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
                link_url = item.link or _build_message_link(item.entity, msg)
                if link_url:
                    base_meta["link"] = link_url
                records.append(RunsRecord(chat=item.title, message_id=msg.id, runs=runs_list, meta=base_meta or None))

                if translate and msg and ((msg.message or "").strip()):
                    try:
                        twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                        tr = await _fetch_translation(client, item.entity, msg.id, twe, target_lang)
                        if tr is not None:
                            await _with_retries("load_custom_emoji_alts", lambda: load_custom_emoji_alts(client, tr))
                            runs_tr = build_runs_from_twe(tr)
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
                            if link_url:
                                tr_meta["link"] = link_url
                            translation_record = RunsRecord(
                                chat=f"{item.title} - {lang_up}",
                                message_id=msg.id,
                                runs=new_runs_tr,
                                meta=tr_meta or None,
                            )
                            if mode_norm == "inline":
                                pending_inline_translations.append(translation_record)
                            else:
                                translations_acc.append(translation_record)
                    except Exception:
                        pass
            previous_title = item.title

        if translate and mode_norm == "end" and translations_acc:
            records.extend(translations_acc)
        if pending_inline_translations:
            records.extend(pending_inline_translations)

        if missing_png_tracker:
            rep_png = Path("data/missing_pngs.json"); rep_png.parent.mkdir(parents=True, exist_ok=True)
            final_sorted = sorted(missing_png_tracker)
            rep_png.write_text(json.dumps({"missing_pngs": final_sorted}, ensure_ascii=False, indent=2), encoding="utf-8")
            if missing_png_tracker != initial_missing_pngs:
                _notify(f"Hinweis: {len(final_sorted)} Emoji-PNGs fehlen weiterhin → {rep_png}")

        styles = {
            "paragraph": {"base": "P.Base"},
            "text": {"base": "T.Base"},
            "graphic": {"inline_emoji": "G.InlineEmoji"},
        }

        _notify("ODT wird geschrieben…")
        write_odt_for_records(records, out_path, styles, doc_title=schedule.document_title)

        extra_path: Optional[Path] = None
        if translate and mode_norm == "separate" and translations_acc:
            tr_title = f"{schedule.document_title} - {lang_up}" if schedule.document_title else f"{out_basename} - {lang_up}"
            extra_path = out_dir / f"{out_basename}_{ts}_{lang_up}.odt"
            _notify("Übersetzungs-ODT wird geschrieben…")
            write_odt_for_records(translations_acc, extra_path, styles, doc_title=tr_title)

        try:
            if missing_letters:
                rep_path = Path("data/missing_lettermap.json")
                rep_path.parent.mkdir(parents=True, exist_ok=True)
                rep_path.write_text(json.dumps({"missing": sorted(missing_letters)}, ensure_ascii=False, indent=2), encoding="utf-8")
                _notify(f"Hinweis: {len(missing_letters)} nicht zugeordnete Zeichen → {rep_path}")
        except Exception:
            pass

        _notify("Fertig.")
        return (out_path, extra_path) if extra_path else out_path
