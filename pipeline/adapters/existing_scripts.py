"""Thin wrappers kept for backwards compatibility.

Historically these helpers imported legacy scripts like tg_by_date_to_odt_modes.py.
They now delegate to :func:`pipeline.runner_schedule.run_schedule` so that
schedule_to_odt.py remains the single entry point.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from pipeline.runner_schedule import run_schedule


def run_by_date(
    schedule_file: Path,
    out_odt_basename: Optional[str] = None,
    output_dir: Optional[Path] = None,
    translate: Optional[bool] = None,
    translation_mode: Optional[str] = None,
    target_lang: Optional[str] = None,
    local_tz: Optional[str] = None,
    translation_provider: Optional[str] = None,
) -> None:
    """Run the schedule flow using :func:`run_schedule`.

    Parameters mirror the previous implementation; unsupported values are ignored.
    """

    if out_odt_basename is None:
        out_odt_basename = schedule_file.stem
    if output_dir is None:
        output_dir = Path("output")

    kwargs = {
        "schedule_path": schedule_file,
        "out_basename": out_odt_basename,
        "output_dir": output_dir,
    }
    if translate is not None:
        kwargs["translate"] = bool(translate)
    if translation_mode is not None:
        kwargs["translation_mode"] = str(translation_mode)
    if target_lang is not None:
        kwargs["target_lang"] = str(target_lang)
    if local_tz is not None:
        kwargs["local_tz_override"] = str(local_tz)
    if translation_provider is not None:
        kwargs["translation_provider"] = str(translation_provider)

    asyncio.run(run_schedule(**kwargs))


def run_grouped_links(*_args, **_kwargs) -> None:
    raise RuntimeError(
        "run_grouped_links wurde entfernt. Bitte konvertiere die Links als Schedule-Datei "
        "(z.B. via convert_schedule.py) und nutze schedule_to_odt.py."
    )
