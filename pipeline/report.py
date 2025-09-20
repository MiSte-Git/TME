"""
report: Statistiken, Lücken und Fehler sammeln → out/report.json
"""
from __future__ import annotations
from pathlib import Path
import json
from typing import Dict, Any

REPORT_FILE_DEFAULT = Path("out/report.json")

def write_report(data: Dict[str, Any], path: Path = REPORT_FILE_DEFAULT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
