"""
Wrapper, die die bestehenden Skripte als Module importieren und ihre Konfiguration
zur Laufzeit setzen, ohne die Dateien zu ändern.

- by_date_adapter.run(...): nutzt tg_by_date_to_odt_modes.py
- grouped_links_adapter.run(...): nutzt (falls vorhanden) TelegramNachrichtenKopieren.py
"""
from __future__ import annotations
import asyncio
import importlib
from pathlib import Path
from typing import Optional


def run_by_date(
    schedule_file: Path,
    out_odt_basename: Optional[str] = None,
    output_dir: Optional[Path] = None,
    translate: Optional[bool] = None,
    translation_mode: Optional[str] = None,
    target_lang: Optional[str] = None,
    local_tz: Optional[str] = None,
) -> None:
    mod = importlib.import_module("tg_by_date_to_odt_modes")
    # Konfiguration überschreiben, wenn übergeben
    if schedule_file:
        mod.SCHEDULE_FILE = str(schedule_file)
    if out_odt_basename:
        mod.OUT_ODT = out_odt_basename if out_odt_basename.endswith(".odt") else f"{out_odt_basename}.odt"
    if output_dir is not None:
        mod.OUTPUT_DIR = str(output_dir)
    if translate is not None:
        mod.TRANSLATE = bool(translate)
    if translation_mode is not None:
        mod.TRANSLATION_MODE = str(translation_mode)
    if target_lang is not None:
        mod.TARGET_LANG = str(target_lang)
    if local_tz is not None:
        mod.LOCAL_TZ = str(local_tz)
    # Ausführen
    asyncio.run(mod.main())


def run_grouped_links(
    links_file: Path,
    out_odt_basename: Optional[str] = None,
    media_dir: Optional[Path] = None,
    target_lang: Optional[str] = None,
) -> None:
    try:
        mod = importlib.import_module("TelegramNachrichtenKopieren")
    except ModuleNotFoundError:
        # Falls nur in _archive vorhanden ist, versuchen zu importieren
        import sys
        p = Path("_archive/TelegramNachrichtenKopieren.py").resolve()
        if p.exists():
            sys.path.insert(0, str(p.parent))
            mod = importlib.import_module("TelegramNachrichtenKopieren")
        else:
            raise
    if links_file:
        mod.LINKS_FILE = str(links_file)
    if out_odt_basename:
        mod.OUT_ODT = out_odt_basename if out_odt_basename.endswith(".odt") else f"{out_odt_basename}.odt"
    if media_dir is not None:
        mod.MEDIA_DIR = str(media_dir)
    if target_lang is not None:
        mod.TARGET_LANG = str(target_lang)
    asyncio.run(mod.main())
