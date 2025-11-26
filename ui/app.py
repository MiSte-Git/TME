#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import asyncio
import json
import os
import threading
import warnings

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from credentials import get_telegram_credentials, save_telegram_credentials

warnings.filterwarnings(
    "ignore",
    message=".*NVIDIA GeForce GT 1030.*not compatible with the current PyTorch installation.*",
    category=UserWarning,
)

from PySide6.QtCore import QObject, QThread, Signal, Qt, QLocale, QTranslator, QEvent, QUrl
from PySide6.QtGui import QAction, QActionGroup, QIcon, QDesktopServices
from functools import partial
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QFileDialog,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTabWidget, QCheckBox, QMessageBox, QComboBox, QProgressBar,
    QInputDialog
)

from pipeline.runner_schedule import run_schedule
from ui.lettermap_tab import LettermapTab
from ui.schedule_editor_tab import ScheduleEditorTab

UI_STATE_FILE = Path("data/ui_state.json")
THEME_STATE_FILE = Path("data/ui_theme.json")
LANG_STATE_FILE = Path("data/ui_lang.json")
TRANSLATIONS_DIR = Path(__file__).parent / "translations"


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
        lettermap_enabled: bool = False,
        source_lang: str = "de",
    ) -> None:
        super().__init__()
        self.schedule_path = schedule_path
        self.translate = translate
        self.translation_mode = translation_mode
        self.target_lang = target_lang
        self.include_images = include_images
        self.include_emojis = include_emojis
        self._mapping_event = mapping_event
        self.lettermap_enabled = lettermap_enabled
        self.source_lang = source_lang

    def run(self) -> None:
        try:
            def _cb(msg: str) -> None:
                self.status.emit(msg)

            def _wait_for_mapping() -> None:
                self.waiting_for_mapping.emit()
                self._mapping_event.clear()
                self._mapping_event.wait()
                self.status.emit(self.tr("Fortsetze nach Mapping…"))

            # Lettermap toggling via runner_by_ids globals (no config merge needed)
            try:
                import pipeline.runner_by_ids as _rbi_cfg
                _rbi_cfg._LM_IN_ORIGINAL = bool(self.lettermap_enabled)
                _rbi_cfg._LM_SCOPE = "all" if self.lettermap_enabled else "none"
                _rbi_cfg._LM_OPEN_UI_ON_MISSING = False
            except Exception:
                pass
            kwargs = dict(
                schedule_path=self.schedule_path,
                out_basename=self.schedule_path.stem,
                output_dir=Path("output"),
                translate=self.translate,
                translation_mode=self.translation_mode,
                target_lang=self.target_lang,
                include_images=self.include_images,
                include_emojis=self.include_emojis,
                source_lang=self.source_lang,
                config_path=Path("config.yaml"),
                progress_cb=_cb,
                skip_lettermap_ui=True,
            )
            if self.lettermap_enabled:
                kwargs["wait_for_mapping_cb"] = _wait_for_mapping
            try:
                result = asyncio.run(run_schedule(**kwargs))
            except TypeError as exc:
                if "wait_for_mapping_cb" in str(exc):
                    kwargs.pop("wait_for_mapping_cb", None)
                    kwargs["skip_lettermap_ui"] = False
                    self.status.emit(self.tr("Warnung: run_schedule unterstützt keinen Fortsetzen-Callback – fahre ohne UI-Verknüpfung fort."))
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
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        self._build_ui(lay)

    def _build_ui(self, lay: QVBoxLayout) -> None:
        # split out so we can retranslate incrementally
        
        pick_lay = QHBoxLayout()
        pick_lay.setSpacing(8)
        self.schedule_edit = QLineEdit()
        self.btn_pick = QPushButton(self.tr("Datei wählen…"))
        self.btn_pick.clicked.connect(self.pick_schedule)
        self.lbl_schedule = QLabel(self.tr("Telegram-Export:"))
        pick_lay.addWidget(self.lbl_schedule)
        pick_lay.addWidget(self.schedule_edit)
        pick_lay.addWidget(self.btn_pick)
        lay.addLayout(pick_lay)

        opt_lay = QHBoxLayout()
        opt_lay.setSpacing(8)
        self.cb_translate = QCheckBox(self.tr("Übersetzen"))
        self.mode_combo = QComboBox(); self.mode_combo.addItems(["inline", "end", "separate"])
        # Quellsprachen-Auswahl für Dateiname (entspricht Sprachleiste)
        self.src_lang_combo = QComboBox(); self.src_lang_combo.addItems(["de", "en", "fr", "it", "ru", "pl", "es", "hr", "nl", "fi"])
        self.lang_edit = QLineEdit(); self.lang_edit.setPlaceholderText("de")
        self.cb_images = QCheckBox(self.tr("Bilder einbetten")); self.cb_images.setChecked(True)
        self.cb_emojis = QCheckBox(self.tr("Custom Emojis einbetten")); self.cb_emojis.setChecked(True)
        self.cb_lettermap = QCheckBox(self.tr("Lettermapping aktivieren")); self.cb_lettermap.setChecked(False)
        self.lbl_mode = QLabel(self.tr("Modus:"))
        self.lbl_lang = QLabel(self.tr("Sprache:"))
        self.lbl_src_lang = QLabel(self.tr("Quellsprache (Dateiname):"))
        opt_lay.addWidget(self.cb_translate)
        opt_lay.addWidget(self.lbl_mode)
        opt_lay.addWidget(self.mode_combo)
        opt_lay.addWidget(self.lbl_src_lang)
        opt_lay.addWidget(self.src_lang_combo)
        opt_lay.addWidget(self.lbl_lang)
        opt_lay.addWidget(self.lang_edit)
        opt_lay.addWidget(self.cb_images)
        opt_lay.addWidget(self.cb_emojis)
        opt_lay.addWidget(self.cb_lettermap)
        lay.addLayout(opt_lay)

        run_lay = QHBoxLayout()
        run_lay.setSpacing(8)
        self.btn_run = QPushButton(self.tr("Schedule → ODT erzeugen"))
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

        self.btn_continue = QPushButton(self.tr("Fortsetzen"))
        self.btn_continue.setVisible(False)
        self.btn_continue.clicked.connect(self._on_continue_clicked)
        lay.addWidget(self.btn_continue)

        # Open output folder button (shown after finish)
        self.btn_open_output = QPushButton(self.tr("Ausgabeordner öffnen"))
        self.btn_open_output.setVisible(False)
        self.btn_open_output.clicked.connect(self._open_output_folder)
        lay.addWidget(self.btn_open_output)
        
        self.worker_thread: QThread | None = None
        self.worker: ScheduleWorker | None = None
        self._mapping_event = threading.Event()
        self.lettermap_tab: LettermapTab | None = None
        self._loading_state = False
        self._last_output_path: Path | None = None
 
        lay.addStretch()
 
        self._load_state()
        self._install_state_handlers()

    def _credentials_present(self) -> bool:
        try:
            get_telegram_credentials()
            return True
        except RuntimeError:
            return False

    def _prompt_store_credentials(self) -> bool:
        api_id_text, ok1 = QInputDialog.getText(self, self.tr("Telegram API"), self.tr("API ID (my.telegram.org):"))
        if not ok1:
            return False
        api_hash_text, ok2 = QInputDialog.getText(self, self.tr("Telegram API"), self.tr("API Hash (my.telegram.org):"))
        if not ok2:
            return False
        try:
            api_id_text = api_id_text.strip()
            api_hash_text = api_hash_text.strip()
            phone_text = ""
            if not api_id_text or not api_hash_text:
                QMessageBox.warning(self, self.tr("Fehler"), self.tr("ID oder Hash nicht gesetzt"))
                return False

            api_id = int(api_id_text)
            phone_val = phone_text.strip() or None
            save_telegram_credentials(api_id, api_hash_text, phone_val)
            # Optional Validierung über zentrale Funktion
            get_telegram_credentials()
            return True
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))
            return False

    def _ensure_credentials(self) -> bool:
        if self._credentials_present():
            return True
        ans = QMessageBox.question(
            self,
            self.tr("Telegram API"),
            self.tr("Telegram API-Zugangsdaten fehlen. Jetzt eintragen und lokal speichern?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ans != QMessageBox.Yes:
            return False
        return self._prompt_store_credentials()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def retranslate(self) -> None:
        self.btn_pick.setText(self.tr("Datei wählen…"))
        self.lbl_schedule.setText(self.tr("Telegram-Export:"))
        self.cb_translate.setText(self.tr("Übersetzen"))
        self.lbl_mode.setText(self.tr("Modus:"))
        self.lbl_src_lang.setText(self.tr("Quellsprache (Dateiname):"))
        self.lbl_lang.setText(self.tr("Sprache:"))
        self.cb_images.setText(self.tr("Bilder einbetten"))
        self.cb_emojis.setText(self.tr("Custom Emojis einbetten"))
        self.cb_lettermap.setText(self.tr("Lettermapping aktivieren"))
        self.btn_run.setText(self.tr("Telegram-Export → ODT erzeugen"))
        self.btn_open_output.setText(self.tr("Ausgabeordner öffnen"))
        self.btn_continue.setText(self.tr("Fortsetzen"))
        # placeholders
        self.lang_edit.setPlaceholderText("de")

    def set_lettermap_tab(self, tab: LettermapTab) -> None:
        self.lettermap_tab = tab
        tab.set_continue_handler(self._on_continue_clicked)
        tab.on_mapping_finished()

    def pick_schedule(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Telegram-Export auswählen"),
            str(Path.cwd() / "input"),
            self.tr("Telegram-Export (*.json *.txt);;JSON (*.json);;Text (*.txt)")
        )
        if p:
            self.schedule_edit.setText(p)
            self._save_state()

    def run_schedule_file(self) -> None:
        path = Path(self.schedule_edit.text())
        if not path.exists():
            QMessageBox.warning(self, self.tr("Fehler"), self.tr("Bitte eine gültige Schedule-Datei wählen."))
            return
        if self.worker_thread is not None:
            QMessageBox.information(self, self.tr("Läuft"), self.tr("Ein Durchlauf ist bereits aktiv. Bitte warten."))
            return
        # Ensure Telegram credentials before starting
        if not self._ensure_credentials():
            return
        translate = self.cb_translate.isChecked()
        target_lang = self.lang_edit.text().strip() or ("de" if translate else "de")
        source_lang = self.src_lang_combo.currentText().strip() or "de"
        self.btn_run.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setVisible(True)
        self.status_label.setText(self.tr("Starte…"))
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
            lettermap_enabled=self.cb_lettermap.isChecked(),
            source_lang=source_lang,
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
        self.status_label.setText(self.tr("Bitte Lettermap im Tab anpassen und anschließend 'Fortsetzen' klicken."))
        self.btn_continue.setVisible(True)
        self.btn_continue.setEnabled(True)
        if self.lettermap_tab:
            self.lettermap_tab.on_waiting_for_mapping()

    def _on_continue_clicked(self) -> None:
        self.btn_continue.setEnabled(False)
        self.btn_continue.setVisible(False)
        self.status_label.setText(self.tr("Prüfe Mapping…"))
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
        main_out: Path | None = None
        if isinstance(result, tuple):
            main_path, extra_path = result
            try:
                main_out = Path(str(main_path)) if main_path else None
            except Exception:
                main_out = None
            if extra_path:
                msg = self.tr("ODTs erzeugt: {main}\n{extra}").format(main=main_path, extra=extra_path)
            else:
                msg = self.tr("ODT erzeugt: {main}").format(main=main_path)
        else:
            try:
                main_out = Path(str(result)) if result else None
            except Exception:
                main_out = None
            msg = self.tr("ODT erzeugt: {path}").format(path=result)
        self.status_label.setText(self.tr("Fertig."))
        # Merke Ausgabe-Pfad und zeige Button
        self._last_output_path = main_out
        self.btn_open_output.setVisible(True)
        self.btn_open_output.setEnabled(True)
        QMessageBox.information(self, self.tr("Fertig"), msg)
        self.progress.setVisible(False)

    def _on_worker_error(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.status_label.setVisible(True)
        self.status_label.setText(self.tr("Fehler: ") + message)
        QMessageBox.critical(self, self.tr("Fehler"), message)
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

    def _open_output_folder(self) -> None:
        try:
            target: Path
            if getattr(self, "_last_output_path", None) and Path(str(self._last_output_path)).exists():
                target = Path(str(self._last_output_path)).parent
            else:
                target = Path.cwd() / "output"
            target.mkdir(parents=True, exist_ok=True)
            url = QUrl.fromLocalFile(str(target))
            try:
                QDesktopServices.openUrl(url)
            except Exception:
                pass
            # Always also try system opener for robustness
            import subprocess, sys
            try:
                if sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(target)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                elif sys.platform.startswith("win"):
                    subprocess.Popen(["explorer", str(target)])
            except Exception:
                pass
        except Exception:
            pass

    def _install_state_handlers(self) -> None:
        self.schedule_edit.editingFinished.connect(self._save_state)
        self.cb_translate.toggled.connect(lambda _checked: self._save_state())
        self.mode_combo.currentTextChanged.connect(lambda _text: self._save_state())
        self.src_lang_combo.currentTextChanged.connect(lambda _text: self._save_state())
        self.lang_edit.editingFinished.connect(self._save_state)
        self.cb_images.toggled.connect(lambda _checked: self._save_state())
        self.cb_emojis.toggled.connect(lambda _checked: self._save_state())
        self.cb_lettermap.toggled.connect(lambda _checked: self._save_state())

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
            src_lang = data.get("source_lang")
            if isinstance(src_lang, str):
                idx = self.src_lang_combo.findText(src_lang)
                if idx >= 0:
                    self.src_lang_combo.setCurrentIndex(idx)
            include_images = data.get("include_images")
            if isinstance(include_images, bool):
                self.cb_images.setChecked(include_images)
            include_emojis = data.get("include_emojis")
            if isinstance(include_emojis, bool):
                self.cb_emojis.setChecked(include_emojis)
            lm_en = data.get("lettermap_enabled")
            if isinstance(lm_en, bool):
                self.cb_lettermap.setChecked(lm_en)
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
            "source_lang": self.src_lang_combo.currentText().strip(),
            "include_images": self.cb_images.isChecked(),
            "include_emojis": self.cb_emojis.isChecked(),
            "lettermap_enabled": self.cb_lettermap.isChecked(),
        }
        try:
            UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            UI_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Telegram → ODT mit Emoji & Übersetzung")
        # App/Icon setzen, wenn vorhanden
        root = Path(__file__).resolve().parents[1]
        icon_path = root / "Telegram-LibreOffice.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.tabs = QTabWidget()
        self.schedule_tab = ScheduleTab()
        self.editor_tab = ScheduleEditorTab()
        self.lettermap_tab = LettermapTab()
        self.schedule_tab.set_lettermap_tab(self.lettermap_tab)
        # Reorder: Schedule, Schedule-Editor, Lettermap (Experimentell)
        self.tabs.addTab(self.schedule_tab, self.tr("Telegram-Export"))
        self.tabs.addTab(self.editor_tab, self.tr("Schedule-Editor"))
        self.tabs.addTab(self.lettermap_tab, self.tr("Lettermap (Experimentell)"))
        # Wrap central with a top language bar
        from PySide6.QtWidgets import QWidget as _QW, QVBoxLayout as _QVL
        central = _QW()
        vlay = _QVL(central)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)
        self._init_lang_bar(vlay)
        vlay.addWidget(self.tabs)
        self.setCentralWidget(central)

        # Menü: Ansicht → Theme und Sprache
        self._init_menus()
        
    def _init_lang_bar(self, parent_layout) -> None:
        from PySide6.QtWidgets import QWidget, QHBoxLayout, QToolButton
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        # Flag buttons (endonyms used as tooltip)
        self.lang_flag_buttons: dict[str, QToolButton] = {}
        langs = [
            ("de", "🇩🇪", "Deutsch"),
            ("en", "🇬🇧", "English"),
            ("fr", "🇫🇷", "Français"),
            ("it", "🇮🇹", "Italiano"),
            ("ru", "🇷🇺", "Русский"),
            ("pl", "🇵🇱", "Polski"),
            ("es", "🇪🇸", "Español"),
            ("hr", "🇭🇷", "Hrvatski"),
            ("nl", "🇳🇱", "Nederlands"),
            ("fi", "🇫🇮", "Suomi"),
        ]
        lay.addStretch(1)
        for code, flag, tip in langs:
            btn = QToolButton()
            btn.setText(flag)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setProperty("base_flag", flag)
            btn.setStyleSheet("font-size: 22px; padding: 2px 6px;")
            btn.clicked.connect(lambda _=False, c=code: self._on_lang_clicked(c))
            lay.addWidget(btn)
            self.lang_flag_buttons[code] = btn
        lay.addStretch(1)
        parent_layout.addWidget(bar)
        # highlight current
        self.update_lang_flag_highlight(_load_language_preference())

    def update_lang_flag_highlight(self, cur: str) -> None:
        try:
            for code, btn in (self.lang_flag_buttons or {}).items():
                is_cur = (code == cur)
                btn.setChecked(is_cur)
                base = btn.property("base_flag") or btn.text().replace("★", "").strip()
                btn.setText(f"{base} ★" if is_cur else str(base))
                btn.setStyleSheet("font-size: 22px; padding: 2px 6px;" + (" font-weight: bold;" if is_cur else ""))
        except Exception:
            pass

    def _on_lang_clicked(self, code: str) -> None:
        try:
            _set_language(code)
            self.update_lang_flag_highlight(code)
        except Exception:
            pass

    def _open_doc(self, rel_path: str) -> None:
        try:
            root = Path(__file__).resolve().parents[1]
            p = root / rel_path
            if p.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
            else:
                QMessageBox.information(self, self.tr("Hinweis"), self.tr("Datei nicht gefunden: ") + str(p))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))

    def _open_output_folder(self) -> None:
        # Prefer last output path; else default output dir
        try:
            target: Path
            if self._last_output_path and Path(str(self._last_output_path)).exists():
                target = Path(str(self._last_output_path)).parent
            else:
                target = Path.cwd() / "output"
            target.mkdir(parents=True, exist_ok=True)
            url = QUrl.fromLocalFile(str(target))
            try:
                QDesktopServices.openUrl(url)
            except Exception:
                pass
            # Always also try system opener for robustness
            import subprocess, sys
            try:
                if sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(target)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                elif sys.platform.startswith("win"):
                    subprocess.Popen(["explorer", str(target)])
            except Exception:
                pass
        except Exception:
            pass

    def _show_api_help(self) -> None:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        cred_path = str(Path(xdg) / "telegram-odt" / "credentials.json")
        title = self.tr("Hinweis: Telegram API-Schlüssel")
        h3 = self.tr("<h3>Telegram API-Schlüssel</h3>")
        p1 = self.tr("<p>Die App benötigt API ID und API Hash von Telegram. So erhältst du sie:</p>")
        li1 = self.tr("<li>Öffne <a href='https://my.telegram.org'>my.telegram.org</a> und melde dich an.</li>")
        li2 = self.tr("<li>Gehe zu <b>API development tools</b> und erstelle eine Anwendung.</li>")
        li3 = self.tr("<li>Kopiere <b>API ID</b> und <b>API Hash</b>.</li>")
        p2 = self.tr("<p>Ablage (ohne Repo-Leak):</p>")
        li4 = self.tr("<li>Empfohlen: <code>{path}</code> (wird beim ersten Start automatisch angelegt)</li>").format(path=cred_path)
        li5 = self.tr("<li>Oder als Umgebungsvariablen: <code>TELEGRAM_API_ID</code> und <code>TELEGRAM_API_HASH</code></li>")
        li6 = self.tr("<li>Mehr Details: siehe Deployment-Anleitung im Hilfe-Menü.</li>")
        html = (
            h3 + p1 + "<ol>" + li1 + li2 + li3 + "</ol>" + p2 + "<ul>" + li4 + li5 + li6 + "</ul>"
        )
        QMessageBox.about(self, title, html)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def retranslate(self) -> None:
        # Window title
        self.setWindowTitle(self.tr("Telegram → ODT mit Emoji & Übersetzung"))
        # Tabs
        self.tabs.setTabText(0, self.tr("Telegram-Export"))
        if self.tabs.count() >= 2:
            self.tabs.setTabText(1, self.tr("Schedule-Editor"))
        if self.tabs.count() >= 3:
            self.tabs.setTabText(2, self.tr("Lettermap (Experimentell)"))
        # Menüs
        self.view_menu.setTitle(self.tr("Ansicht"))
        if hasattr(self, "help_menu"):
            self.help_menu.setTitle(self.tr("Hilfe"))
            if hasattr(self, "action_api_help"):
                self.action_api_help.setText(self.tr("Hinweis: Telegram API-Schlüssel"))
            if hasattr(self, "action_readme"):
                self.action_readme.setText(self.tr("README öffnen"))
            if hasattr(self, "action_deploy"):
                self.action_deploy.setText(self.tr("Deployment-Anleitung öffnen"))
            if hasattr(self, "action_spec"):
                self.action_spec.setText(self.tr("PyInstaller-Spezifikation öffnen"))
        self.lang_menu.setTitle(self.tr("Sprache"))
        self.action_light.setText(self.tr("Hell"))
        self.action_dark.setText(self.tr("Dunkel"))
        # Sprachaktionen werden dynamisch in _init_menus erstellt
        # Weiterreichen an Tabs
        if hasattr(self.schedule_tab, "retranslate"):
            self.schedule_tab.retranslate()
        if hasattr(self.lettermap_tab, "retranslate"):
            self.lettermap_tab.retranslate()
        if hasattr(self, "editor_tab") and hasattr(self.editor_tab, "retranslate"):
            self.editor_tab.retranslate()

    def _init_menus(self) -> None:
        menubar = self.menuBar()
        self.view_menu = menubar.addMenu(self.tr("Ansicht"))
        # Theme
        group_theme = QActionGroup(self)
        group_theme.setExclusive(True)
        self.action_light = QAction(self.tr("Hell"), self, checkable=True)
        self.action_dark = QAction(self.tr("Dunkel"), self, checkable=True)
        group_theme.addAction(self.action_light)
        group_theme.addAction(self.action_dark)
        self.view_menu.addAction(self.action_light)
        self.view_menu.addAction(self.action_dark)
        theme = _load_theme_preference()
        if theme == "light":
            self.action_light.setChecked(True)
        else:
            self.action_dark.setChecked(True)
        self.action_light.triggered.connect(lambda: _set_theme("light"))
        self.action_dark.triggered.connect(lambda: _set_theme("dark"))
        # Hilfe / Doku (ganz rechts)
        self.help_menu = menubar.addMenu(self.tr("Hilfe"))
        self.action_api_help = QAction(self.tr("Hinweis: Telegram API-Schlüssel"), self)
        self.action_readme = QAction(self.tr("README öffnen"), self)
        self.action_deploy = QAction(self.tr("Deployment-Anleitung öffnen"), self)
        self.action_spec = QAction(self.tr("PyInstaller-Spezifikation öffnen"), self)
        self.action_api_help.triggered.connect(self._show_api_help)
        self.action_readme.triggered.connect(lambda: self._open_doc("README.md"))
        self.action_deploy.triggered.connect(lambda: self._open_doc("docs/DEPLOY.md"))
        self.action_spec.triggered.connect(lambda: self._open_doc("telegram_odt.spec"))
        self.help_menu.addAction(self.action_api_help)
        self.help_menu.addAction(self.action_readme)
        self.help_menu.addAction(self.action_deploy)
        self.help_menu.addAction(self.action_spec)
        # Sprache: Flaggenleiste statt Menü (siehe _init_lang_bar)


def _apply_theme(app: QApplication, theme: str) -> None:
    """Apply QSS theme based on name ('light' or 'dark'). Fallbacks gracefully."""
    base = Path(__file__).parent
    files = {
        "light": base / "theme_light.qss",
        "dark": base / "theme_dark.qss",
    }
    qss = ""
    p = files.get(theme)
    if p and p.exists():
        try:
            qss = p.read_text(encoding="utf-8")
        except Exception:
            qss = ""
    # Backward-compat: old theme.qss (assume dark)
    if not qss:
        legacy = base / "theme.qss"
        if legacy.exists():
            try:
                qss = legacy.read_text(encoding="utf-8")
            except Exception:
                qss = ""
    app.setStyleSheet(qss)


def _load_theme_preference() -> str:
    try:
        if THEME_STATE_FILE.exists():
            data = json.loads(THEME_STATE_FILE.read_text(encoding="utf-8"))
            t = str(data.get("theme", "dark")).lower()
            return "light" if t == "light" else "dark"
    except Exception:
        pass
    return "dark"


def _save_theme_preference(theme: str) -> None:
    try:
        data = {}
        if THEME_STATE_FILE.exists():
            data = json.loads(THEME_STATE_FILE.read_text(encoding="utf-8")) or {}
        data["theme"] = theme
        THEME_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        THEME_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _set_theme(theme: str) -> None:
    app = QApplication.instance()
    if app is None:
        return
    _apply_theme(app, theme)
    _save_theme_preference(theme)


_translator: QTranslator | None = None

def _apply_language(app: QApplication, lang: str) -> None:
    global _translator
    # Remove old translator
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None
    # Load new translator (requires compiled .qm)
    tr = QTranslator(app)
    # try ui/translations/app_LANG.qm
    base_name = f"app_{lang}"
    ok = False
    for p in (
        TRANSLATIONS_DIR / f"{base_name}.qm",
        Path.cwd() / "ui" / "translations" / f"{base_name}.qm",
    ):
        if p.exists():
            ok = tr.load(str(p))
            if ok:
                break
    if ok:
        app.installTranslator(tr)
        _translator = tr


_SUPPORTED_LANGS = {"de","en","fr","it","ru","pl","es","hr","nl","fi"}

def _load_language_preference() -> str:
    try:
        if LANG_STATE_FILE.exists():
            data = json.loads(LANG_STATE_FILE.read_text(encoding="utf-8"))
            l = str(data.get("lang", "de")).lower()
            return l if l in _SUPPORTED_LANGS else "de"
    except Exception:
        pass
    # Default: system locale → choose best match by prefix
    sys_lang = QLocale.system().name().lower()  # e.g., de_de
    for cand in _SUPPORTED_LANGS:
        if sys_lang.startswith(cand):
            return cand
    return "de"


def _save_language_preference(lang: str) -> None:
    try:
        data = {"lang": lang}
        LANG_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LANG_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _set_language(lang: str) -> None:
    app = QApplication.instance()
    if app is None:
        return
    _save_language_preference(lang)
    _apply_language(app, lang)
    # Retranslate top-level MainWindow(s)
    for w in app.topLevelWidgets():
        try:
            if isinstance(w, MainWindow):
                w.retranslate()
                try:
                    w.update_lang_flag_highlight(lang)
                except Exception:
                    pass
        except Exception:
            pass


def main() -> None:
    app = QApplication(sys.argv)
    # Use a modern, consistent style across platforms
    try:
        from PySide6.QtWidgets import QStyleFactory
        app.setStyle(QStyleFactory.create("Fusion"))
    except Exception:
        pass
    # Apply theme
    _apply_theme(app, _load_theme_preference())
    # Apply language (requires compiled translations)
    _apply_language(app, _load_language_preference())

    w = MainWindow()
    # Standardfensterhöhe um ca. 15 % erhöhen (von 420 auf 483)
    w.resize(900, 483)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
