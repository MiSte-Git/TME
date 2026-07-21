#!/usr/bin/env python3
"""
Einmaliges Migrations-/Scan-Skript fuer cache/emoji/.

Hintergrund: Vor Commit 61163aa wurden animierte Custom-Emojis (.tgs/.webm)
nur mit einem einzelnen Frame (Frame 0 / Zeitpunkt 0) gerendert. Bei Emojis,
deren Inhalt erst spaeter in der Animation sichtbar wird (Fade-in,
verzoegerter In-Point, Trim-Path-Reveal - typisch bei "geschriebenen"
Buchstaben-Sets), fuehrte das zu leeren oder unvollstaendigen PNGs in
cache/emoji/. Der bestehende Cache-Check in pipeline/assets.py bzw.
pipeline/extract_ce.py prueft nur, ob die Datei existiert - solche
Alt-Eintraege werden daher nie automatisch erneuert.

Dieses Skript:
  1. Ueberspringt alle Eintraege, die bereits eine aktuelle Renderer-Version
     in _render_meta.json vermerkt haben (RENDERER_VERSION, siehe
     pipeline/frame_compositing.py) - die wurden schon mit dem neuen
     Multi-Frame-Compositing-Verfahren erzeugt.
  2. Prueft alle uebrigen (Alt-)Eintraege heuristisch auf "leer/fast leer"
     (frame_compositing.looks_blank) - das Hauptsymptom betroffener
     Frame-0-Renders.
  3. Fuer als leer erkannte Eintraege:
     - Falls --source-dir angegeben ist und dort eine passende Rohdatei
       <doc_id>.<ext> (oder <name>_<doc_id>.<ext>) liegt: direkt mit dem
       neuen Verfahren neu rendern und die Version vermerken.
     - Sonst (Normalfall fuer den echten Produktions-Cache - die
       urspruenglichen Telegram-Downloads werden nach der Konvertierung
       nicht aufbewahrt): PNG nach cache/emoji/_quarantine/ verschieben,
       damit der naechste echte Lauf, der dasselbe Custom-Emoji antrifft,
       es ganz normal per Cache-Miss neu von Telegram laedt und mit dem
       neuen Verfahren rendert.

Wichtige Einschraenkung: Die Bild-Heuristik erkennt zuverlaessig nur Faelle,
in denen bei Frame 0 (fast) nichts sichtbar war. Faelle, in denen Frame 0
schon ein vollstaendiges Bild zeigt und nur zusaetzliche Elemente fehlen
(Beispiel aus der Analyse: media/AnimatedSticker (174).tgs - Katzen-Motiv
bei Frame 0 bereits vollstaendig sichtbar, aber drei zusaetzliche
"Z"-Buchstaben-Layer fehlen), werden NICHT erkannt. Dafuer waere eine
Analyse der Quelldatei noetig (--source-dir), die fuer den echten
Produktions-Cache i.d.R. nicht mehr existiert.

Nutzung:
  python3 scripts/rescan_emoji_cache.py                          # Dry-Run, nur Report
  python3 scripts/rescan_emoji_cache.py --apply                  # Quarantaene/Rerender ausfuehren
  python3 scripts/rescan_emoji_cache.py --apply --source-dir DIR # + Rerender wo Quelle vorhanden
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.frame_compositing import (  # noqa: E402
    RENDERER_VERSION,
    get_render_version,
    looks_blank,
    mark_rendered,
    render_tgs_multiframe,
    render_webm_multiframe,
)

CACHE_DIR_DEFAULT = Path("cache/emoji")
QUARANTINE_SUBDIR = "_quarantine"


def find_source_file(source_dir: Path, doc_id: str) -> Path | None:
    """Sucht in source_dir nach einer Rohdatei fuer doc_id - entweder
    <doc_id>.<ext> oder <name>_<doc_id>.<ext> (Namensschema von
    extract_ce.py). Liefert None, wenn nichts Brauchbares gefunden wird."""
    if not source_dir.is_dir():
        return None
    candidates = sorted(source_dir.glob(f"{doc_id}.*")) + sorted(source_dir.glob(f"*_{doc_id}.*"))
    for c in candidates:
        if c.is_file() and c.stat().st_size > 0 and c.suffix.lower() in (".tgs", ".webm"):
            return c
    return None


def rerender_from_source(src: Path, out_png: Path) -> bool:
    suffix = src.suffix.lower()
    if suffix == ".tgs":
        return render_tgs_multiframe(src, out_png, size=512)
    if suffix == ".webm":
        return render_webm_multiframe(src, out_png)
    return False


def rescan(cache_dir: Path, source_dir: Path | None, apply: bool) -> dict:
    stats = {
        "scanned": 0,
        "already_current": 0,
        "left_untouched": 0,
        "flagged_blank": 0,
        "rerendered": 0,
        "quarantined": 0,
    }
    quarantine_dir = cache_dir / QUARANTINE_SUBDIR

    for png_path in sorted(cache_dir.glob("*.png")):
        doc_id = png_path.stem
        if not doc_id.isdigit():
            continue  # z.B. Fremd-/Reste-Dateien, keine doc_id-PNGs
        stats["scanned"] += 1

        version = get_render_version(cache_dir, doc_id)
        if version is not None and version >= RENDERER_VERSION:
            stats["already_current"] += 1
            continue

        if not looks_blank(png_path):
            stats["left_untouched"] += 1
            continue

        stats["flagged_blank"] += 1
        print(f"[blank] {png_path.name}")

        src = find_source_file(source_dir, doc_id) if source_dir else None
        if src:
            print(f"  Quelle gefunden: {src.name}")
            if apply:
                if rerender_from_source(src, png_path):
                    mark_rendered(cache_dir, doc_id)
                    stats["rerendered"] += 1
                    print("  -> neu gerendert (Multi-Frame-Compositing)")
                else:
                    print("  -> Rerender fehlgeschlagen, Datei unveraendert gelassen")
            else:
                stats["rerendered"] += 1
                print("  -> wuerde neu gerendert (Dry-Run)")
        else:
            if apply:
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(png_path), str(quarantine_dir / png_path.name))
                print(f"  -> in Quarantaene verschoben ({quarantine_dir}/)")
            else:
                print("  -> keine Quelle verfuegbar, wuerde in Quarantaene verschoben (Dry-Run)")
            stats["quarantined"] += 1

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cache-dir", default=str(CACHE_DIR_DEFAULT))
    ap.add_argument(
        "--source-dir",
        default=None,
        help="Optional: Verzeichnis mit Rohdateien <doc_id>.<ext>, aus denen "
        "als leer erkannte Eintraege direkt neu gerendert werden koennen, "
        "statt sie nur in Quarantaene zu verschieben.",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Aenderungen tatsaechlich durchfuehren (sonst Dry-Run/Report).",
    )
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    source_dir = Path(args.source_dir) if args.source_dir else None

    if not cache_dir.is_dir():
        print(f"Cache-Verzeichnis nicht gefunden: {cache_dir}", file=sys.stderr)
        return 1

    stats = rescan(cache_dir, source_dir, args.apply)

    print()
    print("=== Zusammenfassung ===")
    print(f"Gescannt:                  {stats['scanned']}")
    print(f"Bereits aktuell (v{RENDERER_VERSION}):     {stats['already_current']}")
    print(f"Unauffaellig, belassen:    {stats['left_untouched']}")
    print(f"Als leer erkannt:          {stats['flagged_blank']}")
    print(f"  davon neu gerendert:     {stats['rerendered']}")
    print(f"  davon in Quarantaene:    {stats['quarantined']}")
    if not args.apply:
        print()
        print("Dry-Run - keine Dateien veraendert. Mit --apply tatsaechlich ausfuehren.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
