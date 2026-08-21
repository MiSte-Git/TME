"""
lettermap: Mapping von Zeichen → document_id und inverse Map
Ziel: siehe lettermap_file_path().
"""
from __future__ import annotations
from pathlib import Path
import json
import shutil
from typing import Dict, Any

# Alter, CWD-relativer Speicherort (Verhalten vor dem Fix für TME-Backlog.md
# Punkt 2). Bleibt als Fallback (kein QStandardPaths verfügbar/nutzbar, z.B.
# reine CLI-Nutzung ohne Qt-Anwendung) und als Migrationsquelle bestehen.
LEGACY_LETTERMAP_FILE = Path("data/letter_map.json")


def lettermap_file_path() -> Path:
    """Liefert den Speicherort für letter_map.json.

    Vorher fest Path("data/letter_map.json") - CWD-relativ. data/ ist
    gitignored (reine Nutzerdaten, kein Repo-Template) und
    scripts/windows-install.ps1 kopiert die Datei beim Install nicht mit
    (anders als config.yaml). Nach jeder Windows-Neuinstallation war die
    Zuordnung dadurch weg, obwohl der Dev-Checkout sie noch enthielt (siehe
    TME-Backlog.md, Punkt 2 - bestätigt u.a. über data/missing_lettermap_docs.json
    im Install-Verzeichnis).

    Jetzt: QStandardPaths.AppConfigLocation - derselbe installations-
    unabhängige, persistente Ort wie ui_theme.json/ui_lang.json (siehe
    ui/app.py::_config_dir()), übersteht Neuinstallationen/Updates. Das setzt
    voraus, dass QApplication mit setOrganizationName()/setApplicationName()
    existiert (siehe ui/app.py::main()) - das ist beim eigentlichen
    Export-Lauf (Klick auf "Starten" in der laufenden GUI) immer der Fall,
    da run_schedule()/run_by_ids() erst danach aus einem Worker-Thread
    aufgerufen werden. Ohne laufende Qt-Anwendung (z.B. reine CLI-Nutzung
    über emoji_pipeline.py ohne UI) liefert QStandardPaths ggf. keinen mit
    der GUI konsistenten Pfad - deshalb bewusst kein Caching hier (jeder
    Aufruf löst frisch auf) und Fallback auf LEGACY_LETTERMAP_FILE, wenn
    PySide6 nicht importierbar ist oder QStandardPaths nichts liefert.

    Einmalige Migration: existiert die neue Datei noch nicht, aber eine alte
    data/letter_map.json im aktuellen Arbeitsverzeichnis (bestehender
    Dev-Checkout oder ältere Installation), wird sie beim ersten Aufruf an
    den neuen Ort kopiert (Original bleibt unverändert erhalten).
    """
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    except Exception:
        base = None

    if not base:
        return LEGACY_LETTERMAP_FILE

    new_path = Path(base) / "letter_map.json"
    if not new_path.exists() and LEGACY_LETTERMAP_FILE.exists():
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(LEGACY_LETTERMAP_FILE, new_path)
        except Exception:
            pass
    return new_path


def load_lettermap(path: "Path | None" = None) -> Dict[str, Any]:
    if path is None:
        path = lettermap_file_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_lettermap(data: Dict[str, Any], path: "Path | None" = None) -> None:
    if path is None:
        path = lettermap_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
