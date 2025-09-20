from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict, Callable, Awaitable
from datetime import datetime
import asyncio
import random
import json
import re
import shutil

from telethon import TelegramClient, functions, types

from .fetch import parse_link, ensure_join_channel
from .assets import load_custom_emoji_alts, get_custom_emoji_cache
from .runs import RunsRecord, build_runs_from_twe, ImageRun, EmojiRun, TextRun, LineBreak
from .odt_writer import write_odt_for_records
from .recompose import _text_to_runs as lettermap_text_to_runs

# YAML optional laden (keine neue Abhängigkeit erforderlich)
try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore


# --- Retry/Backoff Einstellungen (Default) ---
_RETRIES = 5
_BACKOFF = 1.6
_INITIAL_DELAY = 0.8
_JITTER = 0.3

# --- Telegram-Client Defaults ---
_CLIENT_TIMEOUT = 20
_CLIENT_REQUEST_RETRIES = 0
_CLIENT_AUTO_RECONNECT = True

# --- Lettermap Defaults ---
_LM_CASE_MODE = "upper"   # upper|lower|none
_LM_FALLBACK = "text"     # text|skip
_LM_IN_ORIGINAL = False    # standardmäßig kein Lettermapping im Original
_LM_OPEN_UI_ON_MISSING = True
_LM_CONTINUE_WITHOUT_MAPPING = False
_LM_SCOPE = "emoji-only"  # emoji-only|all|none
_DEBUG_DUMP_ENTITIES = False


def _apply_config_overrides(cfg_path: Path = Path("config.yaml")) -> None:
    global _RETRIES, _BACKOFF, _INITIAL_DELAY, _JITTER
    global _CLIENT_TIMEOUT, _CLIENT_REQUEST_RETRIES, _CLIENT_AUTO_RECONNECT
    if not cfg_path.exists():
        return
    try:
        data = None
        if yaml is not None:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        retry = data.get("retry") or data.get("retries") or {}
        if isinstance(retry, dict):
            _RETRIES = int(retry.get("retries", _RETRIES))
            _BACKOFF = float(retry.get("backoff", _BACKOFF))
            _INITIAL_DELAY = float(retry.get("initial_delay", _INITIAL_DELAY))
            _JITTER = float(retry.get("jitter", _JITTER))
        tgc = data.get("telegram_client") or {}
        if isinstance(tgc, dict):
            _CLIENT_TIMEOUT = int(tgc.get("timeout", _CLIENT_TIMEOUT))
            _CLIENT_REQUEST_RETRIES = int(tgc.get("request_retries", _CLIENT_REQUEST_RETRIES))
            _CLIENT_AUTO_RECONNECT = bool(tgc.get("auto_reconnect", _CLIENT_AUTO_RECONNECT))
        # Lettermap
        lmode = (data.get("lettermap_case_mode") or str(_LM_CASE_MODE)).strip().lower()
        if lmode in ("upper","lower","none"):
            globals()["_LM_CASE_MODE"] = lmode
        lfallback = (data.get("lettermap_fallback") or str(_LM_FALLBACK)).strip().lower()
        if lfallback in ("text","skip"):
            globals()["_LM_FALLBACK"] = lfallback
        lm_in_orig = data.get("lettermap_in_original")
        if isinstance(lm_in_orig, bool):
            globals()["_LM_IN_ORIGINAL"] = lm_in_orig
        # continue_without_mapping
        cwm = data.get("lettermap_continue_without_mapping")
        if isinstance(cwm, bool):
            globals()["_LM_CONTINUE_WITHOUT_MAPPING"] = cwm
        scope = (data.get("lettermap_scope") or str(_LM_SCOPE)).strip().lower()
        if scope in ("emoji-only","all","none"):
            globals()["_LM_SCOPE"] = scope
        dbg = data.get("debug_dump_entities")
        if isinstance(dbg, bool):
            globals()["_DEBUG_DUMP_ENTITIES"] = dbg
    except Exception:
        # Still defaults
        return


# Konfigurationswerte (falls vorhanden) anwenden
_apply_config_overrides()


async def _with_retries(desc: str, func: Callable[[], Awaitable], attempts: int = _RETRIES) -> object | None:
    """Führt eine asynchrone Operation mit Retries/Backoff aus.
    - FloodWaitError.seconds wird respektiert (wenn vorhanden)
    - Sonst exponentieller Backoff mit Jitter
    """
    delay = _INITIAL_DELAY
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            return await func()
        except Exception as e:  # bewusst breit; wir wollen robust neu versuchen
            last_err = e
            seconds = getattr(e, "seconds", None)
            name = e.__class__.__name__
            if name == "FloodWaitError" and seconds is not None:
                wait_s = max(int(seconds), 1) + random.uniform(0, _JITTER)
                # Nur Hinweis; kein Spam
                print(f"Hinweis: FloodWait {wait_s:.1f}s bei {desc}, versuche erneut…")
                await asyncio.sleep(wait_s)
            else:
                await asyncio.sleep(delay + random.uniform(0, _JITTER))
                delay *= _BACKOFF
    if last_err is not None:
        print(f"Warnung: {desc} nach {attempts} Versuchen fehlgeschlagen ({type(last_err).__name__}): {last_err}")
    else:
        print(f"Warnung: {desc} nach {attempts} Versuchen ohne Ergebnis")
    return None


def parse_groups_file(path: Path) -> Tuple[str | None, List[Tuple[str, List[str]]]]:
    """
    Einfache Parser-Logik:
    - Erste nicht-leere Zeile ohne '#' gilt als Dokumenttitel
    - Zeilen mit '#' starten neue Gruppe (Titel = Inhalt nach '#')
    - Andere Zeilen werden als Links gesammelt
    - Leere Zeilen ignoriert
    """
    groups: List[Tuple[str, List[str]]] = []
    title: str | None = None
    buf: List[str] = []
    doc_title: str | None = None
    lines = [raw.strip() for raw in path.read_text(encoding="utf-8").splitlines()]
    i = 0
    # Dokumenttitel ermitteln
    while i < len(lines) and not lines[i]:
        i += 1
    if i < len(lines):
        doc_title = lines[i]
        # Titel ohne führendes '#'
        if doc_title.startswith('#'):
            doc_title = doc_title.lstrip('#').strip()
        i += 1
    # Rest parsen
    while i < len(lines):
        line = lines[i]; i += 1
        if not line:
            continue
        if line.startswith('#'):
            if title and buf:
                groups.append((title, buf))
            title = line[1:].strip()
            buf = []
        else:
            buf.append(line)
    if title and buf:
        groups.append((title, buf))
    return doc_title, groups


async def _fetch_translation(client: TelegramClient, entity, msg_id: int, twe: types.TextWithEntities, to_lang: str) -> types.TextWithEntities | None:
    # 1) Peer+ID bevorzugt
    res = await _with_retries(
        "TranslateTextRequest(peer,id)",
        lambda: client(functions.messages.TranslateTextRequest(peer=entity, id=[msg_id], to_lang=to_lang)),
    )
    if res is not None and getattr(res, "result", None):
        return res.result[0]
    # 2) Fallback: nur Text
    res2 = await _with_retries(
        "TranslateTextRequest(text)",
        lambda: client(functions.messages.TranslateTextRequest(text=[twe], to_lang=to_lang)),
    )
    if res2 is not None and getattr(res2, "result", None):
        return res2.result[0]
    return None


async def run_by_ids(
    links_file: Path,
    out_basename: str,
    output_dir: Path,
    translate: bool = False,
    translation_mode: str | None = None,
    target_lang: str = "de",
    include_images: bool = True,
    include_emojis: bool = True,
) -> Path:
    """
    Baut pro #Gruppe erst Originale und – falls translate=True – danach die Übersetzungen als eigenen Block ("Titel - DE").
    translation_mode wird aktuell nicht differenziert (inline/end/separate → inline-artiger Block je Gruppe).
    """
    # API-Creds aus vorhandenem Skript beziehen
    from tg_by_date_to_odt_modes import API_ID, API_HASH

    doc_title, groups = parse_groups_file(links_file)

    out_dir = output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"{out_basename}_{ts}.odt"

    records: List[RunsRecord] = []

    # Safe image output directory and counter
    safe_img_dir = Path("media/odt_safe"); safe_img_dir.mkdir(parents=True, exist_ok=True)
    img_idx = 1

    # Letter map laden (optional)
    letter_map_path = Path("data/letter_map.json")
    letter_to_doc: Dict[str, str] = {}
    mapped_doc_ids: set[str] = set()
    if letter_map_path.exists():
        try:
            data = json.loads(letter_map_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                # Unterstütze sowohl {document_id: str} als auch {document_ids: [str,...]}
                if isinstance(v, dict):
                    # Primäre doc_id für Rendern
                    primary = str(v.get("document_id", "")).strip()
                    docs = v.get("document_ids")
                    if isinstance(docs, list) and docs:
                        # erste als Primary
                        primary = str(docs[0] if docs[0] is not None else "").strip() or primary
                        # alle als gemappt zählen
                        for d in docs:
                            ds = str(d or "").strip()
                            if ds:
                                mapped_doc_ids.add(ds)
                    if primary:
                        letter_to_doc[str(k)] = primary
                        mapped_doc_ids.add(primary)
        except Exception:
            letter_to_doc = {}
            mapped_doc_ids = set()

    missing_letters: set[str] = set()

    def _normalize_for_lettermap(s: str) -> str:
        # Entferne Variation-Selector-16 (FE0F) und ähnliche
        s = s.replace("\uFE0F", "")
        # Keycap-Zahlen 0-9 → Plain Digit
        keycaps = {
            "0\uFE0F\u20E3": "0", "1\uFE0F\u20E3": "1", "2\uFE0F\u20E3": "2", "3\uFE0F\u20E3": "3",
            "4\uFE0F\u20E3": "4", "5\uFE0F\u20E3": "5", "6\uFE0F\u20E3": "6", "7\uFE0F\u20E3": "7",
            "8\uFE0F\u20E3": "8", "9\uFE0F\u20E3": "9",
        }
        for k, v in keycaps.items():
            s = s.replace(k, v)
        # Input-Symbole entfernen (🔠🔡🔤🔢)
        s = s.replace("🔠", "").replace("🔡", "").replace("🔤", "").replace("🔢", "")
        return s

    def _ensure_png_from_export(doc_id: str) -> bool:
        cache_png = Path("cache/emoji") / f"{doc_id}.png"
        if cache_png.exists():
            return True
        exp_dir = Path("custom_emoji_export")
        if not exp_dir.exists():
            return False
        # suche eine PNG, die die doc_id im Namen trägt
        for p in exp_dir.glob(f"*{doc_id}*.png"):
            try:
                cache_png.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, cache_png)
                return True
            except Exception:
                continue
        return False

    def _map_textrun_to_letter_runs(tr: TextRun) -> List[TextRun | EmojiRun | LineBreak]:
        s = tr.text or ""
        if not s:
            return [tr]
        s_norm = _normalize_for_lettermap(s)
        if _LM_CASE_MODE == "upper":
            s_use = s_norm.upper()
        elif _LM_CASE_MODE == "lower":
            s_use = s_norm.lower()
        else:
            s_use = s_norm
        out: List[TextRun | EmojiRun | LineBreak] = []
        for ch in s_use:
            if ch == "\n":
                out.append(LineBreak(kind="LineBreak"))
                continue
            if not ch.strip():
                out.append(TextRun(kind="TextRun", text=ch, href=tr.href, bold=tr.bold, italic=tr.italic, underline=tr.underline, strike=tr.strike, code=tr.code, spoiler=tr.spoiler))
                continue
            did = letter_to_doc.get(ch)
            if did:
                # PNG vorhanden?
                png_path = Path("cache/emoji") / f"{did}.png"
                if png_path.exists() or _ensure_png_from_export(did):
                    out.append(EmojiRun(kind="EmojiRun", document_id=did))
                else:
                    # PNG fehlt → fallback zum Buchstaben-Text
                    out.append(TextRun(kind="TextRun", text=ch, href=tr.href, bold=tr.bold, italic=tr.italic, underline=tr.underline, strike=tr.strike, code=tr.code, spoiler=tr.spoiler))
            else:
                # nicht gemappt → als Text belassen und für Report merken
                out.append(TextRun(kind="TextRun", text=ch, href=tr.href, bold=tr.bold, italic=tr.italic, underline=tr.underline, strike=tr.strike, code=tr.code, spoiler=tr.spoiler))
                missing_letters.add(ch)
        return out

    # Etwas robustere Client-Parameter (kleine Zeitouts; Auto-Reconnect aktiv)
    async with TelegramClient(
        "tg_session", API_ID, API_HASH,
        request_retries=_CLIENT_REQUEST_RETRIES,  # wir machen eigene Retries
        timeout=_CLIENT_TIMEOUT,
        auto_reconnect=_CLIENT_AUTO_RECONNECT,
    ) as client:
        # Falls Lettermapping irgendwo aktiv ist, versuche vorab alle letter_map doc_ids zu rendern
        if letter_to_doc and (_LM_IN_ORIGINAL or True):  # Prefetch ist günstig; hilft späteren Fallbacks
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
        # 1) Vorab: alle Nachrichten laden und verwendete Custom-Emoji-Dokumente sammeln
        from telethon.tl.types import MessageEntityCustomEmoji
        collected: List[Tuple[str, object, object]] = []  # (title, entity, msg)
        used_doc_ids: set[str] = set()
        debug_dir = Path("data/debug"); debug_dir.mkdir(parents=True, exist_ok=True)
        for title, links in groups:
            for link in links:
                try:
                    peer_raw, msg_id = parse_link(link)
                    entity = await _with_retries("get_entity", lambda: client.get_entity(peer_raw))
                    if not entity:
                        continue
                    await ensure_join_channel(client, entity)
                    msg = await _with_retries("get_messages", lambda: client.get_messages(entity, ids=msg_id))
                    if not msg:
                        continue
                    collected.append((title, entity, msg))
                    # doc_ids aus Entities sammeln und Alt-Texte laden (für Filter)
                    twe_tmp = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                    try:
                        await _with_retries("load_custom_emoji_alts(pre)", lambda: load_custom_emoji_alts(client, twe_tmp))
                    except Exception:
                        pass
                    doc_ids_this: list[str] = []
                    ents_dump = []
                    for e in (msg.entities or []):
                        et = type(e).__name__
                        rec = {"type": et, "offset": int(getattr(e, "offset", 0)), "length": int(getattr(e, "length", 0))}
                        if isinstance(e, MessageEntityCustomEmoji):
                            did = getattr(e, "document_id", None)
                            if did:
                                sd = str(did)
                                used_doc_ids.add(sd)
                                doc_ids_this.append(sd)
                                rec["document_id"] = sd
                        ents_dump.append(rec)
                    if _DEBUG_DUMP_ENTITIES:
                        out = {
                            "title": title,
                            "peer": str(peer_raw),
                            "message_id": int(getattr(msg, "id", 0)),
                            "text": msg.message or "",
                            "doc_ids": doc_ids_this,
                            "entities": ents_dump,
                            "has_media": bool(getattr(msg, "media", None)),
                            "has_document": bool(getattr(msg, "document", None)),
                        }
                        dp = debug_dir / f"entities_{str(peer_raw).replace('/', '_')}_{msg_id}.json"
                        try:
                            dp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                except Exception:
                    continue
        # 2) PNGs für ALLE verwendeten doc_ids (Entities) vorab erzeugen
        try:
            from .extract_ce import ensure_pngs_for_doc_ids
            ids_all = sorted(used_doc_ids)
            if ids_all:
                await ensure_pngs_for_doc_ids(client, [int(x) for x in ids_all])
        except Exception:
            pass
        # 3) Prüfen, ob alle verwendeten doc_ids in letter_map vorhanden sind
        def _invert_letter(letter_to_doc: Dict[str, str]) -> Dict[str, str]:
            inv: Dict[str, str] = {}
            for k, v in (letter_to_doc or {}).items():
                if v and v not in inv:
                    inv[v] = k
            return inv
        inv_map = _invert_letter(letter_to_doc)
        # Dialog für alle unbekannten doc_ids: prüfe gegen gesamte gemappte doc_id-Menge (document_id + document_ids)
        all_mapped = set(mapped_doc_ids)
        # Ignorieren-Liste berücksichtigen
        ignored: set[str] = set()
        try:
            ign_p = Path('data/lettermap_ignore.json')
            if ign_p.exists():
                import json as _json
                arr = _json.loads(ign_p.read_text(encoding='utf-8'))
                if isinstance(arr, list):
                    ignored = {str(x) for x in arr}
        except Exception:
            ignored = set()
        missing_docs = sorted([d for d in used_doc_ids if (d not in all_mapped and d not in ignored)])
        if missing_docs:
            # Report schreiben
            rep_path = Path("data/missing_lettermap_docs.json"); rep_path.parent.mkdir(parents=True, exist_ok=True)
            rep_path.write_text(json.dumps({"missing_doc_ids": missing_docs}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Hinweis: {len(missing_docs)} ungemappte Letter-Emojis (doc_id) → {rep_path}")
        # 4) Report für PNG-Lücken
        try:
            missing_pngs = [d for d in used_doc_ids if not (Path("cache/emoji") / f"{d}.png").exists()]
            if missing_pngs:
                rep_png = Path("data/missing_pngs.json"); rep_png.parent.mkdir(parents=True, exist_ok=True)
                rep_png.write_text(json.dumps({"missing_pngs": sorted(missing_pngs)}, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Hinweis: {len(missing_pngs)} PNGs fehlen → {rep_png}")
        except Exception:
            pass

            # Versuche vorab, fehlende Emoji-PNGs zu rendern (für bessere UI-Vorschau)
            try:
                from telethon.tl.types import MessageEntityCustomEmoji
                from pipeline.assets import ensure_custom_emoji_pngs as _ens
                # Fake-TWE mit allen fehlenden doc_ids
                ents = []
                off = 0
                for d in missing_docs:
                    try:
                        ents.append(MessageEntityCustomEmoji(offset=off, length=1, document_id=int(d)))
                        off += 1
                    except Exception:
                        pass
                twe_fake = types.TextWithEntities(text="X" * len(ents), entities=ents)
                await _with_retries("ensure_custom_emoji_pngs(preload)", lambda: _ens(client, twe_fake))
            except Exception:
                pass

            def _can_open_ui() -> bool:
                try:
                    import PySide6  # type: ignore
                except Exception:
                    return False
                import os, sys
                if os.environ.get("DISPLAY") or sys.platform.startswith("win") or sys.platform == "darwin":
                    return True
                return False

            if _LM_CONTINUE_WITHOUT_MAPPING:
                print("Konfiguration erlaubt: weiter ohne Mapping. Nicht gemappte Buchstaben bleiben als Text.")
            elif _LM_OPEN_UI_ON_MISSING and _can_open_ui():
                # UI starten und warten
                import os, subprocess, sys
                try:
                    proc = subprocess.Popen([sys.executable, "ui/app.py"])  # ohne warten
                except Exception:
                    print("Hinweis: UI konnte nicht gestartet werden. Fahre ohne interaktives Mapping fort.")
                else:
                    # Warte, bis alle doc_ids gemappt sind oder UI geschlossen wird
                    print("Warte auf Mapping der fehlenden Letter-Emojis… (UI offen lassen, Speichern genügt)")
                    while True:
                        try:
                            # neu laden (unterstütze document_ids)
                            if letter_map_path.exists():
                                data = json.loads(letter_map_path.read_text(encoding="utf-8"))
                                tmp: Dict[str, str] = {}
                                for k, v in (data or {}).items():
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
                        # Wenn UI beendet wurde, nicht blockieren
                        if proc.poll() is not None:
                            print("Hinweis: UI wurde geschlossen, es fehlen noch Zuordnungen. Fahre ohne Unterbruch fort.")
                            break
                        await asyncio.sleep(1.0)
                    print("Fahre fort…")
            else:
                print("Hinweis: Interaktives Mapping ist nicht verfügbar (PySide6/Display fehlt). Fahre ohne Unterbruch fort. Nicht gemappte Buchstaben bleiben als Text.")
        # 3) Nun ODT-Inhalte erzeugen aus bereits geladenen Nachrichten
        for title, entity, msg in collected:
            try:
                runs_list = []
                # Gruppenspezifisches Lettermapping: [LM] Marker im Titel aktiviert Mapping für diese Gruppe
                lm_for_group = _LM_IN_ORIGINAL or ("[LM]" in str(title).upper())
                # Bilder einbetten, falls vorhanden und erlaubt
                if include_images:
                    try:
                        from telethon.tl.types import MessageMediaPhoto, DocumentAttributeSticker
                        is_photo = isinstance(msg.media, MessageMediaPhoto)
                        is_image_doc = getattr(msg, "document", None) and (getattr(msg.document, "mime_type", "") or "").startswith("image/")
                        is_sticker = False
                        if getattr(msg, "document", None):
                            for attr in (msg.document.attributes or []):
                                if isinstance(attr, DocumentAttributeSticker):
                                    is_sticker = True; break
                        if is_photo or is_image_doc or is_sticker:
                            media_dir = Path("media"); media_dir.mkdir(exist_ok=True)
                            path = await _with_retries("download_media", lambda: msg.download_media(file=str(media_dir)))
                            if path and str(path).lower().endswith('.webp'):
                                # nach PNG konvertieren
                                try:
                                    from PIL import Image as PILImage
                                    p = Path(path)
                                    png_path = p.with_suffix('.png')
                                    with PILImage.open(p) as im:
                                        im.save(png_path, 'PNG')
                                    path = str(png_path)
                                except Exception:
                                    pass
                            if path:
                                # Always re-save as safe PNG with ASCII short name
                                try:
                                    from PIL import Image as PILImage, ImageOps
                                    safe_name = f"img_{img_idx:04d}.png"; img_idx += 1
                                    safe_path = safe_img_dir / safe_name
                                    with PILImage.open(Path(path)) as im:
                                        im = ImageOps.exif_transpose(im)
                                        if im.mode not in ('RGB','RGBA'):
                                            im = im.convert('RGB')
                                        im.save(safe_path, 'PNG')
                                    path = str(safe_path)
                                except Exception:
                                    pass
                                runs_list.append(ImageRun(kind="ImageRun", path=path, width_cm=10.0))
                    except Exception:
                        pass
                # Text
                if (msg.message or "").strip():
                    twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                    await _with_retries("load_custom_emoji_alts", lambda: load_custom_emoji_alts(client, twe))
                    # Optional: Emojis in cache/emoji rendern (nur statische WEBP/PNG)
                    if include_emojis:
                        try:
                            from .assets import ensure_custom_emoji_pngs  # might not exist yet
                            if 'ensure_custom_emoji_pngs' in dir(__import__('pipeline.assets', fromlist=['ensure_custom_emoji_pngs'])):
                                from pipeline.assets import ensure_custom_emoji_pngs as _ens
                                await _with_retries("ensure_custom_emoji_pngs", lambda: _ens(client, twe))
                        except Exception:
                            pass
                    runs = build_runs_from_twe(twe)
                    ce_map = get_custom_emoji_cache()
                    new_runs: List[TextRun | EmojiRun | LineBreak | ImageRun] = []
                    for rr in runs:
                        if isinstance(rr, EmojiRun):
                            if not include_emojis:
                                # Emojis als Text ausgeben
                                doc_id = rr.document_id
                                alt = ce_map.get(int(doc_id)) if doc_id.isdigit() else None
                                new_runs.append(TextRun(kind="TextRun", text=alt or f"[CE:{doc_id}]"))
                            else:
                                # Emojis als Bild, falls PNG vorhanden; sonst Alt-Text
                                png_path = Path("cache/emoji") / f"{rr.document_id}.png"
                                if png_path.exists() or _ensure_png_from_export(rr.document_id):
                                    new_runs.append(rr)
                                else:
                                    alt = ce_map.get(int(rr.document_id)) if rr.document_id.isdigit() else None
                                    new_runs.append(TextRun(kind="TextRun", text=alt or f"[CE:{rr.document_id}]"))
                        elif isinstance(rr, TextRun) and letter_to_doc and lm_for_group:
                            if _LM_SCOPE == "all":
                                mapped_runs = _map_textrun_to_letter_runs(rr)
                                new_runs.extend(mapped_runs)
                            elif _LM_SCOPE == "emoji-only":
                                # Nur Keycap-Emoji-Sequenzen (0-9 FE0F 20E3) zu Bildern wandeln
                                pattern = re.compile(r"([0-9])\uFE0F\u20E3")
                                pos = 0
                                txt = rr.text or ""
                                for m in pattern.finditer(txt):
                                    a, b = m.span(); digit = m.group(1)
                                    if a > pos:
                                        seg = txt[pos:a]
                                        if seg:
                                            new_runs.append(TextRun(kind="TextRun", text=seg, href=rr.href, bold=rr.bold, italic=rr.italic, underline=rr.underline, strike=rr.strike, code=rr.code, spoiler=rr.spoiler))
                                    did = letter_to_doc.get(digit)
                                    if did and ((Path("cache/emoji") / f"{did}.png").exists() or _ensure_png_from_export(did)):
                                        new_runs.append(EmojiRun(kind="EmojiRun", document_id=did))
                                    else:
                                        new_runs.append(TextRun(kind="TextRun", text=m.group(0), href=rr.href, bold=rr.bold, italic=rr.italic, underline=rr.underline, strike=rr.strike, code=rr.code, spoiler=rr.spoiler))
                                    pos = b
                                if pos < len(txt):
                                    tail = txt[pos:]
                                    if tail:
                                        new_runs.append(TextRun(kind="TextRun", text=tail, href=rr.href, bold=rr.bold, italic=rr.italic, underline=rr.underline, strike=rr.strike, code=rr.code, spoiler=rr.spoiler))
                            else:
                                new_runs.append(rr)
                        else:
                            new_runs.append(rr)
                    runs = new_runs
                    runs_list.extend(runs)
                if runs_list:
                    records.append(RunsRecord(chat=title, message_id=msg.id, runs=runs_list))
            except Exception:
                continue
        # 4) Übersetzungen (optional), nutzt bereits geladene Nachrichten
        if translate:
            lang_up = target_lang.upper()
            for title, entity, msg in collected:
                try:
                    if not msg or not ((msg.message or "").strip()):
                        continue
                    twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                    tr = await _fetch_translation(client, entity, msg.id, twe, target_lang)
                    if tr is None:
                        continue
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
                        elif isinstance(rr, TextRun) and letter_to_doc:
                            mapped_runs = _map_textrun_to_letter_runs(rr)
                            new_runs_tr.extend(mapped_runs)
                        else:
                            new_runs_tr.append(rr)
                    runs_tr = new_runs_tr
                    records.append(RunsRecord(chat=f"{title} - {lang_up}", message_id=msg.id, runs=runs_tr))
                except Exception:
                    continue

    # Minimal-Styles (können aus config gelesen werden, hier Default-Namen)
    styles = {
        "paragraph": {"base": "P.Base"},
        "text": {"base": "T.Base"},
        "graphic": {"inline_emoji": "G.InlineEmoji"},
    }

    write_odt_for_records(records, out_path, styles, doc_title=doc_title)

    # Report für fehlende Zuordnungen (nur schreiben, kein UI nachträglich öffnen)
    try:
        if missing_letters:
            rep_path = Path("data/missing_lettermap.json")
            rep_path.parent.mkdir(parents=True, exist_ok=True)
            rep_path.write_text(json.dumps({"missing": sorted(missing_letters)}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Hinweis: {len(missing_letters)} nicht zugeordnete Zeichen → {rep_path}")
    except Exception:
        pass

    return out_path
