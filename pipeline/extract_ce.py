from __future__ import annotations
from pathlib import Path
import shutil, subprocess, gzip, sys
from typing import Iterable

from telethon import functions


async def ensure_pngs_for_doc_ids(client, doc_ids: Iterable[int], cache_dir: Path = Path("cache/emoji")) -> int:
    """
    Erzeugt PNGs unter cache/emoji/<doc_id>.png für alle angegebenen Custom-Emoji-Dokumente.
    - Lädt die Dokumente via GetCustomEmojiDocumentsRequest
    - Speichert Rohdatei in tmp, konvertiert nach PNG (webp/webm/tgs) und legt PNG im cache ab
    - Gibt die Anzahl erfolgreich erzeugter PNGs zurück
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    ids = [int(x) for x in doc_ids if x]
    if not ids:
        return 0
    docs = await client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=ids))
    ok = 0
    for d in docs:
        out_png = cache_dir / f"{d.id}.png"
        if out_png.exists():
            ok += 1
            continue
        # Dateiname/Endung bestimmen
        from telethon.tl.types import DocumentAttributeFilename
        attr_name = None
        for attr in getattr(d, 'attributes', []) or []:
            if isinstance(attr, DocumentAttributeFilename):
                attr_name = attr.file_name
                break
        mime = (getattr(d, 'mime_type', '') or '').lower()
        # Default-Extension nach MIME
        if mime.startswith('video/webm'):
            ext_default = '.webm'
        elif mime.endswith('webp'):
            ext_default = '.webp'
        elif mime in ('application/x-tgsticker','application/json+tgs'):
            ext_default = '.tgs'
        else:
            ext_default = '.bin'
        from pathlib import Path
        if attr_name:
            stem = Path(attr_name).stem
            ext = Path(attr_name).suffix or ext_default
        else:
            stem, ext = 'sticker', ext_default
        raw_path = cache_dir / f"{stem}_{d.id}{ext}"
        # Download über download_media (wie Originalskript)
        try:
            await client.download_media(d, file=str(raw_path))
            tmp = raw_path
            # WEBM -> PNG (ffmpeg)
            if (mime.startswith('video/webm') or tmp.suffix.lower()=='.webm') and shutil.which('ffmpeg'):
                png_path = tmp.with_suffix('.png')
                try:
                    subprocess.run([
                        'ffmpeg','-y','-i',str(tmp),'-frames:v','1','-pix_fmt','rgba',str(png_path)
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if png_path.exists():
                        out_png.write_bytes(png_path.read_bytes()); ok += 1
                        try: png_path.unlink()
                        except: pass
                        continue
                except Exception:
                    pass
            # WEBP/PNG direkt
            lower = tmp.suffix.lower()
            if mime.endswith('webp') or lower == '.webp':
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(tmp) as im:
                        im.save(out_png, 'PNG'); ok += 1
                        continue
                except Exception:
                    pass
            if mime.endswith('png') or lower == '.png':
                out_png.write_bytes(tmp.read_bytes()); ok += 1
                continue
            # TGS (gzippte Lottie) → PNG Frame 0
            if mime in ('application/x-tgsticker','application/json+tgs') or lower == '.tgs':
                json_path = tmp.with_suffix('.json')
                try:
                    with gzip.open(tmp, 'rb') as gz, open(json_path, 'wb') as jf:
                        jf.write(gz.read())
                    ran = False
                    cli = shutil.which('lottie_convert.py')
                    if cli:
                        try:
                            subprocess.run([cli, str(json_path), str(out_png), '--frame','0','--width','512','--height','512'],
                                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            ran = out_png.exists()
                        except Exception:
                            ran = False
                    if not ran:
                        try:
                            from lottie.scripts.lottie_convert import main as lottie_main
                            argv_old = sys.argv
                            sys.argv = ['lottie_convert.py', str(json_path), str(out_png), '--frame','0','--width','512','--height','512']
                            try:
                                lottie_main()
                                ran = out_png.exists()
                            finally:
                                sys.argv = argv_old
                        except Exception:
                            ran = False
                    if ran:
                        ok += 1
                        try: json_path.unlink()
                        except Exception: pass
                        continue
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try:
                if raw_path.exists(): raw_path.unlink()
            except Exception:
                pass
    return ok
