"""
assets: Emoji-Assets cachen und Metadaten pflegen
Ziel: cache/emoji/<doc_id>.png und data/assets.json
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from typing import Dict, Any

from telethon import functions, types
from telethon.tl.types import DocumentAttributeCustomEmoji, DocumentAttributeSticker, MessageEntityCustomEmoji

# In-Memory Cache für Custom-Emoji-Alttexte
CUSTOM_EMOJI_CACHE: Dict[int, str] = {}

ASSETS_FILE_DEFAULT = Path("data/assets.json")
CACHE_DIR_DEFAULT = Path("cache/emoji")

@dataclass
class AssetMeta:
    file: str
    w: int
    h: int
    mime: str
    set_id: str | None = None
    set_title: str | None = None
    orig_name: str | None = None
    letter_hint: str | None = None


def load_assets(path: Path = ASSETS_FILE_DEFAULT) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # Datei ist korrupt → Sicherung anlegen und neu beginnen
            try:
                bak = path.with_suffix(path.suffix + ".bak")
                path.replace(bak)
            except Exception:
                pass
            return {}
    return {}


def save_assets(data: Dict[str, Any], path: Path = ASSETS_FILE_DEFAULT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


async def load_custom_emoji_alts(client, twe: types.TextWithEntities) -> None:
    """
    Füllt CUSTOM_EMOJI_CACHE: document_id → alt_text (sofern verfügbar).
    Alt-Text wird aus DocumentAttributeCustomEmoji/Sticker.alt geholt.
    """
    ids: list[int] = []
    for e in (twe.entities or []):
        if isinstance(e, MessageEntityCustomEmoji):
            doc_id = getattr(e, "document_id", None)
            if doc_id and doc_id not in CUSTOM_EMOJI_CACHE:
                ids.append(doc_id)
    if not ids:
        return
    docs = await client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=ids))
    for d in docs:
        alt = None
        for attr in (d.attributes or []):
            if isinstance(attr, DocumentAttributeCustomEmoji):
                alt = attr.alt or alt
            if isinstance(attr, DocumentAttributeSticker):
                alt = alt or getattr(attr, "alt", None)
        CUSTOM_EMOJI_CACHE[d.id] = alt or "�"


def get_custom_emoji_cache() -> Dict[int, str]:
    return CUSTOM_EMOJI_CACHE


async def ensure_custom_emoji_pngs(client, twe: types.TextWithEntities, cache_dir: Path = CACHE_DIR_DEFAULT) -> None:
    """
    Lädt die Dokumente der im TWE vorkommenden Custom-Emojis und speichert (soweit möglich)
    eine PNG-Datei unter cache/emoji/<doc_id>.png.
    Unterstützte Konvertierungen:
      - image/webp, image/png → PNG
      - video/webm, image/webm → PNG (mehrere über die Laufzeit verteilte Frames
        alpha-compositet, benötigt ffmpeg; siehe pipeline/frame_compositing.py)
      - application/x-tgsticker (.tgs) → PNG (mehrere über die Composition-Länge
        verteilte Frames alpha-compositet, benötigt lottie_convert.py)
    Falls Tools fehlen, bleibt das Emoji als Text/Alt-Text.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    ids: list[int] = []
    for e in (twe.entities or []):
        if isinstance(e, MessageEntityCustomEmoji):
            doc_id = getattr(e, "document_id", None)
            if doc_id:
                out_png = cache_dir / f"{doc_id}.png"
                if not out_png.exists():
                    ids.append(doc_id)
    if not ids:
        return
    docs = await client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=ids))
    for d in docs:
        out_png = cache_dir / f"{d.id}.png"
        if out_png.exists():
            continue
        # versuchen, die Datei herunterzuladen
        tmp_path = cache_dir / f"{d.id}.bin"
        try:
            from telethon import utils as tutils
            # download_file unterstützt das Document-Objekt direkt
            data = await client.download_file(d)
            tmp_path.write_bytes(data)
            # Heuristik über mime
            mime = getattr(d, 'mime_type', '') or ''
            lower = tmp_path.suffix.lower()
            if mime.startswith('image/webp') or lower == '.webp':
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(tmp_path) as im:
                        im.save(out_png, 'PNG')
                except Exception:
                    pass
            elif mime.startswith('image/png') or lower == '.png':
                out_png.write_bytes(tmp_path.read_bytes())
            elif 'webm' in mime or lower == '.webm':
                from .frame_compositing import render_webm_multiframe
                render_webm_multiframe(tmp_path, out_png)
            elif mime == 'application/x-tgsticker' or lower == '.tgs':
                from .frame_compositing import render_tgs_multiframe
                render_tgs_multiframe(tmp_path, out_png)
            # andere Formate bleiben unkonvertiert
        except Exception:
            pass
        finally:
            try:
                if tmp_path.exists(): tmp_path.unlink()
            except Exception:
                pass
