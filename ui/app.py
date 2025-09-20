#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

# Ensure project root on sys.path when running from ui/ directly
try:
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
except Exception:
    pass

from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QFileDialog,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTabWidget, QCheckBox, QMessageBox, QComboBox
)

from pipeline.adapters.existing_scripts import run_by_date, run_grouped_links
from ui.lettermap_tab import LettermapTab


class ByDateTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)

        # Schedule file picker
        pick_lay = QHBoxLayout()
        self.schedule_edit = QLineEdit()
        btn_pick = QPushButton("TXT wählen…")
        btn_pick.clicked.connect(self.pick_schedule)
        pick_lay.addWidget(QLabel("Plan (TXT):"))
        pick_lay.addWidget(self.schedule_edit)
        pick_lay.addWidget(btn_pick)
        lay.addLayout(pick_lay)

        # Options
        opt_lay = QHBoxLayout()
        self.cb_translate = QCheckBox("Übersetzen")
        self.mode_combo = QComboBox(); self.mode_combo.addItems(["inline","end","separate"]) 
        self.lang_edit = QLineEdit(); self.lang_edit.setPlaceholderText("de")
        opt_lay.addWidget(self.cb_translate)
        opt_lay.addWidget(QLabel("Modus:"))
        opt_lay.addWidget(self.mode_combo)
        opt_lay.addWidget(QLabel("Sprache:"))
        opt_lay.addWidget(self.lang_edit)
        lay.addLayout(opt_lay)

        # Run
        run_lay = QHBoxLayout()
        btn_run_original = QPushButton("Original ausgeben")
        btn_run_original.clicked.connect(self.run_original)
        btn_run_translated = QPushButton("Mit Übersetzung ausgeben")
        btn_run_translated.clicked.connect(self.run_translated)
        run_lay.addWidget(btn_run_original)
        run_lay.addWidget(btn_run_translated)
        lay.addLayout(run_lay)

        lay.addStretch()

    def pick_schedule(self):
        p, _ = QFileDialog.getOpenFileName(self, "Plan/Textdatei auswählen", str(Path.cwd()), "Text (*.txt)")
        if p:
            self.schedule_edit.setText(p)

    def run_original(self):
        schedule = Path(self.schedule_edit.text())
        if not schedule.exists():
            QMessageBox.warning(self, "Fehler", "Bitte eine gültige TXT-Datei wählen.")
            return
        try:
            run_by_date(
                schedule_file=schedule,
                out_odt_basename=schedule.stem,
                output_dir=Path("output"),
                translate=False,
                translation_mode=self.mode_combo.currentText(),
                target_lang=(self.lang_edit.text().strip() or None),
            )
            QMessageBox.information(self, "Fertig", "Original-ODT erzeugt (unter output/).")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))

    def run_translated(self):
        schedule = Path(self.schedule_edit.text())
        if not schedule.exists():
            QMessageBox.warning(self, "Fehler", "Bitte eine gültige TXT-Datei wählen.")
            return
        try:
            run_by_date(
                schedule_file=schedule,
                out_odt_basename=schedule.stem,
                output_dir=Path("output"),
                translate=True,
                translation_mode=self.mode_combo.currentText(),
                target_lang=(self.lang_edit.text().strip() or "de"),
            )
            QMessageBox.information(self, "Fertig", "ODT mit Übersetzung erzeugt (unter output/).")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))


class GroupedLinksTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)

        pick_lay = QHBoxLayout()
        self.links_edit = QLineEdit()
        btn_pick = QPushButton("Links-TXT wählen…")
        btn_pick.clicked.connect(self.pick_links)
        pick_lay.addWidget(QLabel("Links-Datei:"))
        pick_lay.addWidget(self.links_edit)
        pick_lay.addWidget(btn_pick)
        lay.addLayout(pick_lay)

        run_lay = QHBoxLayout()
        btn_run = QPushButton("Gruppen-ODT erzeugen")
        btn_run.clicked.connect(self.run_grouped)
        run_lay.addWidget(btn_run)
        lay.addLayout(run_lay)

        lay.addStretch()

    def pick_links(self):
        p, _ = QFileDialog.getOpenFileName(self, "Links-Datei wählen", str(Path.cwd()), "Text (*.txt)")
        if p:
            self.links_edit.setText(p)

    def run_grouped(self):
        links = Path(self.links_edit.text())
        if not links.exists():
            QMessageBox.warning(self, "Fehler", "Bitte eine gültige TXT-Datei wählen.")
            return
        try:
            run_grouped_links(links_file=links, out_odt_basename=links.stem)
            QMessageBox.information(self, "Fertig", "Gruppen-ODT erzeugt.")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Emoji-ODT Pipeline")
        tabs = QTabWidget()
        tabs.addTab(ByDateTab(), "By-Date")
        tabs.addTab(GroupedLinksTab(), "Grouped Links")
        tabs.addTab(LettermapTab(), "Lettermap")
        self.setCentralWidget(tabs)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(800, 400)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
