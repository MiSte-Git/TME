"""Zentrales Logging-Setup fuer TME.

Richtet einen Logger ein, der gleichzeitig nach data/tme.log (Datei) und
nach stdout (Konsole) schreibt. `get_logger()` ist idempotent - Handler
werden nur beim ersten Aufruf angehaengt, spaetere Aufrufe liefern denselben
konfigurierten Logger zurueck.
"""
from __future__ import annotations

import logging
from pathlib import Path

_LOG_DIR = Path("data")
_LOG_FILE = _LOG_DIR / "tme.log"
_ROOT_LOGGER_NAME = "tme"
_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Konfiguriert (einmalig) den zentralen "tme"-Logger mit Datei- und
    Konsolen-Handler und gibt ihn zurueck."""
    global _configured
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _configured = True
    return logger


def get_logger(module_name: str | None = None) -> logging.Logger:
    """Liefert einen Logger unterhalb des zentralen "tme"-Namespace.

    Beispiel: get_logger(__name__) -> Logger "tme.pipeline.runner_schedule".
    """
    setup_logging()
    if module_name:
        return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{module_name}")
    return logging.getLogger(_ROOT_LOGGER_NAME)
