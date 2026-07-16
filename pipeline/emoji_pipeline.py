#!/usr/bin/env python3
"""
Emoji-ODT Pipeline Orchestrator (Stub)
- Lädt config.yaml
- Kann entweder die modularen Stubs aus pipeline/ nutzen
- Oder die vorhandenen Skripte über pipeline.adapters.existing_scripts aufrufen

Beispiel:
  python3 emoji_pipeline.py by-date --schedule input/links.txt --mode inline --translate 1 --lang de
  python3 emoji_pipeline.py grouped-links --links input/links_groups.txt --lang de
"""
from __future__ import annotations
import argparse
from pathlib import Path
from typing import Tuple
from credentials import get_telegram_credentials
from pipeline.adapters.existing_scripts import run_by_date

# Optional: YAML-Konfiguration (falls PyYAML nicht installiert ist, wird ohne Config gearbeitet)
try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap.add_argument("--config", default="config.yaml", help="Pfad zu config.yaml")

    s1 = sub.add_parser("by-date", help="Schedule-zu-ODT Pipeline ausführen")
    s1.add_argument("--schedule", required=True, type=Path)
    s1.add_argument("--mode", default=None, help="inline|end|separate")
    s1.add_argument("--translate", type=int, choices=[0,1], default=None)
    s1.add_argument("--lang", default=None)
    s1.add_argument("--provider", default=None, choices=["telegram", "deepl", "google", "chatgpt"],
                     help="Übersetzungs-Provider; Default aus config.yaml (translation.provider)")

    s2 = sub.add_parser("grouped-links", help="TelegramNachrichtenKopieren.py ausführen")
    s2.add_argument("--links", required=True, type=Path)
    s2.add_argument("--lang", default=None)

    s3 = sub.add_parser("by-ids", help="Links-TXT nach #Gruppen → ODT (Original + optional Übersetzung)")
    s3.add_argument("--links", required=True, type=Path)
    s3.add_argument("--translate", type=int, choices=[0,1], default=0)
    s3.add_argument("--mode", default="inline")
    s3.add_argument("--lang", default="de")
    s3.add_argument("--no-images", action="store_true", help="Bilder nicht einbetten")
    s3.add_argument("--no-emojis", action="store_true", help="Custom-Emojis nicht als Bilder einbetten; als Text/Platzhalter ausgeben")
    s3.add_argument("--provider", default=None, choices=["telegram", "deepl", "google", "chatgpt"],
                     help="Übersetzungs-Provider; Default aus config.yaml (translation.provider)")

    s4 = sub.add_parser("collect-letters", help="Custom-Emoji PNGs sammeln und nach custom_emoji_export/ kopieren; assets.json aktualisieren")
    s4.add_argument("--links", required=True, type=Path)
    s4.add_argument("--export-dir", default=Path("custom_emoji_export"), type=Path)

    s5 = sub.add_parser("lettermap-suggest", help="CSV-Vorschlag data/lettermap_suggest.csv aus assets.json erzeugen")
    s5.add_argument("--out", default=Path("data/lettermap_suggest.csv"), type=Path)

    s6 = sub.add_parser("lettermap-build", help="letter_map.json aus bearbeiteter CSV erzeugen")
    s6.add_argument("--csv", default=Path("data/lettermap_suggest.csv"), type=Path)
    s6.add_argument("--out", default=Path("data/letter_map.json"), type=Path)

    s7 = sub.add_parser("extract-plain", help="Plaintext (Emoji→Buchstabe) aus Links erzeugen → data/plain/")
    s7.add_argument("--links", required=True, type=Path)

    s8 = sub.add_parser("recompose", help="Aus Plain/Translated und letter_map.json ODT mit Buchstaben-Emojis erzeugen")
    s8.add_argument("--links", required=True, type=Path)
    s8.add_argument("--lang", default="de")

    args = ap.parse_args()
    cfg_path = Path(args.config)
    cfg = {}
    if cfg_path.exists() and yaml is not None:
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cfg = loaded

    out_dir = Path(cfg.get("out_original_dir", "out/original")).parent  # nutze out/

    if args.cmd == "by-date":
        run_by_date(
            schedule_file=args.schedule,
            out_odt_basename=args.schedule.stem,
            output_dir=Path(cfg.get("out_original_dir", "out/original")).parent,  # wir nutzen output/ des Skripts ggf.
            translate=(bool(args.translate) if args.translate is not None else None),
            translation_mode=args.mode,
            target_lang=args.lang,
            local_tz=cfg.get("local_tz"),
            translation_provider=args.provider,
        )
    elif args.cmd == "grouped-links":
        raise SystemExit(
            "Der Modus 'grouped-links' wird nicht mehr unterstützt. Bitte wandle die Daten in eine "
            "Schedule-Datei um (siehe convert_schedule.py) und nutze schedule_to_odt.py."
        )
    elif args.cmd == "by-ids":
        from pipeline.runner_by_ids import run_by_ids
        out = run_by_ids(
            links_file=args.links,
            out_basename=args.links.stem,
            output_dir=Path("output"),
            translate=bool(args.translate),
            translation_mode=args.mode,
            target_lang=args.lang,
            include_images=(not args.no_images),
            include_emojis=(not args.no_emojis),
            translation_provider=args.provider,
        )
        # run_by_ids ist async → ausführen
        import asyncio
        asyncio.run(out)
    elif args.cmd == "collect-letters":
        from pipeline.collect_letters import collect_letters_from_links
        import asyncio
        api_id, api_hash = _require_api_credentials()
        n_all, n_new = asyncio.run(collect_letters_from_links(api_id, api_hash, args.links, args.export_dir))
        print(f"Custom-Emoji gesammelt: {n_all} gefunden, {n_new} neu exportiert nach {args.export_dir}")
    elif args.cmd == "lettermap-suggest":
        from pipeline.lettermap_tools import suggest_lettermap_csv
        p = suggest_lettermap_csv(args.out)
        print(f"CSV-Vorschlag geschrieben: {p}")
    elif args.cmd == "lettermap-build":
        from pipeline.lettermap_tools import build_lettermap_from_csv
        p = build_lettermap_from_csv(args.csv, args.out)
        print(f"letter_map.json geschrieben: {p}")
    elif args.cmd == "extract-plain":
        from pipeline.plaintext import extract_plain_from_links
        import asyncio
        api_id, api_hash = _require_api_credentials()
        n = asyncio.run(extract_plain_from_links(api_id, api_hash, args.links))
        print(f"Plaintext erzeugt: {n} Dateien unter data/plain/")
    elif args.cmd == "recompose":
        from pipeline.recompose import recompose_to_odt
        out = recompose_to_odt(args.links, args.lang)
        print(f"Recompose-ODT: {out}")

if __name__ == "__main__":
    main()
def _require_api_credentials() -> Tuple[int, str]:
    api_id, api_hash, phone = get_telegram_credentials()
    return api_id, api_hash
