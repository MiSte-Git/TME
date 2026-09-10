from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, QObject, QSettings, QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from pipeline.topic_copy import (
    RUNS_DIR, TopicCopyResult, TopicPreview, TopicResumeResult, copy_topic_messages,
    delete_verified_messages, inspect_topic, latest_complete_run,
    latest_incomplete_run, parse_topic_link, resume_topic_copy,
    replace_incomplete_run_selection, verify_deletion_candidates,
)
from pipeline.logging_setup import get_logger
from ui.message_boxes import show_copyable_error

LOCAL_TIMEZONE = ZoneInfo("Europe/Zurich")
logger = get_logger(__name__)


class HistoryComboBox(QComboBox):
    """Öffnet die Historie auch beim Klick in das editierbare Textfeld."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt override)
        if (
            watched is self.lineEdit()
            and event.type() == QEvent.Type.MouseButtonRelease
            and self.count() > 0
        ):
            QTimer.singleShot(0, self.showPopup)
            return True
        return super().eventFilter(watched, event)


class ContainedWheelListWidget(QListWidget):
    """Verhindert, dass Mausrad-Events am Listenrand den Tab scrollen."""

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt override)
        scrollbar = self.verticalScrollBar()
        if scrollbar.maximum() > scrollbar.minimum():
            super().wheelEvent(event)
        # Auch am Anfang/Ende oder bei kurzer Liste bewusst konsumieren.
        event.accept()


def _display_timestamp(date_iso: str) -> str:
    if not date_iso:
        return "Datum unbekannt"
    try:
        value = datetime.fromisoformat(date_iso)
        if value.tzinfo is not None:
            value = value.astimezone(LOCAL_TIMEZONE)
        return value.strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return "Datum unbekannt"


def _resize_list_to_contents(widget: QListWidget, max_rows: int = 8) -> None:
    """Passt die Listenhöhe an den Inhalt an und begrenzt sie mit Scrollbar."""
    visible_rows = max(1, min(widget.count(), max_rows))
    row_height = widget.sizeHintForRow(0) if widget.count() else -1
    if row_height <= 0:
        row_height = widget.fontMetrics().height() + 10
    height = visible_rows * row_height + 2 * widget.frameWidth() + 4
    widget.setFixedHeight(height)


class TopicWorker(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)
    status = Signal(str)

    def __init__(self, mode: str, **kwargs) -> None:
        super().__init__()
        self.mode = mode
        self.kwargs = kwargs
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            if self.mode == "inspect":
                result = asyncio.run(inspect_topic(
                    self.kwargs["source"], self.kwargs.get("target", ""),
                    progress=lambda done, total: self.progress.emit(done, total),
                ))
            elif self.mode == "copy":
                result = asyncio.run(copy_topic_messages(
                    self.kwargs["source"], self.kwargs["target"],
                    excluded_bot_ids=self.kwargs["excluded_bot_ids"],
                    selected_source_ids=self.kwargs["selected_source_ids"],
                    silent=self.kwargs["silent"],
                    progress=lambda done, total: self.progress.emit(done, total),
                    cancel_event=self.cancel_event,
                ))
            elif self.mode == "resume":
                result = asyncio.run(resume_topic_copy(
                    self.kwargs["run_file"],
                    progress=lambda done, total: self.progress.emit(done, total),
                    cancel_event=self.cancel_event,
                    status=lambda message: self.status.emit(message),
                ))
            elif self.mode == "verify":
                result = asyncio.run(verify_deletion_candidates(
                    self.kwargs["run_file"], self.kwargs["location"],
                ))
            elif self.mode == "delete":
                result = asyncio.run(delete_verified_messages(
                    self.kwargs["run_file"], self.kwargs["message_ids"],
                    self.kwargs["location"],
                ))
            else:
                raise RuntimeError("Unbekannte Aktion.")
            self.finished.emit(result)
        except Exception as exc:
            logger.exception(
                "Topic-Aktion '%s' fehlgeschlagen: %s", self.mode, exc,
            )
            self.error.emit(str(exc))


class TopicCopyTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.preview: TopicPreview | None = None
        self.run_file: Path | None = latest_complete_run()
        self.incomplete_run: Path | None = latest_incomplete_run()
        self.thread: QThread | None = None
        self.worker: TopicWorker | None = None
        self.worker_mode = ""
        self._close_after_worker = False
        self._message_items_by_id: dict[int, QListWidgetItem] = {}
        self._ambiguous_items_by_id: dict[int, QListWidgetItem] = {}

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_layout.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        info = QLabel(self.tr(
            "Hier werden ausgewählte Nachrichten kopiert. Die abschließende "
            "Prüfung und ein mögliches Rückgängigmachen erfolgen im eigenen "
            "Tab. Ein Topic selbst wird niemals gelöscht."
        ))
        info.setWordWrap(True)
        layout.addWidget(info)

        copy_box = QGroupBox(self.tr("1. Auswählen und kopieren"))
        copy_layout = QVBoxLayout(copy_box)
        form = QFormLayout()
        self.source_edit = HistoryComboBox()
        self.source_edit.setMinimumHeight(32)
        self.source_edit.lineEdit().setPlaceholderText("https://t.me/c/…/QUELL-TOPIC")
        self.target_edit = HistoryComboBox()
        self.target_edit.setMinimumHeight(32)
        self.target_edit.lineEdit().setPlaceholderText("https://t.me/c/…/ZIEL-TOPIC")
        self._load_topic_history(self.source_edit, "recentSources")
        self._load_topic_history(self.target_edit, "recentTargets")
        form.addRow(self.tr("Quell-Topic-Link:"), self.source_edit)
        form.addRow(self.tr("Ziel-Topic-Link:"), self.target_edit)
        copy_layout.addLayout(form)
        self.load_button = QPushButton(self.tr("Nachrichten und Bots laden"))
        self.load_button.setMinimumHeight(32)
        self.load_button.clicked.connect(self.inspect)
        copy_layout.addWidget(self.load_button)
        self.load_progress = QProgressBar()
        self.load_progress.setRange(0, 1)
        self.load_progress.setValue(0)
        copy_layout.addWidget(self.load_progress)
        self.load_status = QLabel(self.tr("Bereit zum Laden."))
        self.load_status.setWordWrap(True)
        copy_layout.addWidget(self.load_status)
        copy_layout.addWidget(self._section_label(
            self.tr("Bots (Anfragen und Antworten gemeinsam)")
        ))
        self.bot_list = ContainedWheelListWidget()
        _resize_list_to_contents(self.bot_list, max_rows=6)
        self.bot_list.setToolTip(self.tr(
            "Haken gesetzt: Anfragen und Antworten dieses Bots werden kopiert."
        ))
        copy_layout.addWidget(self.bot_list)
        self.bot_list.itemChanged.connect(self._on_bot_item_changed)
        self.bot_count_label = QLabel(self.tr("0 von 0 Bots ausgewählt."))
        copy_layout.addWidget(self.bot_count_label)
        copy_layout.addWidget(self._section_label(self.tr(
            "Nicht eindeutig zugeordnete /-Anfragen"
        )))
        self.ambiguous_list = ContainedWheelListWidget()
        _resize_list_to_contents(self.ambiguous_list, max_rows=8)
        self.ambiguous_list.setToolTip(self.tr(
            "Diese Anfragen beginnen mit '/', konnten aber keinem Bot sicher "
            "zugeordnet werden. Der Haken steuert dieselbe Nachricht wie in der "
            "vollständigen Liste darunter."
        ))
        self.ambiguous_list.itemChanged.connect(self._on_ambiguous_item_changed)
        copy_layout.addWidget(self.ambiguous_list)
        ambiguous_buttons = QHBoxLayout()
        self.ambiguous_all_button = QPushButton(self.tr("Alle auswählen"))
        self.ambiguous_all_button.clicked.connect(
            lambda: self._set_ambiguous_checks(True)
        )
        self.ambiguous_none_button = QPushButton(self.tr("Keine auswählen"))
        self.ambiguous_none_button.clicked.connect(
            lambda: self._set_ambiguous_checks(False)
        )
        ambiguous_buttons.addWidget(self.ambiguous_all_button)
        ambiguous_buttons.addWidget(self.ambiguous_none_button)
        ambiguous_buttons.addStretch(1)
        copy_layout.addLayout(ambiguous_buttons)
        self.ambiguous_count_label = QLabel(self.tr(
            "0 von 0 unklaren Anfragen ausgewählt."
        ))
        copy_layout.addWidget(self.ambiguous_count_label)
        copy_layout.addWidget(self._section_label(
            self.tr("Nachrichten, die kopiert werden")
        ))
        self.message_list = ContainedWheelListWidget()
        self.message_list.setUniformItemSizes(True)
        _resize_list_to_contents(self.message_list, max_rows=10)
        self.message_list.setToolTip(self.tr(
            "Nur Nachrichten mit gesetztem Haken werden kopiert. Die Bot-Auswahl "
            "oben kann zusammengehörige Anfragen und Antworten gesammelt umschalten."
        ))
        self.message_list.itemChanged.connect(self._on_message_item_changed)
        copy_layout.addWidget(self.message_list)
        self.message_count_label = QLabel(self.tr("0 von 0 Nachrichten ausgewählt."))
        copy_layout.addWidget(self.message_count_label)
        self.preview_label = QLabel(self.tr("Noch keine Nachrichten geladen."))
        self.preview_label.setWordWrap(True)
        copy_layout.addWidget(self.preview_label)
        self.silent_box = QCheckBox(self.tr("Ohne Benachrichtigungen kopieren"))
        self.silent_box.setChecked(True)
        self.silent_box.setToolTip(self.tr(
            "Die Nachrichten erscheinen vollständig im Ziel-Topic, lösen dort "
            "aber keine Pushmeldung und keinen Hinweiston für Mitglieder aus. "
            "Bei vielen Nachrichten sollte diese Option aktiviert bleiben."
        ))
        copy_layout.addWidget(self.silent_box)
        self.copy_button = QPushButton(self.tr("Auswahl kopieren"))
        self.copy_button.setMinimumHeight(32)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_or_cancel)
        copy_layout.addWidget(self.copy_button)
        self.copy_progress = QProgressBar()
        self.copy_progress.setRange(0, 1)
        self.copy_progress.setValue(0)
        copy_layout.addWidget(self.copy_progress)
        self.copy_count_label = QLabel(self.tr(
            "0 bestätigt, 0 noch offen."
        ))
        copy_layout.addWidget(self.copy_count_label)
        self.copy_status = QLabel(self.tr("Bereit zum Kopieren."))
        self.copy_status.setWordWrap(True)
        copy_layout.addWidget(self.copy_status)
        self.copy_error_label = QLabel()
        self.copy_error_label.setWordWrap(True)
        self.copy_error_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.copy_error_label.setStyleSheet(
            "color: #ffb4ab; border: 1px solid #b3261e; padding: 8px;"
        )
        self.copy_error_label.setVisible(False)
        copy_layout.addWidget(self.copy_error_label)
        self.copy_error_button = QPushButton(self.tr("Fehler kopieren"))
        self.copy_error_button.setVisible(False)
        self.copy_error_button.clicked.connect(self._copy_last_error)
        copy_layout.addWidget(self.copy_error_button)
        layout.addWidget(copy_box)

        delete_box = QGroupBox(self.tr(
            "2. Bereich neu prüfen und Nachrichten zum Löschen auswählen"
        ))
        delete_layout = QVBoxLayout(delete_box)
        location_row = QFormLayout()
        self.delete_location = QComboBox()
        self.delete_location.addItem(self.tr("Quell-Topic"), "source")
        self.delete_location.addItem(self.tr("Ziel-Topic"), "target")
        self.delete_location.currentIndexChanged.connect(
            self._on_delete_location_changed
        )
        location_row.addRow(self.tr("Anzeigen und löschen in:"), self.delete_location)
        delete_layout.addLayout(location_row)
        order_hint = QLabel(self.tr(
            "Falls Nachrichten in beiden Topics gelöscht werden sollen: zuerst in "
            "der Quelle löschen und danach im Ziel. Für eine sichere Löschung in der "
            "Quelle muss die Zielkopie noch vorhanden sein."
        ))
        order_hint.setWordWrap(True)
        delete_layout.addWidget(order_hint)
        self.verify_button = QPushButton(self.tr(
            "Ausgewählten Bereich neu laden und prüfen"
        ))
        self.verify_button.setToolTip(self.tr(
            "Lädt den gewählten Bereich erneut. In der Liste erscheinen nur "
            "Nachrichten aus dem ausgewählten Quell- oder Ziel-Topic."
        ))
        self.verify_button.setMinimumHeight(32)
        self.verify_button.setEnabled(self.run_file is not None)
        self.verify_button.clicked.connect(self.verify)
        delete_layout.addWidget(self.verify_button)
        self.delete_progress = QProgressBar()
        self.delete_progress.setRange(0, 1)
        self.delete_progress.setValue(0)
        delete_layout.addWidget(self.delete_progress)
        self.delete_status = QLabel(self.tr("Bereit zum Prüfen."))
        self.delete_status.setWordWrap(True)
        delete_layout.addWidget(self.delete_status)
        self.delete_list = ContainedWheelListWidget()
        _resize_list_to_contents(self.delete_list, max_rows=10)
        self.delete_list.itemChanged.connect(self._update_delete_selection_count)
        delete_layout.addWidget(self.delete_list)
        self.delete_count_label = QLabel(self.tr("0 von 0 Nachrichten ausgewählt."))
        delete_layout.addWidget(self.delete_count_label)
        select_row = QHBoxLayout()
        self.select_all_button = QPushButton(self.tr("Alle markieren"))
        self.select_all_button.clicked.connect(lambda: self._set_delete_checks(True))
        self.select_none_button = QPushButton(self.tr("Keine markieren"))
        self.select_none_button.clicked.connect(lambda: self._set_delete_checks(False))
        select_row.addWidget(self.select_all_button)
        select_row.addWidget(self.select_none_button)
        select_row.addStretch(1)
        delete_layout.addLayout(select_row)
        self.delete_button = QPushButton(self.tr("Markierte Nachrichten löschen"))
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected)
        delete_layout.addWidget(self.delete_button)
        # Die bisherige integrierte Löschoberfläche bleibt vorerst als interne
        # Kompatibilitätsstruktur bestehen, wird aber durch den sicheren,
        # eigenständigen Rückgängig-Tab ersetzt.
        delete_box.setVisible(False)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            "font-weight: 600; margin-top: 10px; padding-top: 8px; "
            "border-top: 1px solid palette(mid);"
        )
        return label

    @staticmethod
    def _matching_incomplete_run(source: str, target: str) -> Path | None:
        if not target or not RUNS_DIR.exists():
            return None
        for path in sorted(RUNS_DIR.glob("topic-copy-*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("complete"):
                    continue
                if (
                    parse_topic_link(data["source_link"]) == parse_topic_link(source)
                    and parse_topic_link(data["target_link"]) == parse_topic_link(target)
                ):
                    return path
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return None

    def inspect(self) -> None:
        source = self._current_topic_link(self.source_edit)
        target = self._current_topic_link(self.target_edit)
        try:
            parse_topic_link(source)
            if target:
                parse_topic_link(target)
        except ValueError as exc:
            show_copyable_error(self, self.tr("Ungültiger Link"), str(exc))
            return
        self._prepare_copy_loading()
        self._start(TopicWorker(
            "inspect", source=source, target=target,
        ), self.tr("Nachrichten werden gelesen…"))

    def copy(self) -> None:
        if self.preview is None:
            return
        source = self._current_topic_link(self.source_edit)
        target = self._current_topic_link(self.target_edit)
        try:
            parse_topic_link(target)
        except ValueError as exc:
            show_copyable_error(self, self.tr("Ungültiger Link"), str(exc))
            return
        excluded = {
            int(self.bot_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.bot_list.count())
            if self.bot_list.item(i).data(Qt.ItemDataRole.UserRole) is not None
            and self.bot_list.item(i).checkState() != Qt.CheckState.Checked
        }
        selected_source_ids = {
            int(self.message_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.message_list.count())
            if self.message_list.item(i).checkState() == Qt.CheckState.Checked
        }
        if not selected_source_ids:
            show_copyable_error(
                self, self.tr("Keine Nachrichten ausgewählt"),
                self.tr("Bitte mindestens eine Nachricht zum Kopieren markieren."),
            )
            return
        if self.incomplete_run is not None:
            answer = QMessageBox.question(
                self, self.tr("Unterbrochenen Lauf fortsetzen?"),
                self.tr(
                    "Die aktuell sichtbare Auswahl ersetzt die frühere Auswahl. "
                    "Bestätigte Kopien werden übersprungen und abgewählte Nachrichten "
                    "nicht weiter kopiert. Fortsetzen?"
                ),
            )
            if answer == QMessageBox.StandardButton.Yes:
                try:
                    replace_incomplete_run_selection(
                        self.incomplete_run, source, target,
                        self.preview.messages, selected_source_ids,
                    )
                except (ValueError, OSError, json.JSONDecodeError) as exc:
                    show_copyable_error(
                        self, self.tr("Fortsetzen nicht möglich"), str(exc),
                    )
                    return
                self._start(TopicWorker(
                    "resume", run_file=self.incomplete_run,
                ), self.tr("Vorhandene Kopien werden abgeglichen…"))
            return
        answer = QMessageBox.question(
            self, self.tr("Auswahl kopieren?"),
            self.tr(
                "Die ausgewählten Nachrichten werden weitergeleitet. Im Anschluss "
                "wird noch nichts gelöscht. Jetzt kopieren?"
            ),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start(TopicWorker(
                "copy", source=source, target=target,
                excluded_bot_ids=excluded, silent=self.silent_box.isChecked(),
                selected_source_ids=selected_source_ids,
            ), self.tr("Nachrichten werden kopiert…"))

    def verify(self) -> None:
        if self.run_file is not None:
            self._prepare_delete_loading()
            location = str(self.delete_location.currentData())
            status = (
                self.tr("Zielkopien werden geprüft und Quellnachrichten neu geladen…")
                if location == "source"
                else self.tr("Nachrichten im Ziel-Topic werden neu geladen…")
            )
            self._start(TopicWorker(
                "verify", run_file=self.run_file, location=location,
            ), status)

    def delete_selected(self) -> None:
        selected = {
            int(self.delete_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.delete_list.count())
            if self.delete_list.item(i).checkState() == Qt.CheckState.Checked
        }
        if not selected or self.run_file is None:
            QMessageBox.information(self, self.tr("Keine Auswahl"), self.tr(
                "Bitte mindestens eine nachgewiesene Nachricht markieren."
            ))
            return
        answer = QMessageBox.question(
            self, self.tr("Nachrichten endgültig löschen?"),
            self.tr(
                "{count} markierte Nachrichten werden aus dem {location} gelöscht. "
                "Das Topic selbst bleibt bestehen. Dieser Schritt kann nicht rückgängig "
                "gemacht werden. Wirklich löschen?"
            ).format(
                count=len(selected),
                location=(
                    self.tr("Quell-Topic")
                    if self.delete_location.currentData() == "source"
                    else self.tr("Ziel-Topic")
                ),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start(TopicWorker(
                "delete", run_file=self.run_file, message_ids=selected,
                location=str(self.delete_location.currentData()),
            ), self.tr("Sicherheitsprüfung wird wiederholt…"))

    @staticmethod
    def _placeholder(text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        return item

    def _prepare_copy_loading(self) -> None:
        self.preview = None
        self._message_items_by_id.clear()
        self._ambiguous_items_by_id.clear()
        self.copy_button.setEnabled(False)
        self.bot_list.clear()
        self.bot_list.addItem(self._placeholder(self.tr("Bots werden geladen…")))
        self.ambiguous_list.clear()
        self.ambiguous_list.addItem(self._placeholder(self.tr("Anfragen werden geladen…")))
        self.message_list.clear()
        self.message_list.addItem(self._placeholder(self.tr("Nachrichten werden geladen…")))
        _resize_list_to_contents(self.bot_list, max_rows=6)
        _resize_list_to_contents(self.ambiguous_list, max_rows=8)
        _resize_list_to_contents(self.message_list, max_rows=10)
        self._update_bot_selection_count()
        self._update_ambiguous_selection_count()
        self._update_message_selection_status()

    def _prepare_delete_loading(self) -> None:
        self.delete_list.clear()
        self.delete_list.addItem(self._placeholder(self.tr("Nachrichten werden geprüft…")))
        _resize_list_to_contents(self.delete_list, max_rows=10)
        self._update_delete_selection_count()
        self.delete_button.setEnabled(False)

    def _progress(self) -> QProgressBar:
        if self.worker_mode in {"verify", "delete"}:
            return self.delete_progress
        if self.worker_mode == "inspect":
            return self.load_progress
        return self.copy_progress

    def _set_status(self, text: str) -> None:
        if self.worker_mode in {"verify", "delete"}:
            self.delete_status.setText(text)
        elif self.worker_mode == "inspect":
            self.load_status.setText(text)
        else:
            self.copy_status.setText(text)

    def _start(self, worker: TopicWorker, status: str) -> None:
        self.copy_error_label.setVisible(False)
        self.copy_error_button.setVisible(False)
        self._set_busy(True)
        self.worker_mode = worker.mode
        self._set_status(status)
        self._progress().setRange(0, 0)
        if worker.mode in {"copy", "resume"}:
            self.copy_button.setText(self.tr("Kopieren abbrechen"))
            self.copy_button.setEnabled(True)
        self.thread = QThread(self)
        self.worker = worker
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.status.connect(self._set_status)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        worker.finished.connect(self.thread.quit)
        worker.error.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup)
        self.thread.start()

    def _copy_or_cancel(self) -> None:
        if (
            self.worker is not None
            and self.worker_mode in {"copy", "resume"}
            and self.thread is not None
            and self.thread.isRunning()
        ):
            self._cancel_copy()
            return
        self.copy()

    def _cancel_copy(self) -> None:
        if self.worker is None or self.worker_mode not in {"copy", "resume"}:
            return
        self.worker.cancel()
        self.copy_button.setEnabled(False)
        self.copy_button.setText(self.tr("Abbruch angefordert…"))
        self.copy_status.setText(self.tr(
            "Abbruch angefordert… Der aktuelle Telegram-Block wird noch abgeschlossen."
        ))

    def request_safe_close(self) -> bool:
        """Bricht aktive Arbeit ab und meldet, wann das Fenster schließen darf."""
        if self.thread is None or not self.thread.isRunning():
            return True
        if self.worker is not None:
            self.worker.cancel()
        self.copy_button.setEnabled(False)
        self.copy_button.setText(self.tr("App wird beendet…"))
        self.copy_status.setText(self.tr(
            "App wird beendet… Die laufende Telegram-Anfrage wird noch "
            "kontrolliert abgeschlossen."
        ))
        if not self._close_after_worker:
            self._close_after_worker = True
            self.thread.finished.connect(self._close_parent_after_worker)
        return False

    def _close_parent_after_worker(self) -> None:
        self._close_after_worker = False
        window = self.window()
        QTimer.singleShot(0, window.close)

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._progress().setRange(0, 0)
            self._set_status(self.tr("{done} Nachrichten gelesen…").format(done=done))
            return
        self._progress().setRange(0, max(1, total))
        self._progress().setValue(done)
        if self.worker_mode in {"copy", "resume"}:
            self.copy_count_label.setText(self.tr(
                "{done} bestätigt, {remaining} noch offen "
                "({done} von {total})."
            ).format(
                done=done, remaining=max(0, total - done), total=total,
            ))
        else:
            self._set_status(self.tr(
                "{done} von {total} Nachrichten verarbeitet…"
            ).format(done=done, total=total))

    def _on_finished(self, result: object) -> None:
        if self.worker_mode == "inspect":
            source = self._current_topic_link(self.source_edit)
            target = self._current_topic_link(self.target_edit)
            self.incomplete_run = self._matching_incomplete_run(source, target)
            self._show_preview(result)
            self._remember_topic(
                self.source_edit, "recentSources", result.topic_title, source,
            )
            if target and result.target_title:
                self._remember_topic(
                    self.target_edit, "recentTargets", result.target_title, target,
                )
        elif self.worker_mode == "copy":
            assert isinstance(result, TopicCopyResult)
            self.run_file = result.run_file
            self.incomplete_run = None
            self.copy_button.setText(self.tr("Auswahl kopieren"))
            self._remember_topic(
                self.source_edit, "recentSources", result.source_title,
                self._current_topic_link(self.source_edit),
            )
            self._remember_topic(
                self.target_edit, "recentTargets", result.target_title,
                self._current_topic_link(self.target_edit),
            )
            self.delete_list.clear()
            _resize_list_to_contents(self.delete_list, max_rows=10)
            self._update_delete_selection_count()
            self.delete_button.setEnabled(False)
            self.verify_button.setEnabled(True)
            message = self.tr(
                "{count} Nachrichten kopiert. Nun im Ziel kontrollieren und danach "
                "Schritt 2 starten."
            ).format(count=result.copied)
            self._set_status(message)
            QMessageBox.information(self, self.tr("Kopieren abgeschlossen"), message)
        elif self.worker_mode == "resume":
            assert isinstance(result, TopicResumeResult)
            if result.remaining == 0:
                self.incomplete_run = None
                self.run_file = result.run_file
                self.copy_button.setText(self.tr("Auswahl kopieren"))
                self.verify_button.setEnabled(True)
                if result.uncopyable:
                    message = self.tr(
                        "Fortsetzung abgeschlossen: {confirmed} Nachrichten sind "
                        "bestätigt, {uncopyable} nicht unterstützte "
                        "Mediennachricht(en) wurden nicht ins Ziel kopiert. "
                        "Die App hat keine Quellnachrichten gelöscht."
                    ).format(
                        confirmed=result.total - result.uncopyable,
                        uncopyable=result.uncopyable,
                    )
                else:
                    message = self.tr(
                        "Fortsetzung abgeschlossen: alle {total} Nachrichten "
                        "sind bestätigt."
                    ).format(total=result.total)
            else:
                message = self.tr(
                    "Fortsetzung beendet: {done} von {total} bestätigt, "
                    "{remaining} noch offen."
                ).format(
                    done=result.total - result.remaining,
                    total=result.total, remaining=result.remaining,
                )
            self._set_status(message)
            QMessageBox.information(self, self.tr("Fortsetzung"), message)
        elif self.worker_mode == "verify":
            self._show_candidates(result)
        elif self.worker_mode == "delete":
            self._progress().setRange(0, 1)
            self._progress().setValue(1)
            message = self.tr("{count} Nachrichten gelöscht.").format(count=result)
            self._set_status(message)
            QMessageBox.information(self, self.tr("Löschen abgeschlossen"), message)
            self.delete_list.clear()
            _resize_list_to_contents(self.delete_list, max_rows=10)
            self._update_delete_selection_count()
            self.delete_button.setEnabled(False)

    def _show_preview(self, preview: TopicPreview) -> None:
        self.preview = preview
        self._message_items_by_id.clear()
        self._ambiguous_items_by_id.clear()
        self.bot_list.clear()
        self.ambiguous_list.clear()
        self.message_list.clear()
        for widget in (self.bot_list, self.ambiguous_list, self.message_list):
            widget.blockSignals(True)
            widget.setUpdatesEnabled(False)
        for group in preview.bot_groups:
            item = QListWidgetItem(self.tr(
                "{bot}: {requests} Anfrage(n), {responses} Antwort(en)"
            ).format(
                bot=group.bot_name, requests=len(group.request_ids),
                responses=len(group.response_ids),
            ))
            item.setData(Qt.ItemDataRole.UserRole, group.bot_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.bot_list.addItem(item)
        if not preview.bot_groups:
            item = QListWidgetItem(self.tr("Keine Bots gefunden."))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.bot_list.addItem(item)
        messages_by_id = {message.message_id: message for message in preview.messages}
        for message_id in preview.unassigned_command_ids:
            message = messages_by_id.get(message_id)
            if message is None:
                continue
            text = message.text.replace("\n", " ")[:120]
            timestamp = _display_timestamp(message.date_iso)
            item = QListWidgetItem(f"[{timestamp}] {message.sender_name}: {text}")
            item.setData(Qt.ItemDataRole.UserRole, message.message_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.ambiguous_list.addItem(item)
            self._ambiguous_items_by_id[message.message_id] = item
        if not preview.unassigned_command_ids:
            self.ambiguous_list.addItem(self._placeholder(self.tr(
                "Keine unklaren /-Anfragen gefunden."
            )))
        for message in preview.messages:
            text = message.text.replace("\n", " ")[:120] or self.tr("[Mediennachricht]")
            timestamp = _display_timestamp(message.date_iso)
            if message.original_date_iso:
                original = _display_timestamp(message.original_date_iso)
                prefix = self.tr("[Original: {original} | Kopiert: {copied}]").format(
                    original=original, copied=timestamp,
                )
            else:
                prefix = f"[{timestamp}]"
            item = QListWidgetItem(f"{prefix} {message.sender_name}: {text}")
            item.setData(Qt.ItemDataRole.UserRole, message.message_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.message_list.addItem(item)
            self._message_items_by_id[message.message_id] = item
        for widget in (self.bot_list, self.ambiguous_list, self.message_list):
            widget.setUpdatesEnabled(True)
            widget.blockSignals(False)
        _resize_list_to_contents(self.bot_list, max_rows=6)
        _resize_list_to_contents(self.ambiguous_list, max_rows=8)
        _resize_list_to_contents(self.message_list, max_rows=10)
        self._update_bot_selection_count()
        self._update_ambiguous_selection_count()
        self.preview_label.setText(self.tr(
            "{messages} Nachrichten, {bots} Bot(s), {unclear} nicht eindeutig "
            "zugeordnete /-Anfrage(n). Unklare Anfragen bleiben normale Nachrichten."
        ).format(
            messages=len(preview.messages), bots=len(preview.bot_groups),
            unclear=len(preview.unassigned_command_ids),
        ))
        self.copy_button.setText(
            self.tr("Aktuelle Auswahl sicher fortsetzen")
            if self.incomplete_run is not None
            else self.tr("Auswahl kopieren")
        )
        self.copy_button.setEnabled(True)
        self.load_progress.setRange(0, max(1, len(preview.messages)))
        self.load_progress.setValue(len(preview.messages))
        self.load_status.setText(self.tr(
            "{count} Nachrichten und Bots geladen."
        ).format(count=len(preview.messages)))
        self._update_message_selection_status()

    def _on_bot_item_changed(self, item: QListWidgetItem) -> None:
        if self.preview is None:
            return
        bot_id = item.data(Qt.ItemDataRole.UserRole)
        if bot_id is None:
            return
        group = next(
            (group for group in self.preview.bot_groups if group.bot_id == int(bot_id)),
            None,
        )
        if group is None:
            return
        affected_ids = set(group.message_ids)
        state = item.checkState()
        self.message_list.blockSignals(True)
        self.message_list.setUpdatesEnabled(False)
        try:
            for message_id in affected_ids:
                message_item = self._message_items_by_id.get(message_id)
                if message_item is not None:
                    message_item.setCheckState(state)
        finally:
            self.message_list.setUpdatesEnabled(True)
            self.message_list.blockSignals(False)
        self._update_bot_selection_count()
        self._update_message_selection_status()

    def _update_bot_selection_count(self) -> None:
        bot_items = [
            self.bot_list.item(index)
            for index in range(self.bot_list.count())
            if self.bot_list.item(index).data(Qt.ItemDataRole.UserRole) is not None
        ]
        selected = sum(
            item.checkState() == Qt.CheckState.Checked for item in bot_items
        )
        self.bot_count_label.setText(self.tr(
            "{selected} von {total} Bots ausgewählt."
        ).format(selected=selected, total=len(bot_items)))

    def _on_ambiguous_item_changed(self, item: QListWidgetItem) -> None:
        message_id = item.data(Qt.ItemDataRole.UserRole)
        if message_id is None:
            return
        self.message_list.blockSignals(True)
        try:
            message_item = self._message_items_by_id.get(int(message_id))
            if message_item is not None:
                message_item.setCheckState(item.checkState())
        finally:
            self.message_list.blockSignals(False)
        self._update_ambiguous_selection_count()
        self._update_message_selection_status()

    def _on_message_item_changed(self, item: QListWidgetItem) -> None:
        message_id = item.data(Qt.ItemDataRole.UserRole)
        if message_id is not None:
            self.ambiguous_list.blockSignals(True)
            try:
                ambiguous_item = self._ambiguous_items_by_id.get(int(message_id))
                if ambiguous_item is not None:
                    ambiguous_item.setCheckState(item.checkState())
            finally:
                self.ambiguous_list.blockSignals(False)
        self._update_ambiguous_selection_count()
        self._update_message_selection_status()

    def _update_ambiguous_selection_count(self) -> None:
        items = [
            self.ambiguous_list.item(index)
            for index in range(self.ambiguous_list.count())
            if self.ambiguous_list.item(index).data(Qt.ItemDataRole.UserRole) is not None
        ]
        selected = sum(item.checkState() == Qt.CheckState.Checked for item in items)
        self.ambiguous_count_label.setText(self.tr(
            "{selected} von {total} unklaren Anfragen ausgewählt."
        ).format(selected=selected, total=len(items)))

    def _set_ambiguous_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.ambiguous_list.blockSignals(True)
        self.message_list.blockSignals(True)
        self.ambiguous_list.setUpdatesEnabled(False)
        self.message_list.setUpdatesEnabled(False)
        try:
            for message_id, item in self._ambiguous_items_by_id.items():
                item.setCheckState(state)
                message_item = self._message_items_by_id.get(message_id)
                if message_item is not None:
                    message_item.setCheckState(state)
        finally:
            self.ambiguous_list.setUpdatesEnabled(True)
            self.message_list.setUpdatesEnabled(True)
            self.ambiguous_list.blockSignals(False)
            self.message_list.blockSignals(False)
        self._update_ambiguous_selection_count()
        self._update_message_selection_status()

    def _update_message_selection_status(self, _item=None) -> None:
        message_items = [
            self.message_list.item(index)
            for index in range(self.message_list.count())
            if self.message_list.item(index).data(Qt.ItemDataRole.UserRole) is not None
        ]
        selected = sum(item.checkState() == Qt.CheckState.Checked for item in message_items)
        self.message_count_label.setText(self.tr(
            "{selected} von {total} Nachrichten ausgewählt."
        ).format(selected=selected, total=len(message_items)))
        self.copy_status.setText(self.tr(
            "{selected} von {total} Nachrichten zum Kopieren ausgewählt."
        ).format(selected=selected, total=len(message_items)))

    def _show_candidates(self, candidates) -> None:
        self.delete_list.clear()
        for candidate in candidates:
            text = candidate.text.replace("\n", " ")[:100]
            if self.delete_location.currentData() == "source":
                timestamp_text = _display_timestamp(candidate.date_iso)
                message_id = candidate.source_id
                if candidate.original_date_iso and candidate.original_date_iso != candidate.date_iso:
                    original = _display_timestamp(candidate.original_date_iso)
                    prefix = self.tr(
                        "[Original: {original} | Kopiert: {copied}]"
                    ).format(original=original, copied=timestamp_text)
                else:
                    prefix = f"[{timestamp_text}]"
            else:
                original = _display_timestamp(
                    candidate.original_date_iso or candidate.date_iso
                )
                copied = _display_timestamp(candidate.target_date_iso)
                message_id = candidate.target_id
                prefix = self.tr("[Original: {original} | Kopiert: {copied}]").format(
                    original=original, copied=copied,
                )
                if candidate.possible_duplicate:
                    prefix = self.tr("[MÖGLICHE DOPPELKOPIE] {details}").format(
                        details=prefix,
                    )
            item = QListWidgetItem(f"{prefix} {candidate.sender_name}: {text}")
            item.setData(Qt.ItemDataRole.UserRole, message_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # Absichtlich nichts vorausgewählt: Löschen erfordert eine neue Auswahl.
            item.setCheckState(Qt.CheckState.Unchecked)
            self.delete_list.addItem(item)
        _resize_list_to_contents(self.delete_list, max_rows=10)
        self._update_delete_selection_count()
        self.delete_button.setEnabled(bool(candidates))
        if self.delete_location.currentData() == "source":
            status = self.tr(
                "{count} Zielkopien nachgewiesen. Angezeigt werden nur die zugehörigen "
                "Quellnachrichten; bitte die zu löschenden neu auswählen."
            )
        else:
            status = self.tr(
                "{count} Nachrichten im Ziel-Topic nachgewiesen; bitte die dort zu "
                "löschenden Nachrichten neu auswählen."
            )
        self.delete_status.setText(status.format(count=len(candidates)))
        self.delete_progress.setRange(0, max(1, len(candidates)))
        self.delete_progress.setValue(len(candidates))

    def _on_delete_location_changed(self, _index: int) -> None:
        self.delete_list.clear()
        _resize_list_to_contents(self.delete_list, max_rows=10)
        self._update_delete_selection_count()
        self.delete_button.setEnabled(False)
        self.delete_status.setText(self.tr(
            "Ansicht geändert. Bitte die Nachrichten für diesen Bereich neu laden."
        ))

    def _set_delete_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.delete_list.count()):
            self.delete_list.item(i).setCheckState(state)

    def _update_delete_selection_count(self, _item=None) -> None:
        message_items = [
            self.delete_list.item(index)
            for index in range(self.delete_list.count())
            if self.delete_list.item(index).data(Qt.ItemDataRole.UserRole) is not None
        ]
        selected = sum(item.checkState() == Qt.CheckState.Checked for item in message_items)
        self.delete_count_label.setText(self.tr(
            "{selected} von {total} Nachrichten ausgewählt."
        ).format(selected=selected, total=len(message_items)))

    def _on_error(self, message: str) -> None:
        if self.worker_mode in {"copy", "resume"}:
            source = self._current_topic_link(self.source_edit)
            target = self._current_topic_link(self.target_edit)
            self.incomplete_run = self._matching_incomplete_run(source, target)
            if self.incomplete_run is not None:
                self.copy_button.setText(self.tr("Aktuelle Auswahl sicher fortsetzen"))
        if (
            "WorkerBusyTooLongRetryError" in message
            or "workers are too busy" in message.lower()
        ):
            message = self.tr(
                "Telegram ist momentan ausgelastet. Das Kopieren wurde sicher "
                "angehalten; nur von Telegram bestätigte Kopien stehen im "
                "Fortschritt. Bitte später über denselben Kopierknopf fortsetzen."
            )
        self._set_status(self.tr("Fehler: {message}").format(message=message))
        # Beim Kopieren den letzten von Telegram bestätigten Wert sichtbar
        # lassen. Ein Fehler vor dem ersten Zähler darf den Balken aber nicht
        # im endlosen Beschäftigt-Modus (Minimum == Maximum == 0) weiterlaufen
        # lassen.
        progress_bar = self._progress()
        if (
            self.worker_mode not in {"copy", "resume"}
            or (progress_bar.minimum() == 0 and progress_bar.maximum() == 0)
        ):
            progress_bar.setRange(0, 1)
            progress_bar.setValue(0)
        if self.worker_mode == "inspect":
            self.bot_list.clear()
            self.bot_list.addItem(self._placeholder(self.tr("Laden fehlgeschlagen.")))
            self.ambiguous_list.clear()
            self.ambiguous_list.addItem(self._placeholder(self.tr("Laden fehlgeschlagen.")))
            self.message_list.clear()
            self.message_list.addItem(self._placeholder(self.tr("Laden fehlgeschlagen.")))
            _resize_list_to_contents(self.bot_list, max_rows=6)
            _resize_list_to_contents(self.ambiguous_list, max_rows=8)
            _resize_list_to_contents(self.message_list, max_rows=10)
        elif self.worker_mode == "verify":
            self.delete_list.clear()
            self.delete_list.addItem(self._placeholder(self.tr("Prüfung fehlgeschlagen.")))
            _resize_list_to_contents(self.delete_list, max_rows=10)
        full_message = self.tr(
            "{message}\n\nDetails wurden in data/tme.log gespeichert."
        ).format(message=message)
        if self.worker_mode in {"copy", "resume"}:
            self._last_error_message = full_message
            self.copy_error_label.setText(full_message)
            self.copy_error_label.setVisible(True)
            self.copy_error_button.setVisible(True)
        else:
            show_copyable_error(self, self.tr("Fehler"), full_message)

    def _copy_last_error(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(getattr(self, "_last_error_message", ""))

    @staticmethod
    def _current_topic_link(combo: QComboBox) -> str:
        index = combo.currentIndex()
        if index >= 0 and combo.currentText() == combo.itemText(index):
            stored = combo.itemData(index, Qt.ItemDataRole.UserRole)
            if stored:
                return str(stored).strip()
        return combo.currentText().strip()

    def _load_topic_history(self, combo: QComboBox, key: str) -> None:
        settings = QSettings("MiSte", "TME")
        try:
            entries = json.loads(str(settings.value(key, "[]")))
        except (TypeError, ValueError, json.JSONDecodeError):
            entries = []
        for entry in entries:
            url = str(entry.get("url") or "").strip()
            title = str(entry.get("title") or "").strip() or url
            if url:
                combo.addItem(f"{title} — {url}", url)
        combo.setCurrentIndex(-1)

    def _remember_topic(
        self, combo: QComboBox, key: str, title: str, url: str,
    ) -> None:
        if not url:
            return
        title = title.strip() or url
        entries = [{"title": title, "url": url}]
        for index in range(combo.count()):
            old_url = str(combo.itemData(index, Qt.ItemDataRole.UserRole) or "")
            if old_url and old_url != url:
                old_label = combo.itemText(index)
                old_title = old_label.split(" — ", 1)[0]
                entries.append({"title": old_title, "url": old_url})
        entries = entries[:10]
        combo.blockSignals(True)
        try:
            combo.clear()
            for entry in entries:
                combo.addItem(
                    f"{entry['title']} — {entry['url']}", entry["url"],
                )
            combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)
        QSettings("MiSte", "TME").setValue(key, json.dumps(entries, ensure_ascii=False))

    def _set_busy(self, busy: bool) -> None:
        for button in (self.load_button, self.copy_button, self.verify_button,
                       self.delete_button, self.select_all_button, self.select_none_button,
                       self.ambiguous_all_button, self.ambiguous_none_button):
            button.setEnabled(not busy)

    def _cleanup(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        if self.thread is not None:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self._set_busy(False)
        self.copy_button.setEnabled(self.preview is not None)
        self.verify_button.setEnabled(self.run_file is not None)
        self.delete_button.setEnabled(self.delete_list.count() > 0)
