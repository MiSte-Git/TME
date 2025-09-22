#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from schedule_json import (
    load_schedule_document,
    load_legacy_schedule,
    save_schedule_document,
)


def _detect_loader(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".json":
        return load_schedule_document
    if suffix == ".txt":
        return load_legacy_schedule
    raise ValueError(f"Unbekanntes Format '{path.suffix}' (erwartet .txt oder .json)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Konvertiert Legacy-Schedules nach JSON.")
    parser.add_argument("source", type=Path, help="Eingabedatei (.txt oder .json)")
    parser.add_argument("destination", type=Path, nargs="?", help="Zieldatei (.json)")
    args = parser.parse_args()

    src = args.source.resolve()
    if not src.exists():
        raise SystemExit(f"Quelle nicht gefunden: {src}")

    loader = _detect_loader(src)
    schedule = loader(src)

    if args.destination:
        dest = args.destination.resolve()
    else:
        dest = src.with_suffix(".json")
    if dest.suffix.lower() != ".json":
        dest = dest.with_suffix(".json")

    dest.parent.mkdir(parents=True, exist_ok=True)
    save_schedule_document(schedule, dest)
    print(f"{len(schedule.sections)} Abschnitte nach {dest} geschrieben.")


if __name__ == "__main__":
    main()
