"""
lettermap: Mapping von Zeichen → document_id und inverse Map
Ziel: data/letter_map.json
"""
from __future__ import annotations
from pathlib import Path
import json
from typing import Dict, Any

LETTERMAP_FILE_DEFAULT = Path("data/letter_map.json")

def load_lettermap(path: Path = LETTERMAP_FILE_DEFAULT) -> Dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}

def save_lettermap(data: Dict[str, Any], path: Path = LETTERMAP_FILE_DEFAULT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
