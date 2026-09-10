from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from pipeline.logging_setup import get_logger
from pipeline.topic_copy import (
    RollbackAudit, audit_target_rollback, latest_complete_run,
    rollback_target_messages,
)
from ui.message_boxes import show_copyable_error

logger = get_logger(__name__)
LOCAL_TIMEZONE = ZoneInfo("Europe/Zurich")
BOT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _timestamp(value: str) -> str:
    if not value:
        return "Datum unbekannt"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone(LOCAL_TIMEZONE)
        return parsed.strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return "Datum unbekannt"


class ContainedListWidget(QListWidget):
    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self.verticalScrollBar().maximum() > self.verticalScrollBar().minimum():
            super().wheelEvent(event)
        event.accept()


class RollbackWorker(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)
    status = Signal(str)

    def __init__(self, mode: str, run_file: Path, target_ids=None) -> None:
        super().__init__()
        self.mode = mode
        self.run_file = run_file
        self.target_ids = set(target_ids or ())
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            if self.mode == "audit":
                result = asyncio.run(audit_target_rollback(
                    self.run_file,
                    progress=lambda done, total: self.progress.emit(done, total),
                    status=lambda text: self.status.emit(text),
                    cancel_event=self.cancel_event,
                ))
            else:
                result = asyncio.run(rollback_target_messages(
                    self.run_file, self.target_ids,
                    progress=lambda done, total: self.progress.emit(done, total),
                    status=lambda text: self.status.emit(text),
                    cancel_event=self.cancel_event,
                ))
            self.finished.emit(result)
        except Exception as exc:
            logger.exception("Rückgängig-Aktion '%s' fehlgeschlagen: %s", self.mode, exc)
            self.error.emit(str(exc))


class TopicRollbackTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.run_file = latest_complete_run()
        self.audit: RollbackAudit | None = None
        self.thread: QThread | None = None
        self.worker: RollbackWorker | None = None
        self.worker_mode = ""
        self._close_after_worker = False
        self._delete_baseline = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        info = QLabel(self.tr(
            "Dieser Tab entfernt ausschließlich einzeln protokollierte Kopien "
            "aus dem Ziel-Topic. Zwischenzeitlich normal geschriebene Nachrichten "
            "und das Topic selbst bleiben unangetastet."
        ))
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        info.setMaximumHeight(64)
        layout.addWidget(info)

        box = QGroupBox(self.tr("Zielkopien prüfen und rückgängig machen"))
        box_layout = QVBoxLayout(box)
        self.run_label = QLabel()
        self.run_label.setWordWrap(True)
        box_layout.addWidget(self.run_label)
        self.audit_button = QPushButton(self.tr("Vollständige Abschlussprüfung starten"))
        self.audit_button.setMinimumHeight(32)
        self.audit_button.clicked.connect(self.start_audit)
        box_layout.addWidget(self.audit_button)
        self.resume_button = QPushButton()
        self.resume_button.setMinimumHeight(32)
        self.resume_button.setVisible(False)
        self.resume_button.clicked.connect(self.resume_delete)
        box_layout.addWidget(self.resume_button)
        self.cancel_button = QPushButton(self.tr("Prüfung abbrechen"))
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel)
        box_layout.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        box_layout.addWidget(self.progress)
        self.progress_label = QLabel(self.tr("0 verarbeitet."))
        box_layout.addWidget(self.progress_label)
        self.status = QLabel(self.tr("Noch keine Abschlussprüfung ausgeführt."))
        self.status.setWordWrap(True)
        box_layout.addWidget(self.status)

        self.bot_box = QGroupBox(self.tr("Bot-Anfragen und -Antworten gemeinsam auswählen"))
        bot_layout = QVBoxLayout(self.bot_box)
        self.bot_list = ContainedListWidget()
        self.bot_list.setMaximumHeight(150)
        self.bot_list.itemChanged.connect(self._bot_changed)
        bot_layout.addWidget(self.bot_list)
        box_layout.addWidget(self.bot_box)

        self.category_tabs = QTabWidget()
        self.safe_list = self._new_check_list()
        self.ambiguous_list = self._new_check_list()
        self.safe_list.itemChanged.connect(self._update_selection)
        self.ambiguous_list.itemChanged.connect(self._update_selection)
        self.missing_list = self._new_info_list()
        self.wrong_list = self._new_info_list()
        self.uncopyable_list = self._new_info_list()
        self.deleted_list = self._new_info_list()
        self.category_tabs.addTab(self.safe_list, self.tr("Sicher kopiert"))
        self.category_tabs.addTab(self.ambiguous_list, self.tr("Manuell prüfen"))
        self.category_tabs.addTab(self.missing_list, self.tr("Fehlend"))
        self.category_tabs.addTab(self.wrong_list, self.tr("Falsches Topic"))
        self.category_tabs.addTab(self.uncopyable_list, self.tr("Nicht kopierbar"))
        self.category_tabs.addTab(self.deleted_list, self.tr("Bereits entfernt"))
        box_layout.addWidget(self.category_tabs)

        controls = QHBoxLayout()
        self.all_safe_button = QPushButton(self.tr("Alle sicheren markieren"))
        self.none_button = QPushButton(self.tr("Auswahl aufheben"))
        self.all_safe_button.clicked.connect(lambda: self._set_safe_checks(True))
        self.none_button.clicked.connect(self._clear_checks)
        controls.addWidget(self.all_safe_button)
        controls.addWidget(self.none_button)
        controls.addStretch(1)
        box_layout.addLayout(controls)
        self.selection_label = QLabel(self.tr("0 Zielnachrichten ausgewählt."))
        box_layout.addWidget(self.selection_label)
        self.delete_button = QPushButton(self.tr("Ausgewählte Zielkopien entfernen"))
        self.delete_button.setMinimumHeight(34)
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.start_delete)
        box_layout.addWidget(self.delete_button)
        layout.addWidget(box)
        self._refresh_run_label()
        self._set_controls(False)

    @staticmethod
    def _new_check_list() -> QListWidget:
        widget = ContainedListWidget()
        widget.setUniformItemSizes(True)
        widget.setMinimumHeight(220)
        return widget

    @staticmethod
    def _new_info_list() -> QListWidget:
        widget = ContainedListWidget()
        widget.setUniformItemSizes(True)
        widget.setMinimumHeight(160)
        return widget

    def _refresh_run_label(self) -> None:
        if self.run_file is None:
            self.run_label.setText(self.tr("Kein abgeschlossener Kopierlauf gefunden."))
            self.audit_button.setEnabled(False)
        else:
            self.run_label.setText(self.tr("Kopierprotokoll: {name}").format(
                name=self.run_file.name,
            ))
            self.audit_button.setEnabled(True)
        remaining = self._saved_remaining_ids()
        self.resume_button.setVisible(bool(remaining))
        self.resume_button.setText(self.tr(
            "Unterbrochenes Entfernen sicher fortsetzen ({count} offen)"
        ).format(count=len(remaining)))
        requested, deleted = self._saved_rollback_counts()
        if requested:
            confirmed = len(requested & deleted)
            total = len(requested)
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(confirmed)
            self.progress_label.setText(self.tr(
                "{done} von {total} entfernt, {remaining} noch offen."
            ).format(
                done=confirmed, total=total,
                remaining=max(0, total - confirmed),
            ))

    def _saved_remaining_ids(self) -> set[int]:
        requested, deleted = self._saved_rollback_counts()
        return requested - deleted

    def _saved_rollback_counts(self) -> tuple[set[int], set[int]]:
        if self.run_file is None:
            return set(), set()
        try:
            data = json.loads(self.run_file.read_text(encoding="utf-8"))
            requested = {
                int(value)
                for value in (data.get("target_rollback") or {}).get(
                    "requested_ids", []
                )
            }
            deleted = {int(value) for value in data.get("deleted_target_ids") or []}
            return requested, deleted
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return set(), set()

    def resume_delete(self) -> None:
        remaining = self._saved_remaining_ids()
        if not remaining or self.run_file is None:
            self._refresh_run_label()
            return
        answer = QMessageBox.question(
            self,
            self.tr("Entfernen fortsetzen?"),
            self.tr(
                "{count} bereits ausgewählte Zielkopien sind noch offen. "
                "Jeder Block wird vor dem Löschen erneut geprüft. Fortsetzen?"
            ).format(count=len(remaining)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start(RollbackWorker(
                "delete", self.run_file, remaining,
            ), "delete")

    def start_audit(self) -> None:
        self.run_file = latest_complete_run()
        self._refresh_run_label()
        if self.run_file is None:
            return
        self._start(RollbackWorker("audit", self.run_file), "audit")

    def start_delete(self) -> None:
        selected = self._selected_target_ids()
        if not selected or self.run_file is None:
            return
        answer = QMessageBox.question(
            self,
            self.tr("Zielkopien endgültig entfernen?"),
            self.tr(
                "{count} einzeln nachgewiesene Zielnachrichten werden gelöscht. "
                "Normale Nachrichten und das Topic bleiben erhalten. Dieser "
                "Schritt kann nicht rückgängig gemacht werden. Fortfahren?"
            ).format(count=len(selected)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start(RollbackWorker(
                "delete", self.run_file, selected,
            ), "delete")

    def _start(self, worker: RollbackWorker, mode: str) -> None:
        self.worker_mode = mode
        self.worker = worker
        self.thread = QThread(self)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.status.connect(self.status.setText)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        worker.finished.connect(self.thread.quit)
        worker.error.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup)
        self.audit_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.cancel_button.setText(
            self.tr("Löschen abbrechen") if mode == "delete"
            else self.tr("Prüfung abbrechen")
        )
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        if mode == "delete":
            requested, deleted = self._saved_rollback_counts()
            self._delete_baseline = len(deleted & requested)
            total = self._delete_baseline + len(worker.target_ids)
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(self._delete_baseline)
            self.progress_label.setText(self.tr(
                "{done} von {total} entfernt, {remaining} noch offen."
            ).format(
                done=self._delete_baseline,
                total=total,
                remaining=len(worker.target_ids),
            ))
        else:
            self._delete_baseline = 0
            self.progress_label.setText(self.tr("Abschlussprüfung wird gestartet…"))
        self.status.setText(
            self.tr("Zielkopien werden vollständig geprüft…")
            if mode == "audit" else self.tr("Rückgängig-Lauf wird vorbereitet…")
        )
        self.thread.start()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText(self.tr("Abbruch angefordert…"))

    def _on_progress(self, done: int, total: int) -> None:
        if self.worker_mode == "delete":
            overall_done = self._delete_baseline + done
            overall_total = self._delete_baseline + total
            self.progress.setRange(0, max(1, overall_total))
            self.progress.setValue(overall_done)
            self.progress_label.setText(self.tr(
                "{done} von {total} entfernt, {remaining} noch offen."
            ).format(
                done=overall_done,
                total=overall_total,
                remaining=max(0, overall_total - overall_done),
            ))
        else:
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
            self.progress_label.setText(self.tr(
                "{done} von {total} Zielnachrichten geprüft."
            ).format(done=done, total=total))

    def _on_finished(self, result: object) -> None:
        if self.worker_mode == "audit":
            self._show_audit(result)
        else:
            self.status.setText(self.tr(
                "{count} Zielkopien wurden nachweislich entfernt. Bitte die "
                "Abschlussprüfung erneut starten."
            ).format(count=int(result)))
            self.audit = None
            self.delete_button.setEnabled(False)

    def _on_error(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText(self.tr("Fehler: {message}").format(message=message))
        show_copyable_error(
            self, self.tr("Rückgängig-Fehler"),
            self.tr("{message}\n\nDetails wurden in data/tme.log gespeichert.").format(
                message=message,
            ),
        )

    def _cleanup(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        if self.thread is not None:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.cancel_button.setVisible(False)
        self.audit_button.setEnabled(self.run_file is not None)
        self.resume_button.setEnabled(True)
        self._refresh_run_label()
        if self.audit is not None:
            self.delete_button.setEnabled(bool(self._selected_target_ids()))

    def _show_audit(self, audit: RollbackAudit) -> None:
        self.audit = audit
        lists = (
            self.safe_list, self.ambiguous_list, self.missing_list,
            self.wrong_list, self.uncopyable_list, self.deleted_list,
        )
        for widget in lists:
            widget.blockSignals(True)
            widget.setUpdatesEnabled(False)
            widget.clear()
        self.bot_list.blockSignals(True)
        self.bot_list.clear()
        for item in audit.safe:
            self._add_item(self.safe_list, item, checkable=True)
        for item in audit.ambiguous:
            self._add_item(self.ambiguous_list, item, checkable=True)
        for widget, items in (
            (self.missing_list, audit.missing),
            (self.wrong_list, audit.wrong_topic),
            (self.uncopyable_list, audit.uncopyable),
            (self.deleted_list, audit.already_deleted),
        ):
            for item in items:
                self._add_item(widget, item, checkable=False)
        bots = {}
        for item in (*audit.safe, *audit.ambiguous):
            if item.bot_id is not None:
                bots[item.bot_id] = item.bot_name
        for bot_id, bot_name in sorted(bots.items(), key=lambda value: value[1].lower()):
            row = QListWidgetItem(bot_name)
            row.setData(Qt.ItemDataRole.UserRole, bot_id)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Unchecked)
            self.bot_list.addItem(row)
        self.bot_list.blockSignals(False)
        for widget in lists:
            widget.setUpdatesEnabled(True)
            widget.blockSignals(False)
        self._set_controls(True)
        for index, (label, count) in enumerate((
            (self.tr("Sicher kopiert"), len(audit.safe)),
            (self.tr("Manuell prüfen"), len(audit.ambiguous)),
            (self.tr("Fehlend"), len(audit.missing)),
            (self.tr("Falsches Topic"), len(audit.wrong_topic)),
            (self.tr("Nicht kopierbar"), len(audit.uncopyable)),
            (self.tr("Bereits entfernt"), len(audit.already_deleted)),
        )):
            self.category_tabs.setTabText(index, f"{label} ({count})")
        self._update_selection()
        self.status.setText(self.tr(
            "Abschlussprüfung: {safe} sicher, {ambiguous} manuell zu prüfen, "
            "{missing} fehlend, {wrong} falsches Topic, {uncopyable} nicht "
            "kopierbar, {deleted} bereits entfernt."
        ).format(
            safe=len(audit.safe), ambiguous=len(audit.ambiguous),
            missing=len(audit.missing), wrong=len(audit.wrong_topic),
            uncopyable=len(audit.uncopyable), deleted=len(audit.already_deleted),
        ))

    def _add_item(self, widget: QListWidget, item, checkable: bool) -> None:
        text = item.text.replace("\n", " ")[:120] or self.tr("[Mediennachricht]")
        prefix = self.tr("[Original: {original} | Kopiert: {copied}]").format(
            original=_timestamp(item.date_iso), copied=_timestamp(item.target_date_iso),
        )
        if item.reason:
            text = f"{text} — {item.reason}"
        row = QListWidgetItem(f"{prefix} {item.sender_name}: {text}")
        row.setData(Qt.ItemDataRole.UserRole, item.target_id or None)
        row.setData(BOT_ROLE, item.bot_id)
        if checkable:
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Unchecked)
        else:
            row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        widget.addItem(row)

    def _set_controls(self, enabled: bool) -> None:
        self.bot_box.setVisible(enabled and self.bot_list.count() > 0)
        self.category_tabs.setVisible(enabled)
        self.all_safe_button.setEnabled(enabled)
        self.none_button.setEnabled(enabled)

    def _set_safe_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.safe_list.blockSignals(True)
        self.safe_list.setUpdatesEnabled(False)
        try:
            for index in range(self.safe_list.count()):
                self.safe_list.item(index).setCheckState(state)
        finally:
            self.safe_list.setUpdatesEnabled(True)
            self.safe_list.blockSignals(False)
        self._update_selection()

    def _clear_checks(self) -> None:
        for widget in (self.safe_list, self.ambiguous_list, self.bot_list):
            widget.blockSignals(True)
            widget.setUpdatesEnabled(False)
            try:
                for index in range(widget.count()):
                    item = widget.item(index)
                    if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                        item.setCheckState(Qt.CheckState.Unchecked)
            finally:
                widget.setUpdatesEnabled(True)
                widget.blockSignals(False)
        self._update_selection()

    def _bot_changed(self, bot_item: QListWidgetItem) -> None:
        bot_id = bot_item.data(Qt.ItemDataRole.UserRole)
        state = bot_item.checkState()
        for widget in (self.safe_list, self.ambiguous_list):
            widget.blockSignals(True)
            widget.setUpdatesEnabled(False)
            try:
                for index in range(widget.count()):
                    item = widget.item(index)
                    if item.data(BOT_ROLE) == bot_id:
                        item.setCheckState(state)
            finally:
                widget.setUpdatesEnabled(True)
                widget.blockSignals(False)
        self._update_selection()

    def _selected_target_ids(self) -> set[int]:
        result = set()
        for widget in (self.safe_list, self.ambiguous_list):
            for index in range(widget.count()):
                item = widget.item(index)
                target_id = item.data(Qt.ItemDataRole.UserRole)
                if target_id and item.checkState() == Qt.CheckState.Checked:
                    result.add(int(target_id))
        return result

    def _update_selection(self, _item=None) -> None:
        count = len(self._selected_target_ids())
        self.selection_label.setText(self.tr(
            "{count} Zielnachrichten ausgewählt."
        ).format(count=count))
        self.delete_button.setEnabled(count > 0 and self.worker is None)

    def request_safe_close(self) -> bool:
        if self.thread is None or not self.thread.isRunning():
            return True
        self.cancel()
        if not self._close_after_worker:
            self._close_after_worker = True
            self.thread.finished.connect(self._close_parent_after_worker)
        return False

    def _close_parent_after_worker(self) -> None:
        self._close_after_worker = False
        self.window().close()
