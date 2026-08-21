from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import json
import re

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox, QHeaderView,
    QComboBox, QAbstractItemView
)

from schedule_json import (
    ScheduleDocument, ScheduleSection, load_schedule_document, save_schedule_document,
    ISO_DATE_FMT, _parse_date as parse_date
)


def _sanitize_filename_stem(title: str) -> str:
    """Bereinigt einen Dokumententitel zu einem gültigen Dateinamen-Stamm
    (ohne Endung): Pfadtrenner, Steuerzeichen und unter Windows reservierte
    Zeichen werden durch '_' ersetzt, Mehrfach-Unterstriche zusammengefasst,
    Rand-Whitespace/-Punkte/-Unterstriche entfernt. Liefert "" bei leerem
    oder vollständig ungültigem Titel (Aufrufer entscheidet dann über einen
    Fallback-Namen)."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title or "").strip()
    cleaned = re.sub(r"_+", "_", cleaned).strip("_ .")
    return cleaned


class ScheduleEditorTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.current_path: Optional[Path] = None
        self._building = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        # Top bar: file + actions
        top = QHBoxLayout(); top.setSpacing(8)
        self.path_edit = QLineEdit(); self.path_edit.setPlaceholderText(str(Path.cwd() / "input/schedule.json"))
        self.btn_new = QPushButton(self.tr("Neu")); self.btn_new.clicked.connect(self._new_doc)
        self.btn_load = QPushButton(self.tr("Telegram-Export laden")); self.btn_load.clicked.connect(self._load_doc)
        self.btn_save = QPushButton(self.tr("Telegram-Export speichern")); self.btn_save.clicked.connect(self._save_doc)
        self.btn_save_as = QPushButton(self.tr("Speichern unter…")); self.btn_save_as.clicked.connect(self._save_doc_as)
        top.addWidget(self.path_edit)
        top.addWidget(self.btn_new)
        top.addWidget(self.btn_load)
        top.addWidget(self.btn_save)
        top.addWidget(self.btn_save_as)
        lay.addLayout(top)

        # Document fields
        doc_bar = QHBoxLayout(); doc_bar.setSpacing(8)
        self.title_edit = QLineEdit(); self.title_edit.setPlaceholderText(self.tr("Dokumenttitel (optional)"))
        self.default_channel_edit = QLineEdit(); self.default_channel_edit.setPlaceholderText(self.tr("Default-Channel (@name oder Link, optional)"))
        doc_bar.addWidget(QLabel(self.tr("Dokumenttitel")))
        doc_bar.addWidget(self.title_edit, 2)
        doc_bar.addWidget(QLabel(self.tr("Default-Channel")))
        doc_bar.addWidget(self.default_channel_edit, 2)
        lay.addLayout(doc_bar)

        # Sections table
        # Spalten: Datum, Von, Bis, Titel, Untertitel, Links, Nach Datum holen, Kanal
        self.table = QTableWidget(0, 8)
        # Bedienbarkeit verbessern: horizontales Scrollen erlauben, Spalten manuell resizebar,
        # und Zeilenumbruch in den Titelzellen.
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Punkt 12: Standardmäßig verlangt QTableWidget einen Doppelklick (oder
        # F2/Enter) zum Editieren einer Zelle. CurrentChanged öffnet den Editor
        # bereits, sobald eine Zelle per einfachem Klick (oder Tastatur) zur
        # aktuellen Zelle wird - AnyKeyPressed/EditKeyPressed bleiben zusätzlich
        # erhalten, damit Tippen bzw. F2/Enter weiterhin funktionieren. Die
        # Checkbox-Spalte (6) ist davon unberührt, da sie keinen Text-Editor hat.
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.CurrentChanged
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setHorizontalHeaderLabels([
            self.tr("Datum\n(YYYY-MM-DD)"),
            self.tr("Von\n(HH:MM[:SS])"),
            self.tr("Bis\n(HH:MM[:SS])"),
            self.tr("Titel"),
            self.tr("Untertitel (optional)"),
            self.tr("Links / @Benutzernamen (mit ; trennen)"),
            self.tr("Nach Datum holen"),
            self.tr("Kanal (siehe Hinweis)"),
        ])
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        # Alle Spalten sind manuell größenveränderbar; breite Texte können durch
        # horizontales Scrollen vollständig gelesen werden.
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # Startbreiten sinnvoll vorgeben (können vom Nutzer angepasst werden)
        # Datum/Zeiten relativ schmal halten, damit Überschriften mit Zeilenumbruch gut lesbar sind
        hh.resizeSection(0, 120)  # Datum
        hh.resizeSection(1, 90)   # Von
        hh.resizeSection(2, 90)   # Bis
        hh.resizeSection(3, 260)  # Titel
        hh.resizeSection(4, 260)  # Untertitel
        hh.resizeSection(5, 320)  # Links
        hh.resizeSection(6, 130)  # Nach Datum holen
        hh.resizeSection(7, 220)  # Kanal
        # Tooltips für Spaltentitel
        hh.setToolTip(self.tr("Schedule-Spalten"))
        hh.setSectionsClickable(True)
        model = hh.model()
        if model is not None:
            model.setHeaderData(0, Qt.Orientation.Horizontal, self.tr("Datum im Format YYYY-MM-DD"), Qt.ItemDataRole.ToolTipRole)
            model.setHeaderData(1, Qt.Orientation.Horizontal, self.tr("Startzeit im Format HH:MM oder HH:MM:SS"), Qt.ItemDataRole.ToolTipRole)
            model.setHeaderData(2, Qt.Orientation.Horizontal, self.tr("Endzeit im Format HH:MM oder HH:MM:SS"), Qt.ItemDataRole.ToolTipRole)
            model.setHeaderData(3, Qt.Orientation.Horizontal, self.tr("Titel des Abschnitts"), Qt.ItemDataRole.ToolTipRole)
            model.setHeaderData(4, Qt.Orientation.Horizontal, self.tr("Untertitel oder Beschreibung (optional)"), Qt.ItemDataRole.ToolTipRole)
            model.setHeaderData(5, Qt.Orientation.Horizontal, self.tr("Telegram-Links oder @Benutzernamen; mehrere mit ; trennen"), Qt.ItemDataRole.ToolTipRole)
            model.setHeaderData(6, Qt.Orientation.Horizontal, self.tr("Ob Nachrichten nach Datum aus dem Kanal geladen werden. Wenn abgewählt, werden stattdessen die Links dieser Zeile verwendet (Von/Bis werden dann ignoriert)."), Qt.ItemDataRole.ToolTipRole)
            model.setHeaderData(7, Qt.Orientation.Horizontal, self.tr("Kanal für diesen Abschnitt. Erforderlich, wenn 'Nach Datum holen' aktiv ist und kein Default-Channel gesetzt ist - sonst wird der Lauf fehlschlagen. Ohne 'Nach Datum holen' wird diese Spalte nicht benötigt (Links gelten dann)."), Qt.ItemDataRole.ToolTipRole)
        self.table.itemChanged.connect(self._on_table_item_changed)
        lay.addWidget(self.table)

        # Section actions
        sec_bar = QHBoxLayout(); sec_bar.setSpacing(8)
        self.btn_add = QPushButton(self.tr("Abschnitt hinzufügen")); self.btn_add.clicked.connect(self._add_row)
        self.btn_remove = QPushButton(self.tr("Ausgewählten Abschnitt entfernen")); self.btn_remove.clicked.connect(self._remove_row)
        sec_bar.addWidget(QLabel(self.tr("Einfügen an Position:")))
        self.add_position = QComboBox(); self._set_add_position_items()
        sec_bar.addWidget(self.add_position)
        sec_bar.addWidget(self.btn_add)
        sec_bar.addWidget(self.btn_remove)
        sec_bar.addStretch(1)
        lay.addLayout(sec_bar)

        # Bottom run bar
        # Punkt 13: Die frühere eigene "Übersetzen"/Modus/Sprache-Auswahl
        # dieses Tabs wurde entfernt - sie war ein veralteter Duplikat der
        # tatsächlich wirksamen, persistierten Einstellungen im
        # "Telegram-Export"-Tab (dort: cb_translate/mode_combo/lang_edit in
        # ScheduleTab, siehe ui/app.py). _run_now() sprang beim Start ohnehin
        # in diesen Tab und überschrieb dessen Einstellungen stumm mit den
        # hier lokal (und nicht persistent) gewählten Werten - verwirrend und
        # redundant. Jetzt gilt beim Starten einfach das, was im
        # Telegram-Export-Tab bereits konfiguriert ist.
        run_bar = QHBoxLayout(); run_bar.setSpacing(8)
        # "Speichern & Starten" statt nur "Starten": der Klick speichert die
        # Schedule-Datei, wechselt in den Telegram-Export-Tab und startet dort
        # den Lauf - eine reine "Starten"-Beschriftung würde das Speichern
        # verschweigen (siehe auch Punkt 14, dort trifft "Starten" zu, weil
        # der dortige Button nur startet, nicht zusätzlich speichert/wechselt).
        self.btn_run = QPushButton(self.tr("Speichern && Starten")); self.btn_run.clicked.connect(self._run_now)
        run_bar.addStretch(1)
        run_bar.addWidget(self.btn_run)
        lay.addLayout(run_bar)

        # Info
        info = QLabel(self.tr(
            "Hinweis: Datum im Format YYYY-MM-DD. Bei aktiviertem 'Nach Datum holen' muss ein Kanal "
            "bekannt sein - entweder in der Spalte 'Kanal' dieser Zeile oder als Default-Channel oben "
            "für alle Abschnitte. Ist 'Nach Datum holen' abgewählt, werden stattdessen die angegebenen "
            "Links verwendet und Von/Bis sowie Kanal spielen keine Rolle."
        ))
        info.setWordWrap(True)
        lay.addWidget(info)

        lay.addStretch(1)
        self._new_doc()

    def _set_add_position_items(self) -> None:
        self.add_position.clear()
        self.add_position.addItem(self.tr("unterhalb der Auswahl"), "below")
        self.add_position.addItem(self.tr("oberhalb der Auswahl"), "above")
        self.add_position.addItem(self.tr("am Anfang"), "start")
        self.add_position.addItem(self.tr("am Ende"), "end")

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def retranslate(self) -> None:
        self.btn_new.setText(self.tr("Neu"))
        self.btn_load.setText(self.tr("Telegram-Export laden"))
        self.btn_save.setText(self.tr("Telegram-Export speichern"))
        self.btn_save_as.setText(self.tr("Speichern unter…"))
        self.title_edit.setPlaceholderText(self.tr("Dokumenttitel (optional)"))
        self.default_channel_edit.setPlaceholderText(self.tr("Default-Channel (@name oder Link, optional)"))
        self.table.setHorizontalHeaderLabels([
            self.tr("Datum\n(YYYY-MM-DD)"),
            self.tr("Von\n(HH:MM[:SS])"),
            self.tr("Bis\n(HH:MM[:SS])"),
            self.tr("Titel"),
            self.tr("Untertitel (optional)"),
            self.tr("Links / @Benutzernamen (mit ; trennen)"),
            self.tr("Nach Datum holen"),
            self.tr("Kanal (siehe Hinweis)"),
        ])
        self.btn_run.setText(self.tr("Speichern && Starten"))
        if hasattr(self, "add_position"):
            current_data = self.add_position.currentData()
            self._set_add_position_items()
            # nach dem Reset Auswahl wiederherstellen
            idx = self.add_position.findData(current_data)
            if idx >= 0:
                self.add_position.setCurrentIndex(idx)

    # Document model helpers -------------------------------------------------
    def _new_doc(self) -> None:
        self.current_path = None
        self.path_edit.clear()
        self.title_edit.clear()
        self.default_channel_edit.clear()
        self.table.setRowCount(0)
        self._add_row()  # start with one row

    def _load_doc(self) -> None:
        p_str, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Schedule laden"),
            str(Path.cwd() / "input"),
            self.tr("Schedule (*.json *.txt);;JSON (*.json);;Text (*.txt)"),
        )
        if not p_str:
            return
        p = Path(p_str)
        try:
            if p.suffix.lower() != ".json":
                raise ValueError(self.tr("Nur JSON-Schedules werden unterstützt. Bitte die Datei zuerst nach JSON konvertieren."))
            sched = load_schedule_document(p)
            self._populate_from_schedule(sched)
            self.current_path = p
            self.path_edit.setText(str(p))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))

    def _populate_from_schedule(self, sched: ScheduleDocument) -> None:
        self._building = True
        try:
            self.title_edit.setText(sched.document_title or "")
            self.default_channel_edit.setText(sched.default_channel or "")
            self.table.setRowCount(0)
            for sec in sched.sections:
                row = self.table.rowCount(); self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(sec.date.strftime(ISO_DATE_FMT)))
                # Zeiten; wenn nicht gesetzt, Defaults anzeigen
                from_time = sec.start_time or "00:00:00"
                to_time = sec.end_time or "23:59:59"
                self.table.setItem(row, 1, QTableWidgetItem(from_time))
                self.table.setItem(row, 2, QTableWidgetItem(to_time))
                it_title = QTableWidgetItem(sec.title)
                # Zeilenumbruch in Titelzellen erlauben, damit lange Titel lesbarer sind
                it_title.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, 3, it_title)

                it_sub = QTableWidgetItem(sec.subheading or "")
                it_sub.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, 4, it_sub)
                self.table.setItem(row, 5, QTableWidgetItem(";".join(sec.links)))
                # ItemIsEditable bewusst NICHT gesetzt (siehe Punkt 12): ohne
                # das würde der neue CurrentChanged-EditTrigger versuchen,
                # beim Anklicken auch über der Checkbox einen Text-Editor zu
                # öffnen - die Checkbox selbst wird über ItemIsUserCheckable
                # unabhängig davon bedienbar.
                cb = QTableWidgetItem()
                cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                cb.setCheckState(Qt.CheckState.Checked if sec.fetch_by_date else Qt.CheckState.Unchecked)
                self.table.setItem(row, 6, cb)
                self.table.setItem(row, 7, QTableWidgetItem(sec.channel or ""))
                # itemChanged wird während self._building unterdrückt (siehe
                # _on_table_item_changed) - Von/Bis-Zustand daher hier explizit
                # nachziehen (Punkt 11).
                self._update_time_fields_enabled(row)
        finally:
            self._building = False

    def _collect_schedule(self) -> ScheduleDocument:
        title = (self.title_edit.text() or "").strip() or None
        default_channel = (self.default_channel_edit.text() or "").strip() or None
        sections: List[ScheduleSection] = []
        for row in range(self.table.rowCount()):
            date_item = self.table.item(row, 0)
            from_item = self.table.item(row, 1)
            to_item = self.table.item(row, 2)
            title_item = self.table.item(row, 3)
            sub_item = self.table.item(row, 4)
            links_item = self.table.item(row, 5)
            date_text = (date_item.text() if date_item is not None else "").strip()
            from_text = (from_item.text() if from_item is not None else "").strip() or "00:00:00"
            to_text = (to_item.text() if to_item is not None else "").strip() or "23:59:59"
            title_text = (title_item.text() if title_item is not None else "").strip()
            sub_text = (sub_item.text() if sub_item is not None else "").strip() or None
            links_text = (links_item.text() if links_item is not None else "").strip()
            fetch_item = self.table.item(row, 6)
            fetch_flag = True
            if fetch_item and fetch_item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                fetch_flag = (fetch_item.checkState() == Qt.CheckState.Checked)
            channel_item = self.table.item(row, 7)
            channel_text = (channel_item.text() if channel_item is not None else "").strip() or None
            # Validation
            try:
                date_obj = parse_date(date_text)
            except Exception as e:
                raise ValueError(self.tr("Ungültiges Datum in Zeile {row}: {err}").format(row=row+1, err=str(e)))
            if not title_text:
                raise ValueError(self.tr("Titel fehlt in Zeile {row}").format(row=row+1))
            links = [seg.strip() for seg in links_text.split(";") if seg.strip()]
            # Punkt 10: Eingabe-Plausibilitätsprüfung, die die von
            # schedule_json.schedule_to_blocks() ohnehin durchgesetzte Regel
            # (siehe dort: "benötigt einen default_channel, da keine Links
            # angegeben sind") schon hier beim Speichern/Start meldet - statt
            # erst tief im Lauf nach Telegram-Login und Zeichen-Vorschau.
            effective_channel = channel_text or default_channel
            if fetch_flag and not effective_channel:
                raise ValueError(self.tr(
                    "Zeile {row}: 'Nach Datum holen' ist aktiv, aber es ist weder ein Kanal für diesen "
                    "Abschnitt (Spalte 'Kanal') noch ein Default-Channel gesetzt."
                ).format(row=row + 1))
            if not fetch_flag and not links:
                raise ValueError(self.tr(
                    "Zeile {row}: 'Nach Datum holen' ist abgewählt, aber es sind keine Links angegeben. "
                    "Dieser Abschnitt würde keine Nachrichten enthalten."
                ).format(row=row + 1))
            # Zeiten als Strings speichern; Validierung übernimmt schedule_json.
            sections.append(ScheduleSection(
                date=date_obj,
                title=title_text,
                subheading=sub_text,
                start_time=from_text,
                end_time=to_text,
                links=links,
                fetch_by_date=bool(fetch_flag),
                channel=channel_text,
            ))
        return ScheduleDocument(document_title=title, default_channel=default_channel, sections=sections)

    def _save_doc(self) -> None:
        try:
            sched = self._collect_schedule()
            if self.current_path is None:
                self._save_doc_as()
                return
            save_schedule_document(sched, self.current_path)
            QMessageBox.information(self, self.tr("Gespeichert"), self.tr("Schedule gespeichert: {p}").format(p=str(self.current_path)))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))

    def _save_doc_as(self) -> None:
        try:
            sched = self._collect_schedule()
            if self.current_path is not None:
                default_path = self.current_path
            else:
                # Neue, noch unbenannte Schedule-Datei: Dokumententitel
                # (bereinigt) als Standard-Dateiname vorschlagen, falls
                # vorhanden. Ohne Titel bleibt der bisherige generische
                # Vorschlag unverändert.
                stem = _sanitize_filename_stem(sched.document_title or "")
                default_path = (Path.cwd() / "input" / f"{stem}.json") if stem else (Path.cwd() / "input/schedule.json")
            p_str, _ = QFileDialog.getSaveFileName(
                self,
                self.tr("Schedule speichern"),
                str(default_path),
                self.tr("JSON (*.json)"),
            )
            if not p_str:
                return
            p = Path(p_str)
            if p.suffix.lower() != ".json":
                p = p.with_suffix(".json")
            save_schedule_document(sched, p)
            self.current_path = p
            self.path_edit.setText(str(p))
            QMessageBox.information(self, self.tr("Gespeichert"), self.tr("Schedule gespeichert: {p}").format(p=str(p)))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))

    def _add_row(self) -> None:
        insert_pos = self.table.rowCount()
        mode = getattr(self, "add_position", None)
        if mode is not None:
            sel = mode.currentData()
            current_row = self.table.currentRow()
            if sel == "above" and current_row >= 0:
                insert_pos = current_row
            elif sel == "below" and current_row >= 0:
                insert_pos = current_row + 1
            elif sel == "start":
                insert_pos = 0
            elif sel == "end":
                insert_pos = self.table.rowCount()
        self.table.insertRow(insert_pos)
        # Initialize with today template
        from datetime import date
        self.table.setItem(insert_pos, 0, QTableWidgetItem(date.today().strftime(ISO_DATE_FMT)))
        # Default-Zeiten: ganzer Tag
        self.table.setItem(insert_pos, 1, QTableWidgetItem("00:00:00"))
        self.table.setItem(insert_pos, 2, QTableWidgetItem("23:59:59"))
        it_title = QTableWidgetItem("")
        it_title.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(insert_pos, 3, it_title)
        it_sub = QTableWidgetItem("")
        it_sub.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(insert_pos, 4, it_sub)
        self.table.setItem(insert_pos, 5, QTableWidgetItem(""))
        # ItemIsEditable bewusst NICHT gesetzt (siehe Punkt 12, analog zu
        # _populate_from_schedule oben).
        cb = QTableWidgetItem()
        cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        cb.setCheckState(Qt.CheckState.Checked)
        self.table.setItem(insert_pos, 6, cb)
        self.table.setItem(insert_pos, 7, QTableWidgetItem(""))
        self._update_time_fields_enabled(insert_pos)

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        """Reagiert auf Änderungen an Tabellenzellen - aktuell nur relevant für
        die 'Nach Datum holen'-Checkbox (Spalte 6): schaltet Von/Bis (Punkt 11)
        für die jeweilige Zeile passend um. Während _populate_from_schedule()
        (self._building) unterdrückt, da dort jede Zeile am Ende explizit
        _update_time_fields_enabled() aufruft (vermeidet N unnötige
        Zwischenaufrufe während des Befüllens)."""
        if self._building:
            return
        if item.column() == 6:
            self._update_time_fields_enabled(item.row())

    def _update_time_fields_enabled(self, row: int) -> None:
        """Punkt 11: Von/Bis (Spalten 1/2) sind nur relevant, wenn diese Zeile
        per 'Nach Datum holen' (Spalte 6) aus dem Kanal lädt - bei expliziten
        Links (Checkbox abgewählt) werden sie ignoriert (siehe
        _collect_schedule/schedule_json). Werden hier ausgegraut und
        nicht-editierbar geschaltet statt geleert, damit ein zuvor
        eingegebener Wert beim Wiederanschalten erhalten bleibt."""
        fetch_item = self.table.item(row, 6)
        enabled = True
        if fetch_item is not None and (fetch_item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            enabled = fetch_item.checkState() == Qt.CheckState.Checked
        for col in (1, 2):
            cell = self.table.item(row, col)
            if cell is None:
                continue
            flags = cell.flags()
            if enabled:
                flags |= (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
            else:
                flags &= ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
            cell.setFlags(flags)

    def _run_now(self) -> None:
        # Ensure saved JSON exists
        if self.current_path is None or not Path(str(self.current_path)).exists():
            # Try saving first
            self._save_doc()
            if self.current_path is None or not Path(str(self.current_path)).exists():
                return
        # Switch to Schedule tab and reuse its mechanism. Punkt 13: die
        # Übersetzungseinstellungen kommen jetzt ausschließlich vom
        # Telegram-Export-Tab (st.cb_translate/mode_combo/lang_edit) - dieser
        # Tab hat dafür keine eigene, davon abweichende Auswahl mehr.
        try:
            mw = self.window()
            if hasattr(mw, "schedule_tab") and hasattr(mw, "tabs"):
                st = getattr(mw, "schedule_tab")
                tabs = getattr(mw, "tabs", None)
                st.schedule_edit.setText(str(self.current_path))
                if tabs is not None and hasattr(tabs, "setCurrentWidget"):
                    tabs.setCurrentWidget(st)
                st.run_schedule_file()
            else:
                # Fallback: basic run (no UI progress), ohne Übersetzung - die
                # dafür nötigen, persistierten Einstellungen leben im
                # Telegram-Export-Tab (mw.schedule_tab), der in diesem Zweig
                # nicht existiert.
                from .app import ScheduleWorker  # type: ignore
                from PySide6.QtCore import QThread
                import threading
                mapping_event = threading.Event()
                worker = ScheduleWorker(
                    schedule_path=Path(str(self.current_path)),
                    translate=False,
                    translation_mode="inline",
                    target_lang="de",
                    include_images=True,
                    include_emojis=True,
                    mapping_event=mapping_event,
                    lettermap_enabled=False,
                )
                thread = QThread(self)
                worker.moveToThread(thread)
                worker.finished.connect(thread.quit)
                worker.error.connect(thread.quit)
                worker.finished.connect(worker.deleteLater)
                worker.error.connect(worker.deleteLater)
                thread.finished.connect(thread.deleteLater)
                thread.started.connect(worker.run)
                thread.start()
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))
