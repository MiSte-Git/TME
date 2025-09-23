#!/usr/bin/env python3
from __future__ import annotations
import sys
import asyncio
from pathlib import Path
import json
import os
# Ensure project root on sys.path when running from ui/ directly
try:
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
except Exception:
    pass

import threading

from PySide6.QtCore import QObject, QThread, Signal, Qt, QLocale, QTranslator, QEvent
from PySide6.QtGui import QAction, QActionGroup
from functools import partial
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QFileDialog,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTabWidget, QCheckBox, QMessageBox, QComboBox, QProgressBar,
    QInputDialog
)

from pipeline.runner_schedule import run_schedule
from ui.lettermap_tab import LettermapTab

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
                self.status.emit(self.tr("Fortsetze nach Mapping…"))

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
        self.lbl_schedule = QLabel(self.tr("Schedule:"))
        pick_lay.addWidget(self.lbl_schedule)
        pick_lay.addWidget(self.schedule_edit)
        pick_lay.addWidget(self.btn_pick)
        lay.addLayout(pick_lay)

        opt_lay = QHBoxLayout()
        opt_lay.setSpacing(8)
        self.cb_translate = QCheckBox(self.tr("Übersetzen"))
        self.mode_combo = QComboBox(); self.mode_combo.addItems(["inline", "end", "separate"])
        self.lang_edit = QLineEdit(); self.lang_edit.setPlaceholderText("de")
        self.cb_images = QCheckBox(self.tr("Bilder einbetten")); self.cb_images.setChecked(True)
        self.cb_emojis = QCheckBox(self.tr("Custom Emojis einbetten")); self.cb_emojis.setChecked(True)
        self.lbl_mode = QLabel(self.tr("Modus:"))
        self.lbl_lang = QLabel(self.tr("Sprache:"))
        opt_lay.addWidget(self.cb_translate)
        opt_lay.addWidget(self.lbl_mode)
        opt_lay.addWidget(self.mode_combo)
        opt_lay.addWidget(self.lbl_lang)
        opt_lay.addWidget(self.lang_edit)
        opt_lay.addWidget(self.cb_images)
        opt_lay.addWidget(self.cb_emojis)
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

        self.worker_thread: QThread | None = None
        self.worker: ScheduleWorker | None = None
        self._mapping_event = threading.Event()
        self.lettermap_tab: LettermapTab | None = None
        self._loading_state = False

        lay.addStretch()

        self._load_state()
        self._install_state_handlers()

    def _credentials_present(self) -> bool:
        if os.environ.get("TELEGRAM_API_ID") and os.environ.get("TELEGRAM_API_HASH"):
            return True
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        cand = [
            Path(xdg) / "telegram-odt" / "credentials.json",
            Path(xdg) / "telegram-odt" / "credentials.yaml",
            Path(xdg) / "telegram-odt" / "credentials.yml",
            Path(xdg) / "telegram-odt" / "credentials.env",
            Path(xdg) / "telegram-odt.env",
        ]
        return any(p.exists() for p in cand)

    def _prompt_store_credentials(self) -> bool:
        api_id, ok1 = QInputDialog.getText(self, self.tr("Telegram API"), self.tr("API ID (my.telegram.org):"))
        if not ok1 or not api_id.strip():
            return False
        api_hash, ok2 = QInputDialog.getText(self, self.tr("Telegram API"), self.tr("API Hash (my.telegram.org):"))
        if not ok2 or not api_hash.strip():
            return False
        try:
            xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
            cred_dir = Path(xdg) / "telegram-odt"
            cred_dir.mkdir(parents=True, exist_ok=True)
            cred_file = cred_dir / "credentials.json"
            cred_file.write_text(json.dumps({"api_id": api_id.strip(), "api_hash": api_hash.strip()}, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                if os.name == "posix":
                    os.chmod(cred_file, 0o600)
            except Exception:
                pass
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
        self.lbl_schedule.setText(self.tr("Schedule:"))
        self.cb_translate.setText(self.tr("Übersetzen"))
        self.lbl_mode.setText(self.tr("Modus:"))
        self.lbl_lang.setText(self.tr("Sprache:"))
        self.cb_images.setText(self.tr("Bilder einbetten"))
        self.cb_emojis.setText(self.tr("Custom Emojis einbetten"))
        self.btn_run.setText(self.tr("Schedule → ODT erzeugen"))
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
            self.tr("Schedule auswählen"),
            str(Path.cwd() / "input"),
            self.tr("Schedule (*.json *.txt);;JSON (*.json);;Text (*.txt)")
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
        if isinstance(result, tuple):
            main_path, extra_path = result
            if extra_path:
                msg = self.tr("ODTs erzeugt: {main}\n{extra}").format(main=main_path, extra=extra_path)
            else:
                msg = self.tr("ODT erzeugt: {main}").format(main=main_path)
        else:
            msg = self.tr("ODT erzeugt: {path}").format(path=result)
        self.status_label.setText(self.tr("Fertig."))
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
        self.setWindowTitle("Telegram → ODT mit Emoji & Übersetzung")
        self.tabs = QTabWidget()
        self.schedule_tab = ScheduleTab()
        self.lettermap_tab = LettermapTab()
        self.schedule_tab.set_lettermap_tab(self.lettermap_tab)
        self.tabs.addTab(self.schedule_tab, self.tr("Schedule"))
        self.tabs.addTab(self.lettermap_tab, self.tr("Lettermap"))
        self.setCentralWidget(self.tabs)

        # Menü: Ansicht → Theme und Sprache
        self._init_menus()
        
    def changeEvent(self, event) -> None:
        if event.type() == QEvent.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def retranslate(self) -> None:
        # Window title
        self.setWindowTitle(self.tr("Telegram → ODT mit Emoji & Übersetzung"))
        # Tabs
        self.tabs.setTabText(0, self.tr("Schedule"))
        self.tabs.setTabText(1, self.tr("Lettermap"))
        # Menüs
        self.view_menu.setTitle(self.tr("Ansicht"))
        self.lang_menu.setTitle(self.tr("Sprache"))
        self.action_light.setText(self.tr("Hell"))
        self.action_dark.setText(self.tr("Dunkel"))
        self.action_lang_de.setText(self.tr("Deutsch"))
        self.action_lang_en.setText(self.tr("Englisch"))
        # Weiterreichen an Tabs
        if hasattr(self.schedule_tab, "retranslate"):
            self.schedule_tab.retranslate()
        if hasattr(self.lettermap_tab, "retranslate"):
            self.lettermap_tab.retranslate()

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
        # Sprache
        self.lang_menu = menubar.addMenu(self.tr("Sprache"))
        group_lang = QActionGroup(self)
        group_lang.setExclusive(True)
        # Endonyme Labels
        languages = [
            ("de", "Deutsch"),
            ("en", "English"),
            ("fr", "Français"),
            ("it", "Italiano"),
            ("ru", "Русский"),
            ("pl", "Polski"),
            ("es", "Español"),
            ("hr", "Hrvatski"),
            ("nl", "Nederlands"),
            ("fi", "Suomi"),
        ]
        self.lang_actions: dict[str, QAction] = {}
        for code, label in languages:
            act = QAction(label, self, checkable=True)
            group_lang.addAction(act)
            self.lang_menu.addAction(act)
            act.triggered.connect(partial(_set_language, code))
            self.lang_actions[code] = act
        cur_lang = _load_language_preference()
        self.lang_actions.get(cur_lang, self.lang_actions["de"]).setChecked(True)


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
    w.resize(900, 420)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
