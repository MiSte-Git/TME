#!/usr/bin/env python3
from __future__ import annotations
import sys
import asyncio
from pathlib import Path
import json
# Ensure project root on sys.path when running from ui/ directly
try:
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
except Exception:
    pass

import threading

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QFileDialog,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTabWidget, QCheckBox, QMessageBox, QComboBox, QProgressBar
)

from pipeline.runner_schedule import run_schedule
from ui.lettermap_tab import LettermapTab

UI_STATE_FILE = Path("data/ui_state.json")


class ScheduleWorker(QObject):
    finished = Signal(object)
    error = Signal(str)
    status = Signal(str)
    waiting_for_mapping = Signal()

    def __init__(
        self,
        schedule_path: Path,
        translate: bool,
        translation_mode: str,
        target_lang: str,
        include_images: bool,
        include_emojis: bool,
        mapping_event: threading.Event,
    ) -> None:
        super().__init__()
        self.schedule_path = schedule_path
        self.translate = translate
        self.translation_mode = translation_mode
        self.target_lang = target_lang
        self.include_images = include_images
        self.include_emojis = include_emojis
        self._mapping_event = mapping_event

    def run(self) -> None:
        try:
            def _cb(msg: str) -> None:
                self.status.emit(msg)

            def _wait_for_mapping() -> None:
                self.waiting_for_mapping.emit()
                self._mapping_event.clear()
                self._mapping_event.wait()
                self.status.emit("Fortsetze nach Mapping…")

            kwargs = dict(
                schedule_path=self.schedule_path,
                out_basename=self.schedule_path.stem,
                output_dir=Path("output"),
                translate=self.translate,
                translation_mode=self.translation_mode,
                target_lang=self.target_lang,
                include_images=self.include_images,
                include_emojis=self.include_emojis,
                config_path=Path("config.yaml"),
                progress_cb=_cb,
                skip_lettermap_ui=True,
                wait_for_mapping_cb=_wait_for_mapping,
            )
            try:
                result = asyncio.run(run_schedule(**kwargs))
            except TypeError as exc:
                if "wait_for_mapping_cb" in str(exc):
                    kwargs.pop("wait_for_mapping_cb", None)
                    kwargs["skip_lettermap_ui"] = False
                    self.status.emit("Warnung: run_schedule unterstützt keinen Fortsetzen-Callback – fahre ohne UI-Verknüpfung fort.")
                    result = asyncio.run(run_schedule(**kwargs))
                else:
                    raise
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class ScheduleTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)

        pick_lay = QHBoxLayout()
        self.schedule_edit = QLineEdit()
        btn_pick = QPushButton("Schedule wählen…")
        btn_pick.clicked.connect(self.pick_schedule)
        pick_lay.addWidget(QLabel("Schedule:"))
        pick_lay.addWidget(self.schedule_edit)
        pick_lay.addWidget(btn_pick)
        lay.addLayout(pick_lay)

        opt_lay = QHBoxLayout()
        self.cb_translate = QCheckBox("Übersetzen")
        self.mode_combo = QComboBox(); self.mode_combo.addItems(["inline", "end", "separate"])
        self.lang_edit = QLineEdit(); self.lang_edit.setPlaceholderText("de")
        self.cb_images = QCheckBox("Bilder einbetten"); self.cb_images.setChecked(True)
        self.cb_emojis = QCheckBox("Custom Emojis einbetten"); self.cb_emojis.setChecked(True)
        opt_lay.addWidget(self.cb_translate)
        opt_lay.addWidget(QLabel("Modus:"))
        opt_lay.addWidget(self.mode_combo)
        opt_lay.addWidget(QLabel("Sprache:"))
        opt_lay.addWidget(self.lang_edit)
        opt_lay.addWidget(self.cb_images)
        opt_lay.addWidget(self.cb_emojis)
        lay.addLayout(opt_lay)

        run_lay = QHBoxLayout()
        self.btn_run = QPushButton("Schedule → ODT erzeugen")
        self.btn_run.clicked.connect(self.run_schedule_file)
        run_lay.addWidget(self.btn_run)
        lay.addLayout(run_lay)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        lay.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        lay.addWidget(self.status_label)

        self.btn_continue = QPushButton("Fortsetzen")
        self.btn_continue.setVisible(False)
        self.btn_continue.clicked.connect(self._on_continue_clicked)
        lay.addWidget(self.btn_continue)

        self.worker_thread: QThread | None = None
        self.worker: ScheduleWorker | None = None
        self._mapping_event = threading.Event()
        self.lettermap_tab: LettermapTab | None = None
        self._loading_state = False

        lay.addStretch()

        self._load_state()
        self._install_state_handlers()

    def set_lettermap_tab(self, tab: LettermapTab) -> None:
        self.lettermap_tab = tab
        tab.set_continue_handler(self._on_continue_clicked)
        tab.on_mapping_finished()

    def pick_schedule(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self,
            "Schedule auswählen",
            str(Path.cwd() / "input"),
            "Schedule (*.json *.txt);;JSON (*.json);;Text (*.txt)"
        )
        if p:
            self.schedule_edit.setText(p)
            self._save_state()

    def run_schedule_file(self) -> None:
        path = Path(self.schedule_edit.text())
        if not path.exists():
            QMessageBox.warning(self, "Fehler", "Bitte eine gültige Schedule-Datei wählen.")
            return
        if self.worker_thread is not None:
            QMessageBox.information(self, "Läuft", "Ein Durchlauf ist bereits aktiv. Bitte warten.")
            return
        translate = self.cb_translate.isChecked()
        target_lang = self.lang_edit.text().strip() or ("de" if translate else "de")
        self.btn_run.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setVisible(True)
        self.status_label.setText("Starte…")
        self.btn_continue.setVisible(False)
        self._mapping_event.clear()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()

        self.worker = ScheduleWorker(
            schedule_path=path,
            translate=translate,
            translation_mode=self.mode_combo.currentText(),
            target_lang=target_lang,
            include_images=self.cb_images.isChecked(),
            include_emojis=self.cb_emojis.isChecked(),
            mapping_event=self._mapping_event,
        )
        self.worker_thread = QThread(self)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.error.connect(self._on_worker_error)
        self.worker.status.connect(self._on_worker_status)
        self.worker.waiting_for_mapping.connect(self._on_waiting_for_mapping)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._on_thread_finished)
        self.worker_thread.start()
        self._save_state()

    def _on_worker_status(self, message: str) -> None:
        self.status_label.setVisible(True)
        self.status_label.setText(message)

    def _on_waiting_for_mapping(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status_label.setVisible(True)
        self.status_label.setText("Bitte Lettermap im Tab anpassen und anschließend 'Fortsetzen' klicken.")
        self.btn_continue.setVisible(True)
        self.btn_continue.setEnabled(True)
        if self.lettermap_tab:
            self.lettermap_tab.on_waiting_for_mapping()

    def _on_continue_clicked(self) -> None:
        self.btn_continue.setEnabled(False)
        self.btn_continue.setVisible(False)
        self.status_label.setText("Prüfe Mapping…")
        self.progress.setRange(0, 0)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()

    def _on_worker_finished(self, result: object) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.btn_run.setEnabled(True)
        self.btn_continue.setVisible(False)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()
        msg: str
        if isinstance(result, tuple):
            main_path, extra_path = result
            if extra_path:
                msg = f"ODTs erzeugt: {main_path}\n{extra_path}"
            else:
                msg = f"ODT erzeugt: {main_path}"
        else:
            msg = f"ODT erzeugt: {result}"
        self.status_label.setText("Fertig.")
        QMessageBox.information(self, "Fertig", msg)
        self.progress.setVisible(False)

    def _on_worker_error(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.status_label.setVisible(True)
        self.status_label.setText("Fehler: " + message)
        QMessageBox.critical(self, "Fehler", message)
        self.btn_continue.setVisible(False)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()

    def _on_thread_finished(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.worker_thread = None
        self.worker = None
        self.btn_continue.setVisible(False)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()

    def _install_state_handlers(self) -> None:
        self.schedule_edit.editingFinished.connect(self._save_state)
        self.cb_translate.toggled.connect(lambda _checked: self._save_state())
        self.mode_combo.currentTextChanged.connect(lambda _text: self._save_state())
        self.lang_edit.editingFinished.connect(self._save_state)
        self.cb_images.toggled.connect(lambda _checked: self._save_state())
        self.cb_emojis.toggled.connect(lambda _checked: self._save_state())

    def _load_state(self) -> None:
        self._loading_state = True
        try:
            if not UI_STATE_FILE.exists():
                return
            data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            schedule_path = data.get("schedule")
            if schedule_path:
                p = Path(str(schedule_path))
                if p.exists():
                    self.schedule_edit.setText(str(p))
                else:
                    self.schedule_edit.clear()
            translate = data.get("translate")
            if isinstance(translate, bool):
                self.cb_translate.setChecked(translate)
            mode = data.get("mode")
            if isinstance(mode, str) and mode in {self.mode_combo.itemText(i) for i in range(self.mode_combo.count())}:
                self.mode_combo.setCurrentText(mode)
            lang = data.get("lang")
            if isinstance(lang, str):
                self.lang_edit.setText(lang)
            include_images = data.get("include_images")
            if isinstance(include_images, bool):
                self.cb_images.setChecked(include_images)
            include_emojis = data.get("include_emojis")
            if isinstance(include_emojis, bool):
                self.cb_emojis.setChecked(include_emojis)
        except Exception:
            pass
        finally:
            self._loading_state = False

    def _save_state(self) -> None:
        if self._loading_state:
            return
        data = {
            "schedule": self.schedule_edit.text().strip(),
            "translate": self.cb_translate.isChecked(),
            "mode": self.mode_combo.currentText(),
            "lang": self.lang_edit.text().strip(),
            "include_images": self.cb_images.isChecked(),
            "include_emojis": self.cb_emojis.isChecked(),
        }
        try:
            UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            UI_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Emoji-ODT Pipeline")
        tabs = QTabWidget()
        self.schedule_tab = ScheduleTab()
        self.lettermap_tab = LettermapTab()
        self.schedule_tab.set_lettermap_tab(self.lettermap_tab)
        tabs.addTab(self.schedule_tab, "Schedule")
        tabs.addTab(self.lettermap_tab, "Lettermap")
        self.setCentralWidget(tabs)


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(900, 420)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
