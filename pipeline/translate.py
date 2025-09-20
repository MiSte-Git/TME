"""
translate: Plaintext extrahieren und Zieltexte bereitstellen
Ziel: data/plain/*.txt und data/translated/*.txt
"""
from __future__ import annotations
from pathlib import Path
from typing import Iterable


def save_plaintext(dst_dir: Path, chat: str, msg_id: int, text: str) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    p = dst_dir / f"{chat}_{msg_id}.txt"
    p.write_text(text, encoding="utf-8")
    return p


def read_translated(src_dir: Path, chat: str, msg_id: int) -> str | None:
    p = src_dir / f"{chat}_{msg_id}.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None
