from __future__ import annotations
from pathlib import Path
import csv, json, re
from typing import Dict, Any

from .assets import load_assets

LETTERMAP_FILE = Path("data/letter_map.json")
ASSETS_FILE = Path("data/assets.json")
EXPORT_DIR = Path("custom_emoji_export")


DOC_ID_RE = re.compile(r"(\d{16,20})")


def suggest_lettermap_csv(out_csv: Path = Path("data/lettermap_suggest.csv")) -> Path:
    assets = load_assets(ASSETS_FILE)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    # Sammle doc_ids aus assets.json
    rows: Dict[str, Dict[str, Any]] = {}
    for doc_id, rec in assets.items():
        file = rec.get("file") or ""
        alt = rec.get("alt") or ""
        hint = rec.get("letter_hint") or ""
        # wenn exportierte Datei existiert, diese anzeigen (besser zur Sichtprüfung)
        export_png = EXPORT_DIR / f"{doc_id}.png"
        if export_png.exists():
            file = str(export_png)
        rows[str(doc_id)] = {"file": file, "alt": alt, "hint": hint}

    # Zusätzliche doc_ids direkt aus custom_emoji_export/ erfassen (AnimatedSticker_*.png, sticker_*.png, <doc_id>.png)
    if EXPORT_DIR.exists():
        for p in EXPORT_DIR.glob("*.png"):
            m = DOC_ID_RE.search(p.stem)
            if not m:
                continue
            doc_id = m.group(1)
            if doc_id not in rows:
                rows[doc_id] = {"file": str(p), "alt": "", "hint": ""}

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc_id","file","alt","letter_hint","suggest"])  # suggest kann manuell befüllt werden
        for doc_id in sorted(rows.keys()):
            rec = rows[doc_id]
            w.writerow([doc_id, rec.get("file",""), rec.get("alt",""), rec.get("hint",""), rec.get("hint","")])
    return out_csv


def build_lettermap_from_csv(in_csv: Path = Path("data/lettermap_suggest.csv"), out_json: Path = LETTERMAP_FILE) -> Path:
    mapping: Dict[str, Any] = {}
    if in_csv.exists():
        with in_csv.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                doc_id = (row.get("doc_id") or "").strip()
                suggest = (row.get("suggest") or "").strip()
                if not doc_id or not suggest:
                    continue
                # einfache Zuordnung: Großbuchstaben aus Vorschlag
                mapping[suggest] = {"document_id": doc_id}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out_json
