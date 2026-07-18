#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import asyncio
import json
import os
import threading
import warnings
from typing import Any, Callable, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from credentials import get_telegram_credentials, save_telegram_credentials, get_provider_api_key_source

# provider_combo-Wert (ui/app.py) -> Provider-Id in credentials.py
_TRANSLATION_PROVIDER_TO_CREDENTIALS_KEY = {"deepl": "deepl", "google": "google", "chatgpt": "openai"}

warnings.filterwarnings(
    "ignore",
    message=".*NVIDIA GeForce GT 1030.*not compatible with the current PyTorch installation.*",
    category=UserWarning,
)

from PySide6.QtCore import QObject, QThread, Signal, Qt, QLocale, QTranslator, QEvent, QUrl, QStandardPaths
from PySide6.QtGui import QAction, QActionGroup, QIcon, QDesktopServices, QGuiApplication
from functools import partial
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QFileDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QGroupBox,
    QPushButton, QLabel, QLineEdit,
    QTabWidget, QCheckBox, QMessageBox, QComboBox, QProgressBar,
    QInputDialog
)

from pipeline.runner_schedule import run_schedule
from pipeline.runner_base_imports import ScheduleCancelled, TelegramSessionInvalid
from ui.login_dialog import LoginDialog
from ui.api_keys_dialog import ApiKeysDialog
from pipeline.logging_setup import get_logger

logger = get_logger(__name__)
from ui.lettermap_tab import LettermapTab
from ui.schedule_editor_tab import ScheduleEditorTab
from ui.no_translate_words_tab import NoTranslateWordsTab

TRANSLATIONS_DIR = Path(__file__).parent / "translations"

APP_NAME = "TME"
ORG_NAME = "MiSte"  # beliebig, aber fix lassen für stabile Pfade

_CONFIG_DIR: Path | None = None

def _config_dir() -> Path:
    global _CONFIG_DIR
    if _CONFIG_DIR is None:
        if QApplication.instance() is None:
            raise RuntimeError("_config_dir() called before QApplication exists")
        _CONFIG_DIR = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return _CONFIG_DIR

def _ui_state_file() -> Path:
    return _config_dir() / "ui_state.json"

def _theme_state_file() -> Path:
    return _config_dir() / "ui_theme.json"

def _lang_state_file() -> Path:
    return _config_dir() / "ui_lang.json"

class ScheduleWorker(QObject):
    finished = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    session_invalid = Signal(str)
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
        output_format: str = "odt",
        chronological_merge: bool = False,
        translation_provider: str = "telegram",
        incremental_mode: bool = False,
        layout: str = "linear",
        cancel_event: threading.Event | None = None,
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
        self.output_format = output_format
        self.chronological_merge = chronological_merge
        self.translation_provider = translation_provider
        self.incremental_mode = incremental_mode
        self.layout = layout
        self._cancel_event = cancel_event

    def run(self) -> None:
        try:
            def _cb(msg: str) -> None:
                self.status.emit(msg)

            def _wait_for_mapping() -> None:
                self.waiting_for_mapping.emit()
                self._mapping_event.clear()
                while not self._mapping_event.wait(timeout=0.5):
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        raise ScheduleCancelled("Abgebrochen während des Wartens auf die Lettermap-Zuordnung.")
                self.status.emit(self.tr("Fortsetze nach Mapping…"))

            # Lettermap toggling via runner_by_ids globals (no config merge needed)
            try:
                import pipeline.runner_by_ids as _rbi_cfg
                _rbi_cfg._LM_IN_ORIGINAL = bool(self.lettermap_enabled)
                _rbi_cfg._LM_SCOPE = "all" if self.lettermap_enabled else "none"
                _rbi_cfg._LM_OPEN_UI_ON_MISSING = False
            except Exception:
                pass
            kwargs: dict[str, Any] = {
                "schedule_path": self.schedule_path,
                "out_basename": self.schedule_path.stem,
                "output_dir": Path("output"),
                "translate": self.translate,
                "translation_mode": self.translation_mode,
                "target_lang": self.target_lang,
                "include_images": self.include_images,
                "include_emojis": self.include_emojis,
                "source_lang": self.source_lang,
                "output_format": self.output_format,
                "chronological_merge": self.chronological_merge,
                "translation_provider": self.translation_provider,
                "incremental_mode": self.incremental_mode,
                "layout": self.layout,
                "config_path": Path("config.yaml"),
                "progress_cb": cast(Callable[[str], None], _cb),
                "skip_lettermap_ui": True,
                "cancel_event": self._cancel_event,
            }
            if self.lettermap_enabled:
                kwargs["wait_for_mapping_cb"] = cast(Callable[[], None], _wait_for_mapping)
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
        except ScheduleCancelled:
            self.cancelled.emit()
        except TelegramSessionInvalid as exc:
            self.session_invalid.emit(str(exc))
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

        self.cb_translate = QCheckBox(self.tr("Übersetzen"))
        self.mode_combo = QComboBox(); self.mode_combo.addItems(["inline", "end", "separate"])
        self.lbl_provider = QLabel(self.tr("Übersetzungs-Provider:"))
        self.lbl_provider.setWordWrap(True)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem(self.tr("Telegram (kostenlos)"), "telegram")
        self.provider_combo.addItem("DeepL", "deepl")
        self.provider_combo.addItem("Google Translate", "google")
        self.provider_combo.addItem("ChatGPT (OpenAI)", "chatgpt")
        # Quellsprachen-Auswahl für Dateiname (entspricht Sprachleiste)
        self.src_lang_combo = QComboBox(); self.src_lang_combo.addItems(["de", "en", "fr", "it", "ru", "pl", "es", "hr", "nl", "fi"])
        self.lang_edit = QLineEdit(); self.lang_edit.setPlaceholderText("de")
        self.cb_images = QCheckBox(self.tr("Bilder einbetten")); self.cb_images.setChecked(True)
        self.cb_emojis = QCheckBox(self.tr("Custom Emojis einbetten")); self.cb_emojis.setChecked(True)
        self.cb_lettermap = QCheckBox(self.tr("Lettermapping aktivieren")); self.cb_lettermap.setChecked(False)
        self.cb_interleave = QCheckBox(self.tr("Kanäle chronologisch mischen")); self.cb_interleave.setChecked(False)
        self.cb_incremental = QCheckBox(self.tr("Inkrementelles Update (Store)")); self.cb_incremental.setChecked(False)
        self.lbl_mode = QLabel(self.tr("Modus:"))
        self.lbl_lang = QLabel(self.tr("Sprache:"))
        self.lbl_src_lang = QLabel(self.tr("Quellsprache (Dateiname):"))
        self.lbl_src_lang.setWordWrap(True)
        self.lbl_format = QLabel(self.tr("Ausgabeformat:"))
        self.format_combo = QComboBox()
        self.format_combo.addItem(self.tr("Nur ODT"), "odt")
        self.format_combo.addItem(self.tr("Nur DOCX"), "docx")
        self.format_combo.addItem(self.tr("ODT + DOCX"), "both")
        self.lbl_layout = QLabel(self.tr("Layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItem(self.tr("Linear"), "linear")
        self.layout_combo.addItem(self.tr("Übersetzung neben Original"), "side_by_side")

        # Optionsfelder thematisch in QGroupBox-Bereiche mit QFormLayout gruppiert,
        # statt alle ~15 Felder in einer einzigen QHBoxLayout-Zeile aneinanderzureihen
        # (das hatte zuvor eine sehr breite minimumSizeHint erzwungen). Jede
        # Formular-Zeile ist nur so breit wie ihr eigener Inhalt.
        self.group_translation = QGroupBox(self.tr("Übersetzung"))
        form_translation = QFormLayout(self.group_translation)
        form_translation.addRow(self.cb_translate)
        form_translation.addRow(self.lbl_mode, self.mode_combo)
        form_translation.addRow(self.lbl_provider, self.provider_combo)
        form_translation.addRow(self.lbl_src_lang, self.src_lang_combo)
        form_translation.addRow(self.lbl_lang, self.lang_edit)
        # Layout (side_by_side) ist nur sinnvoll/wirksam, wenn überhaupt übersetzt
        # wird - deshalb hier statt in "Ausgabe" und an cb_translate gekoppelt
        # (siehe _on_translate_toggled).
        form_translation.addRow(self.lbl_layout, self.layout_combo)

        self.group_output = QGroupBox(self.tr("Ausgabe"))
        form_output = QFormLayout(self.group_output)
        form_output.addRow(self.lbl_format, self.format_combo)
        form_output.addRow(self.cb_interleave)
        form_output.addRow(self.cb_incremental)

        self.group_content = QGroupBox(self.tr("Inhalt"))
        form_content = QFormLayout(self.group_content)
        form_content.addRow(self.cb_images)
        form_content.addRow(self.cb_emojis)
        form_content.addRow(self.cb_lettermap)

        opt_grid = QGridLayout()
        opt_grid.setSpacing(8)
        opt_grid.addWidget(self.group_translation, 0, 0, 2, 1)
        opt_grid.addWidget(self.group_output, 0, 1)
        opt_grid.addWidget(self.group_content, 1, 1)
        lay.addLayout(opt_grid)

        run_lay = QHBoxLayout()
        run_lay.setSpacing(8)
        self.btn_run = QPushButton(self.tr("Schedule → ODT erzeugen"))
        self.btn_run.clicked.connect(self.run_schedule_file)
        run_lay.addWidget(self.btn_run)
        self.btn_cancel = QPushButton(self.tr("Abbrechen"))
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_cancel.setVisible(False)
        run_lay.addWidget(self.btn_cancel)
        self.btn_login = QPushButton(self.tr("Jetzt einloggen…"))
        self.btn_login.clicked.connect(self._on_login_clicked)
        self.btn_login.setVisible(False)
        run_lay.addWidget(self.btn_login)
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
        self._cancel_event = threading.Event()
        self.lettermap_tab: LettermapTab | None = None
        self._loading_state = False
        self._last_output_path: Path | None = None
 
        lay.addStretch()
 
        self._load_state()
        self._install_state_handlers()
        self._on_translate_toggled(self.cb_translate.isChecked())

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
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return False
        return self._prompt_store_credentials()

    def _ensure_provider_api_key(self, provider: str) -> bool:
        """Warnt vor Start eines Laufs, falls für den gewählten Übersetzungs-
        Provider kein API-Key hinterlegt ist (weder ENV noch Keyring noch
        credentials.json) - statt erst mitten im Lauf mit einem API-Fehler zu
        scheitern. "telegram" braucht keinen eigenen Key und ist immer ok."""
        key_provider = _TRANSLATION_PROVIDER_TO_CREDENTIALS_KEY.get(provider)
        if key_provider is None:
            return True
        if get_provider_api_key_source(key_provider) != "none":
            return True
        ans = QMessageBox.question(
            self,
            self.tr("API-Key fehlt"),
            self.tr(
                "Für den gewählten Übersetzungs-Provider ist kein API-Key hinterlegt "
                "(weder Umgebungsvariable, OS-Keyring noch credentials.json). "
                "Jetzt API-Keys verwalten?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return False
        dialog = ApiKeysDialog(self)
        dialog.exec()
        return get_provider_api_key_source(key_provider) != "none"

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.LanguageChange:
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
        self.cb_interleave.setText(self.tr("Kanäle chronologisch mischen"))
        self.cb_incremental.setText(self.tr("Inkrementelles Update (Store)"))
        self.lbl_provider.setText(self.tr("Übersetzungs-Provider:"))
        self.group_translation.setTitle(self.tr("Übersetzung"))
        self.group_output.setTitle(self.tr("Ausgabe"))
        self.group_content.setTitle(self.tr("Inhalt"))
        _prov_current = self.provider_combo.currentData()
        self.provider_combo.setItemText(0, self.tr("Telegram (kostenlos)"))
        if _prov_current is not None:
            _pidx = self.provider_combo.findData(_prov_current)
            if _pidx >= 0:
                self.provider_combo.setCurrentIndex(_pidx)
        self.lbl_format.setText(self.tr("Ausgabeformat:"))
        _fmt_current = self.format_combo.currentData()
        self.format_combo.setItemText(0, self.tr("Nur ODT"))
        self.format_combo.setItemText(1, self.tr("Nur DOCX"))
        self.format_combo.setItemText(2, self.tr("ODT + DOCX"))
        if _fmt_current is not None:
            _idx = self.format_combo.findData(_fmt_current)
            if _idx >= 0:
                self.format_combo.setCurrentIndex(_idx)
        self.lbl_layout.setText(self.tr("Layout:"))
        _layout_current = self.layout_combo.currentData()
        self.layout_combo.setItemText(0, self.tr("Linear"))
        self.layout_combo.setItemText(1, self.tr("Übersetzung neben Original"))
        if _layout_current is not None:
            _lidx = self.layout_combo.findData(_layout_current)
            if _lidx >= 0:
                self.layout_combo.setCurrentIndex(_lidx)
        self.btn_run.setText(self.tr("Telegram-Export → ODT erzeugen"))
        self.btn_cancel.setText(self.tr("Abbrechen"))
        self.btn_login.setText(self.tr("Jetzt einloggen…"))
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
        provider_val = str(self.provider_combo.currentData() or "telegram")
        if translate and not self._ensure_provider_api_key(provider_val):
            return
        target_lang = self.lang_edit.text().strip() or ("de" if translate else "de")
        source_lang = self.src_lang_combo.currentText().strip() or "de"
        self.btn_run.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setVisible(True)
        self.status_label.setText(self.tr("Starte…"))
        self.btn_continue.setVisible(False)
        self._mapping_event.clear()
        self._cancel_event = threading.Event()
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.btn_login.setVisible(False)
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
            output_format=str(self.format_combo.currentData() or "odt"),
            chronological_merge=self.cb_interleave.isChecked(),
            translation_provider=str(self.provider_combo.currentData() or "telegram"),
            incremental_mode=self.cb_incremental.isChecked(),
            layout=str(self.layout_combo.currentData() or "linear"),
            cancel_event=self._cancel_event,
        )
        self.worker_thread = QThread(self)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.error.connect(self._on_worker_error)
        self.worker.cancelled.connect(self._on_worker_cancelled)
        self.worker.session_invalid.connect(self._on_session_invalid)
        self.worker.status.connect(self._on_worker_status)
        self.worker.waiting_for_mapping.connect(self._on_waiting_for_mapping)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker_thread.quit)
        self.worker.cancelled.connect(self.worker_thread.quit)
        self.worker.session_invalid.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.worker.deleteLater)
        self.worker.cancelled.connect(self.worker.deleteLater)
        self.worker.session_invalid.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._on_thread_finished)
        self.worker_thread.start()
        self._save_state()

    def _on_cancel_clicked(self) -> None:
        logger.info("Nutzer hat Abbruch des laufenden Schedule-Laufs angefordert.")
        self.btn_cancel.setEnabled(False)
        self.status_label.setVisible(True)
        self.status_label.setText(self.tr("Breche ab…"))
        self._cancel_event.set()

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
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.btn_login.setVisible(False)
        self.btn_continue.setVisible(False)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()
        main_out: Path | None = None
        odt_path = getattr(result, "odt_path", None)
        odt_translation_path = getattr(result, "odt_translation_path", None)
        docx_path = getattr(result, "docx_path", None)
        docx_translation_path = getattr(result, "docx_translation_path", None)
        docx_error = getattr(result, "docx_error", None)
        translation_cost_summary = getattr(result, "translation_cost_summary", None)
        lines: list[str] = []
        if odt_path is not None:
            try:
                main_out = Path(str(odt_path))
            except Exception:
                main_out = None
            lines.append(self.tr("ODT erzeugt: {path}").format(path=odt_path))
        else:
            # Rückwärtskompatibler Fallback, falls result kein ScheduleRunResult ist.
            try:
                main_out = Path(str(result)) if result else None
            except Exception:
                main_out = None
            lines.append(self.tr("ODT erzeugt: {path}").format(path=result))
        if odt_translation_path:
            lines.append(self.tr("Übersetzungs-ODT erzeugt: {path}").format(path=odt_translation_path))
        if docx_path:
            lines.append(self.tr("DOCX erzeugt: {path}").format(path=docx_path))
        if docx_translation_path:
            lines.append(self.tr("Übersetzungs-DOCX erzeugt: {path}").format(path=docx_translation_path))
        if docx_error:
            lines.append(self.tr("Warnung: DOCX-Konvertierung fehlgeschlagen: {err}").format(err=docx_error))
        if translation_cost_summary:
            lines.append(self.tr("Übersetzungskosten (Schätzung):"))
            for line in translation_cost_summary:
                lines.append(f"  {line}")
        msg = "\n".join(lines)
        self.status_label.setText(self.tr("Fertig."))
        # Merke Ausgabe-Pfad und zeige Button
        self._last_output_path = main_out
        self.btn_open_output.setVisible(True)
        self.btn_open_output.setEnabled(True)
        title = self.tr("Fertig (mit Warnung)") if docx_error else self.tr("Fertig")
        if docx_error:
            QMessageBox.warning(self, title, msg)
        else:
            QMessageBox.information(self, title, msg)
        self.progress.setVisible(False)

    def _on_worker_error(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.btn_login.setVisible(False)
        self.status_label.setVisible(True)
        self.status_label.setText(self.tr("Fehler: ") + message)
        QMessageBox.critical(self, self.tr("Fehler"), message)
        self.btn_continue.setVisible(False)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()

    def _on_worker_cancelled(self) -> None:
        logger.info("Schedule-Lauf abgebrochen (Bestätigung vom Worker).")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.btn_login.setVisible(False)
        self.status_label.setVisible(True)
        self.status_label.setText(self.tr("Lauf abgebrochen."))
        QMessageBox.information(self, self.tr("Abgebrochen"), self.tr("Der Lauf wurde abgebrochen."))
        self.btn_continue.setVisible(False)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()

    def _on_session_invalid(self, message: str) -> None:
        logger.warning("Telegram-Session ungültig gemeldet: %s", message)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.status_label.setVisible(True)
        self.status_label.setText(message)
        self.btn_login.setVisible(True)
        self.btn_login.setEnabled(True)
        self.btn_continue.setVisible(False)
        self._mapping_event.set()
        if self.lettermap_tab:
            self.lettermap_tab.on_mapping_finished()

    def _on_login_clicked(self) -> None:
        dialog = LoginDialog(self)
        dialog.exec()
        if dialog.login_result is not None:
            self.btn_login.setVisible(False)
            self.status_label.setVisible(True)
            self.status_label.setText(
                self.tr("Login erfolgreich - du kannst den Lauf jetzt erneut starten.")
            )

    def _on_thread_finished(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.worker_thread = None
        self.worker = None
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(False)
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

    def _on_translate_toggled(self, checked: bool) -> None:
        # Layout "Übersetzung neben Original" (side_by_side) ist nur relevant/
        # wirksam, wenn überhaupt übersetzt wird.
        self.lbl_layout.setEnabled(checked)
        self.layout_combo.setEnabled(checked)

    def _install_state_handlers(self) -> None:
        self.schedule_edit.editingFinished.connect(self._save_state)
        self.cb_translate.toggled.connect(lambda _checked: self._save_state())
        self.cb_translate.toggled.connect(self._on_translate_toggled)
        self.mode_combo.currentTextChanged.connect(lambda _text: self._save_state())
        self.src_lang_combo.currentTextChanged.connect(lambda _text: self._save_state())
        self.lang_edit.editingFinished.connect(self._save_state)
        self.cb_images.toggled.connect(lambda _checked: self._save_state())
        self.cb_emojis.toggled.connect(lambda _checked: self._save_state())
        self.cb_lettermap.toggled.connect(lambda _checked: self._save_state())
        self.cb_interleave.toggled.connect(lambda _checked: self._save_state())
        self.cb_incremental.toggled.connect(lambda _checked: self._save_state())
        self.format_combo.currentIndexChanged.connect(lambda _i: self._save_state())
        self.provider_combo.currentIndexChanged.connect(lambda _i: self._save_state())
        self.layout_combo.currentIndexChanged.connect(lambda _i: self._save_state())

    def _load_state(self) -> None:
        self._loading_state = True
        try:
            p = _ui_state_file()
            data: dict = {}
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
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
            output_format = data.get("output_format")
            if isinstance(output_format, str):
                idx = self.format_combo.findData(output_format)
                if idx >= 0:
                    self.format_combo.setCurrentIndex(idx)
            interleave = data.get("chronological_merge")
            if isinstance(interleave, bool):
                self.cb_interleave.setChecked(interleave)
            provider = data.get("translation_provider")
            if isinstance(provider, str):
                pidx = self.provider_combo.findData(provider)
                if pidx >= 0:
                    self.provider_combo.setCurrentIndex(pidx)
            incremental = data.get("incremental_mode")
            if isinstance(incremental, bool):
                self.cb_incremental.setChecked(incremental)
            layout_val = data.get("layout")
            if isinstance(layout_val, str):
                lidx = self.layout_combo.findData(layout_val)
                if lidx >= 0:
                    self.layout_combo.setCurrentIndex(lidx)
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
            "output_format": self.format_combo.currentData(),
            "chronological_merge": self.cb_interleave.isChecked(),
            "translation_provider": self.provider_combo.currentData(),
            "incremental_mode": self.cb_incremental.isChecked(),
            "layout": self.layout_combo.currentData(),
        }
        try:
            p = _ui_state_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Telegram → ODT mit Emoji & Übersetzung")
        # Verhindert, dass das Fenster kleiner als sinnvoll nutzbar wird, ohne eine
        # größere Mindestgröße als nötig zu erzwingen (siehe auch die QGroupBox-
        # Gruppierung in ScheduleTab, die die eigentliche Ursache der übergroßen
        # minimumSizeHint behebt).
        self.setMinimumSize(700, 400)
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
        self.no_translate_words_tab = NoTranslateWordsTab()
        # Reorder: Schedule, Schedule-Editor, Lettermap (Experimentell), Ausnahmeliste
        self.tabs.addTab(self.schedule_tab, self.tr("Telegram-Export"))
        self.tabs.addTab(self.editor_tab, self.tr("Schedule-Editor"))
        self.tabs.addTab(self.lettermap_tab, self.tr("Lettermap (Experimentell)"))
        self.tabs.addTab(self.no_translate_words_tab, self.tr("Nicht übersetzen"))
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
            last_out = getattr(self, "_last_output_path", None)
            if last_out and Path(str(last_out)).exists():
                target = Path(str(last_out)).parent
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

    def _on_manage_api_keys(self) -> None:
        dialog = ApiKeysDialog(self)
        dialog.exec()

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
        if event.type() == QEvent.Type.LanguageChange:
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
        if self.tabs.count() >= 4:
            self.tabs.setTabText(3, self.tr("Nicht übersetzen"))
        # Menüs
        self.view_menu.setTitle(self.tr("Ansicht"))
        if hasattr(self, "settings_menu"):
            self.settings_menu.setTitle(self.tr("Einstellungen"))
            if hasattr(self, "action_api_keys"):
                self.action_api_keys.setText(self.tr("API-Keys verwalten…"))
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
        lang_menu_obj = getattr(self, "lang_menu", None)
        if lang_menu_obj is not None:
            lang_menu_obj.setTitle(self.tr("Sprache"))
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
        if hasattr(self, "no_translate_words_tab") and hasattr(self.no_translate_words_tab, "retranslate"):
            self.no_translate_words_tab.retranslate()

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
        # Einstellungen
        self.settings_menu = menubar.addMenu(self.tr("Einstellungen"))
        self.action_api_keys = QAction(self.tr("API-Keys verwalten…"), self)
        self.action_api_keys.triggered.connect(self._on_manage_api_keys)
        self.settings_menu.addAction(self.action_api_keys)
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
        p = _theme_state_file()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            t = str(data.get("theme", "dark")).lower()
            return "light" if t == "light" else "dark"
    except Exception:
        pass
    return "dark"


def _save_theme_preference(theme: str) -> None:
    try:
        p = _theme_state_file()
        data = {}
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8")) or {}
        data["theme"] = theme
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _set_theme(theme: str) -> None:
    app = QApplication.instance()
    if app is None or not isinstance(app, QApplication):
        return
    _apply_theme(cast(QApplication, app), theme)
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
        p = _lang_state_file()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
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
        p = _lang_state_file()
        data = {"lang": lang}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _set_language(lang: str) -> None:
    app = QApplication.instance()
    if app is None or not isinstance(app, QApplication):
        return
    _save_language_preference(lang)
    _apply_language(cast(QApplication, app), lang)
    # Retranslate top-level MainWindow(s)
    for w in cast(QApplication, app).topLevelWidgets():
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
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)

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
    # Explizit auf dem primären Bildschirm zentrieren statt sich auf das
    # Qt-Standardverhalten zu verlassen, das bei Multi-Monitor-Setups
    # inkonsistent sein kann (Fenster über zwei Bildschirme verteilt o.ä.).
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        x = avail.x() + (avail.width() - w.width()) // 2
        y = avail.y() + (avail.height() - w.height()) // 2
        w.move(max(avail.x(), x), max(avail.y(), y))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
