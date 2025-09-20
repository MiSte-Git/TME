from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from PySide6.QtCore import Qt, QSize, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox, QHeaderView,
    QCheckBox
)

from pipeline.assets import load_assets
from pipeline.lettermap import load_lettermap, save_lettermap

ASSETS_FILE = Path("data/assets.json")
EXPORT_DIR = Path("custom_emoji_export")
CACHE_DIR = Path("cache/emoji")
LETTERMAP_FILE = Path("data/letter_map.json")
MISSING_DOCS_FILE = Path("data/missing_lettermap_docs.json")
IGNORE_FILE = Path("data/lettermap_ignore.json")


def _collect_entries() -> List[Dict[str, Any]]:
    assets = load_assets(ASSETS_FILE)
    entries: Dict[str, Dict[str, Any]] = {}

    # 1) Aus assets.json
    for doc_id, rec in assets.items():
        file = rec.get("file") or ""
        alt = rec.get("alt") or ""
        hint = rec.get("letter_hint") or ""
        # bevorzugte Reihenfolge: export → cache → assets.file
        exp = EXPORT_DIR / f"{doc_id}.png"
        cache = CACHE_DIR / f"{doc_id}.png"
        if exp.exists():
            file = str(exp)
        elif cache.exists():
            file = str(cache)
        entries[str(doc_id)] = {"doc_id": str(doc_id), "file": file, "alt": alt, "hint": hint}

    # 2) Zusätzliche .png im Export-Ordner
    if EXPORT_DIR.exists():
        for p in EXPORT_DIR.glob("*.png"):
            stem = p.stem
            import re
            m = re.search(r"(\d{8,20})", stem)
            if not m:
                continue
            doc_id = m.group(1)
            if doc_id not in entries:
                entries[doc_id] = {"doc_id": doc_id, "file": str(p), "alt": "", "hint": ""}

    # 3) Zusätzliche .png im Cache-Ordner
    if CACHE_DIR.exists():
        for p in CACHE_DIR.glob("*.png"):
            stem = p.stem
            import re
            m = re.search(r"(\d{8,20})", stem)
            if not m:
                continue
            doc_id = m.group(1)
            if doc_id not in entries:
                entries[doc_id] = {"doc_id": doc_id, "file": str(p), "alt": "", "hint": ""}

    return [entries[k] for k in sorted(entries.keys())]


def _invert_lettermap(lettermap: Dict[str, Any]) -> Dict[str, str]:
    """
    Baut eine Map doc_id -> letter-key auf.
    Unterstützt sowohl {document_id: str} als auch {document_ids: [str,...], primary: str}.
    Bei Mehrfachzuordnung werden alle doc_ids dem gleichen Buchstaben zugewiesen.
    """
    inv: Dict[str, str] = {}
    for key, v in (lettermap or {}).items():
        if not isinstance(v, dict):
            continue
        # Liste verwenden, wenn vorhanden, sonst einzelne ID
        docs: List[str] = []
        if isinstance(v.get("document_ids"), list) and v.get("document_ids"):
            docs = [str(x) for x in v.get("document_ids") if str(x)]
        else:
            doc_single = str(v.get("document_id", "")).strip()
            if doc_single:
                docs = [doc_single]
        for d in docs:
            inv[d] = key
    return inv


class LettermapTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.entries: List[Dict[str, Any]] = []
        self.lettermap: Dict[str, Any] = {}
        self.missing_doc_ids: set[str] = set()
        self.ignored_doc_ids: set[str] = set()

        lay = QVBoxLayout(self)

        # Toolbar
        top = QHBoxLayout()
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText("Suchen (doc_id, alt, hint)…")
        self.search_edit.textChanged.connect(self._apply_filter)
        btn_reload = QPushButton("Neu laden")
        btn_reload.clicked.connect(self.reload_data)
        btn_save = QPushButton("Speichern")
        btn_save.clicked.connect(self.save_mapping)
        self.cb_only_unmapped = QCheckBox("Nur ungemappte")
        self.cb_only_unmapped.toggled.connect(self._apply_filter)
        self.cb_only_missing = QCheckBox("Nur fehlende doc_ids")
        self.cb_only_missing.toggled.connect(self._apply_filter)
        top.addWidget(self.search_edit)
        top.addWidget(self.cb_only_unmapped)
        top.addWidget(self.cb_only_missing)
        top.addWidget(btn_reload)
        top.addWidget(btn_save)
        lay.addLayout(top)

        # Tabelle
        info = QLabel("Hinweis: Nach dem Speichern kannst du dieses Fenster schließen. Der laufende Prozess macht automatisch weiter.")
        info.setWordWrap(True)
        lay.addWidget(info)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Vorschau", "doc_id", "alt", "hint", "Mapping (Buchstabe)", "Ignorieren"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(110)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setIconSize(QSize(96, 96))
        self.table.cellDoubleClicked.connect(self._open_image_for_row)
        lay.addWidget(self.table)

        # Bottom actions
        bottom = QHBoxLayout()
        btn_close = QPushButton("Fertig – schließen")
        btn_close.clicked.connect(lambda: self.window().close())
        bottom.addStretch(1)
        bottom.addWidget(btn_close)
        lay.addLayout(bottom)

        self.reload_data()

    def _load_missing_ids(self) -> None:
        self.missing_doc_ids = set()
        try:
            if MISSING_DOCS_FILE.exists():
                import json
                data = json.loads(MISSING_DOCS_FILE.read_text(encoding="utf-8"))
                ids = data.get("missing_doc_ids") or data.get("missing") or []
                self.missing_doc_ids = {str(x) for x in ids}
        except Exception:
            self.missing_doc_ids = set()

    def _load_ignore(self) -> None:
        self.ignored_doc_ids = set()
        try:
            if IGNORE_FILE.exists():
                import json
                ids = json.loads(IGNORE_FILE.read_text(encoding="utf-8"))
                if isinstance(ids, list):
                    self.ignored_doc_ids = {str(x) for x in ids}
        except Exception:
            self.ignored_doc_ids = set()

    # Data loading ---------------------------------------------------------
    def reload_data(self) -> None:
        try:
            self._load_missing_ids()
            self._load_ignore()
            self.entries = _collect_entries()
            self.lettermap = load_lettermap(LETTERMAP_FILE)
            inv = _invert_lettermap(self.lettermap)
            self._populate_table(inv)
            self._apply_filter()
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))

    def _populate_table(self, inv_map: Dict[str, str]) -> None:
        self.table.setRowCount(0)
        for rec in self.entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            # Vorschau
            lab = QLabel()
            lab.setAlignment(Qt.AlignCenter)
            p = Path(rec.get("file") or "")
            if p.exists():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    lab.setPixmap(pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    lab.setToolTip(str(p))
                else:
                    lab.setText("[kein Bild]")
            else:
                lab.setText("[kein Bild]")
            self.table.setCellWidget(row, 0, lab)
            # doc_id
            doc_id = str(rec.get("doc_id", ""))
            self.table.setItem(row, 1, QTableWidgetItem(doc_id))
            # alt, hint
            self.table.setItem(row, 2, QTableWidgetItem(str(rec.get("alt", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(str(rec.get("hint", ""))))
            # mapping (LineEdit)
            le = QLineEdit()
            le.setMaxLength(8)
            le.setText(inv_map.get(doc_id, ""))
            le.textChanged.connect(lambda _t, r=row: self._normalize_key(r))
            self.table.setCellWidget(row, 4, le)
            # ignorieren (Checkbox)
            cb = QCheckBox()
            cb.setChecked(doc_id in self.ignored_doc_ids)
            self.table.setCellWidget(row, 5, cb)

    # Filtering ------------------------------------------------------------
    def _apply_filter(self) -> None:
        q = (self.search_edit.text() or "").strip().lower()
        only_unmapped = getattr(self, "cb_only_unmapped", None)
        only_unmapped_checked = bool(only_unmapped.isChecked()) if only_unmapped else False
        only_missing = getattr(self, "cb_only_missing", None)
        only_missing_checked = bool(only_missing.isChecked()) if only_missing else False
        for row in range(self.table.rowCount()):
            doc_id_item = self.table.item(row, 1)
            doc_id = (doc_id_item.text() if doc_id_item else "").lower()
            alt = (self.table.item(row, 2).text() if self.table.item(row, 2) else "").lower()
            hint = (self.table.item(row, 3).text() if self.table.item(row, 3) else "").lower()
            w = self.table.cellWidget(row, 4)
            mapped_text = ""
            if isinstance(w, QLineEdit):
                mapped_text = (w.text() or "").strip()
            matches_search = (q in doc_id) or (q in alt) or (q in hint) if q else True
            matches_unmapped = (not mapped_text) if only_unmapped_checked else True
            matches_missing = (doc_id in {x.lower() for x in self.missing_doc_ids}) if only_missing_checked else True
            visible = matches_search and matches_unmapped and matches_missing
            self.table.setRowHidden(row, not visible)

    def _normalize_key(self, row: int) -> None:
        w = self.table.cellWidget(row, 4)
        if isinstance(w, QLineEdit):
            t = w.text()
            nt = t.strip().upper()
            if nt != t:
                w.blockSignals(True)
                w.setText(nt)
                w.blockSignals(False)

    def _open_image_for_row(self, row: int, col: int) -> None:
        # Doppelklick öffnet Bild in Standard-Viewer
        p_item = self.table.item(row, 1)
        if not p_item:
            return
        doc_id = p_item.text()
        # Versuche Export → Cache
        for base in (EXPORT_DIR, CACHE_DIR):
            f = base / f"{doc_id}.png"
            if f.exists() and f.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(f)))
                return
        QMessageBox.information(self, "Hinweis", "Kein Bild gefunden (export/cache).")

    # Save -----------------------------------------------------------------
    def save_mapping(self) -> None:
        # Lade bestehendes Mapping, um zu mergen
        existing = load_lettermap(LETTERMAP_FILE)
        # existing ist Dict[str, Any]; wir interpretieren sowohl document_id als auch document_ids
        merged: Dict[str, Any] = {}
        # Übernehme bestehende Einträge
        try:
            import json
            raw = json.loads(LETTERMAP_FILE.read_text(encoding="utf-8")) if LETTERMAP_FILE.exists() else {}
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, dict):
                    docs = []
                    if isinstance(v.get("document_ids"), list):
                        docs = [str(x) for x in v.get("document_ids") if str(x)]
                    elif v.get("document_id"):
                        docs = [str(v.get("document_id"))]
                    if docs:
                        merged[k] = {"document_ids": list(dict.fromkeys(docs)), "primary": docs[0]}
        # Sammle neue Zuordnungen aus der Tabelle
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, 4)
            if not isinstance(w, QLineEdit):
                continue
            key = (w.text() or "").strip()
            if not key:
                continue
            doc_id = self.table.item(row, 1).text().strip()
            entry = merged.get(key) or {"document_ids": [], "primary": doc_id}
            arr = entry.get("document_ids") or []
            if doc_id not in arr:
                arr.append(doc_id)
            entry["document_ids"] = arr
            if not entry.get("primary"):
                entry["primary"] = doc_id
            merged[key] = entry
        # Ignorierliste aus Tabelle sammeln
        ignore_ids: list[str] = []
        for row in range(self.table.rowCount()):
            doc_item = self.table.item(row, 1)
            cb = self.table.cellWidget(row, 5)
            if doc_item and isinstance(cb, QCheckBox) and cb.isChecked():
                ignore_ids.append(doc_item.text().strip())
        try:
            # Speichere zusammengeführte Struktur + Ignore-Liste
            import json
            LETTERMAP_FILE.parent.mkdir(parents=True, exist_ok=True)
            LETTERMAP_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            IGNORE_FILE.parent.mkdir(parents=True, exist_ok=True)
            IGNORE_FILE.write_text(json.dumps(sorted(set(ignore_ids)), ensure_ascii=False, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Gespeichert", f"Mapping gespeichert nach {LETTERMAP_FILE}\nIgnorieren gespeichert nach {IGNORE_FILE}.")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))
