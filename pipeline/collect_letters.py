from __future__ import annotations
from pathlib import Path
from typing import Set, Dict, Any, Tuple
import json

from telethon import TelegramClient, functions, types
from telethon.tl.types import MessageEntityCustomEmoji

from .fetch import parse_link, ensure_join_channel
from .assets import load_custom_emoji_alts, ensure_custom_emoji_pngs, get_custom_emoji_cache, load_assets, save_assets, CACHE_DIR_DEFAULT


def _links_from_file(p: Path) -> list[str]:
    out: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        out.append(s)
    return out


def _heuristic_letter_hint(alt: str | None) -> str | None:
    if not alt:
        return None
    alt = alt.strip()
    if len(alt) == 1 and (alt.isalnum() or alt in "!?:;,.+-*/="):
        return alt
    return None


async def collect_letters_from_links(
    api_id: int,
    api_hash: str,
    links_file: Path,
    export_dir: Path = Path("custom_emoji_export"),
    assets_file: Path = Path("data/assets.json"),
) -> Tuple[int, int]:
    """
    Lädt alle Nachrichten aus links_file, extrahiert Custom-Emoji-Dokumente, rendert PNGs
    in cache/emoji und kopiert sie nach export_dir. assets.json wird mit Metadaten (letter_hint, file) aktualisiert.
    Rückgabe: (anzahl_docs, neu_gerenderte)
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    links = _links_from_file(links_file)

    seen_ids: Set[int] = set()
    rendered_new = 0

    assets = load_assets(assets_file)

    async with TelegramClient("tg_session", api_id, api_hash) as client:
        for link in links:
            try:
                peer_raw, msg_id = parse_link(link)
                entity = await client.get_entity(peer_raw)
                await ensure_join_channel(client, entity)
                msg = await client.get_messages(entity, ids=msg_id)
                if not msg:
                    continue
                twe = types.TextWithEntities(text=msg.message or "", entities=msg.entities or [])
                # Alt-Texte laden
                await load_custom_emoji_alts(client, twe)
                # PNGs robust rendern für ALLE doc_ids (Export-Flow)
                ids: set[int] = set()
                for e in (twe.entities or []):
                    if isinstance(e, MessageEntityCustomEmoji):
                        did = getattr(e, "document_id", None)
                        if did: ids.add(int(did))
                if ids:
                    from .extract_ce import ensure_pngs_for_doc_ids
                    await ensure_pngs_for_doc_ids(client, ids, CACHE_DIR_DEFAULT)
                # IDs einsammeln
                for e in (twe.entities or []):
                    if isinstance(e, MessageEntityCustomEmoji):
                        doc_id = getattr(e, "document_id", None)
                        if doc_id is None:
                            continue
                        if doc_id in seen_ids:
                            continue
                        seen_ids.add(doc_id)
                        png = (CACHE_DIR_DEFAULT / f"{doc_id}.png")
                        if png.exists():
                            # nach export kopieren
                            dst = export_dir / f"{doc_id}.png"
                            if not dst.exists():
                                try:
                                    dst.write_bytes(png.read_bytes())
                                    rendered_new += 1
                                except Exception:
                                    pass
                        # assets.json aktualisieren
                        rec = assets.get(str(doc_id), {})
                        rec["file"] = str(png)
                        rec.setdefault("mime", "image/png")
                        # letter_hint aus alt
                        alt = get_custom_emoji_cache().get(doc_id)
                        hint = _heuristic_letter_hint(alt)
                        if hint:
                            rec["letter_hint"] = hint
                        assets[str(doc_id)] = rec
            except Exception:
                continue

    save_assets(assets, assets_file)
    return (len(seen_ids), rendered_new)
