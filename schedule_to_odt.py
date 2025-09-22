#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pipeline.runner_schedule import run_schedule


def main() -> None:
    parser = argparse.ArgumentParser(description="Erzeugt ein ODT aus einer Schedule-Datei (JSON/TXT)")
    parser.add_argument("schedule", nargs="?", type=Path, help="Pfad zur Schedule-Datei (.json oder .txt)")
    parser.add_argument("--out-dir", type=Path, default=Path("output"), help="Ausgabeordner für das ODT")
    parser.add_argument("--translate", action="store_true", help="Übersetzungen anhängen")
    parser.add_argument("--lang", default="de", help="Zielsprache für Übersetzungen")
    parser.add_argument("--mode", default="inline", help="Übersetzungsmodus (inline|end|separate)")
    parser.add_argument("--no-images", action="store_true", help="Bilder nicht einbetten")
    parser.add_argument("--no-emojis", action="store_true", help="Custom-Emojis nicht als Bilder einbetten")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Pfad zur config.yaml")

    args = parser.parse_args()

    schedule_path = args.schedule
    if schedule_path is None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            initial_dir = Path.cwd() / "input"
            if not initial_dir.exists():
                initial_dir = Path.cwd()
            chosen = filedialog.askopenfilename(
                title="Schedule-Datei wählen",
                initialdir=str(initial_dir),
                filetypes=[("Schedule", "*.json *.txt"), ("JSON", "*.json"), ("Text", "*.txt"), ("Alle Dateien", "*.*")],
            )
            if not chosen:
                print("Abbruch: keine Datei ausgewählt.")
                return
            schedule_path = Path(chosen)
        except Exception:
            raise SystemExit("Bitte eine Schedule-Datei angeben.")

    result = asyncio.run(
        run_schedule(
            schedule_path=schedule_path,
            out_basename=schedule_path.stem,
            output_dir=args.out_dir,
            translate=args.translate,
            translation_mode=args.mode,
            target_lang=args.lang,
            include_images=not args.no_images,
            include_emojis=not args.no_emojis,
            config_path=args.config,
        )
    )
    if isinstance(result, tuple):
        main_path, extra_path = result
        if extra_path:
            print(f"Fertig: {main_path} | {extra_path}")
        else:
            print(f"Fertig: {main_path}")
    else:
        print(f"Fertig: {result}")


if __name__ == "__main__":
    main()
