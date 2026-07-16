from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QListWidget, QFileDialog, QMessageBox
)

from pipeline.no_translate_words import (
    NO_TRANSLATE_WORDS_FILE,
    load_no_translate_words,
    save_no_translate_words,
    export_csv,
    import_csv,
)


class NoTranslateWordsTab(QWidget):
    """Pflege der Ausnahmeliste für Emoji-Wörter, die NICHT übersetzt werden
    sollen (z.B. Namen, feststehende Ausdrücke). Getrennt von letter_map.json,
    aber im UI-Aufbau analog zum Lettermap-Tab (Reload/Speichern/CSV)."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        self.info_label = QLabel(self.tr(
            "Emoji-Wörter (aus mit Buchstaben-Emojis geschriebenem Text erkannt), "
            "die hier stehen, werden bei der Übersetzung NICHT übersetzt und bleiben "
            "als Emoji-Sequenz erhalten."
        ))
        self.info_label.setWordWrap(True)
        lay.addWidget(self.info_label)

        add_lay = QHBoxLayout()
        add_lay.setSpacing(8)
        self.word_edit = QLineEdit()
        self.word_edit.setPlaceholderText(self.tr("Wort eingeben…"))
        self.word_edit.returnPressed.connect(self.add_word)
        self.btn_add = QPushButton(self.tr("Hinzufügen"))
        self.btn_add.clicked.connect(self.add_word)
        self.btn_remove = QPushButton(self.tr("Entfernen"))
        self.btn_remove.clicked.connect(self.remove_selected)
        add_lay.addWidget(self.word_edit)
        add_lay.addWidget(self.btn_add)
        add_lay.addWidget(self.btn_remove)
        lay.addLayout(add_lay)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        lay.addWidget(self.list_widget)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_reload = QPushButton(self.tr("Neu laden"))
        self.btn_reload.clicked.connect(self.reload_data)
        self.btn_save = QPushButton(self.tr("Speichern"))
        self.btn_save.clicked.connect(self.save_data)
        self.btn_import = QPushButton(self.tr("CSV importieren…"))
        self.btn_import.clicked.connect(self.import_csv_dialog)
        self.btn_export = QPushButton(self.tr("CSV exportieren…"))
        self.btn_export.clicked.connect(self.export_csv_dialog)
        bottom.addWidget(self.btn_reload)
        bottom.addWidget(self.btn_save)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_import)
        bottom.addWidget(self.btn_export)
        lay.addLayout(bottom)

        self.reload_data()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def retranslate(self) -> None:
        self.info_label.setText(self.tr(
            "Emoji-Wörter (aus mit Buchstaben-Emojis geschriebenem Text erkannt), "
            "die hier stehen, werden bei der Übersetzung NICHT übersetzt und bleiben "
            "als Emoji-Sequenz erhalten."
        ))
        self.word_edit.setPlaceholderText(self.tr("Wort eingeben…"))
        self.btn_add.setText(self.tr("Hinzufügen"))
        self.btn_remove.setText(self.tr("Entfernen"))
        self.btn_reload.setText(self.tr("Neu laden"))
        self.btn_save.setText(self.tr("Speichern"))
        self.btn_import.setText(self.tr("CSV importieren…"))
        self.btn_export.setText(self.tr("CSV exportieren…"))

    # ---- Daten ----

    def reload_data(self) -> None:
        try:
            words = load_no_translate_words(NO_TRANSLATE_WORDS_FILE)
            self.list_widget.clear()
            self.list_widget.addItems(words)
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))

    def _current_words(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def save_data(self) -> None:
        try:
            save_no_translate_words(self._current_words(), NO_TRANSLATE_WORDS_FILE)
            QMessageBox.information(
                self, self.tr("Gespeichert"),
                self.tr("Ausnahmeliste gespeichert nach {file}.").format(file=str(NO_TRANSLATE_WORDS_FILE)),
            )
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))

    def add_word(self) -> None:
        word = self.word_edit.text().strip()
        if not word:
            return
        existing = {w.upper() for w in self._current_words()}
        if word.upper() not in existing:
            self.list_widget.addItem(word)
            self.list_widget.sortItems()
        self.word_edit.clear()

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def import_csv_dialog(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, self.tr("CSV importieren"), str(Path.cwd() / "data"), self.tr("CSV-Dateien (*.csv)")
        )
        if not path_str:
            return
        try:
            words = import_csv(Path(path_str), NO_TRANSLATE_WORDS_FILE, merge=True)
            self.list_widget.clear()
            self.list_widget.addItems(words)
            QMessageBox.information(self, self.tr("Importiert"), self.tr("{n} Wort/Wörter importiert.").format(n=len(words)))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))

    def export_csv_dialog(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self, self.tr("CSV exportieren"), str(Path.cwd() / "data" / "no_translate_words.csv"), self.tr("CSV-Dateien (*.csv)")
        )
        if not path_str:
            return
        try:
            save_no_translate_words(self._current_words(), NO_TRANSLATE_WORDS_FILE)
            out = export_csv(Path(path_str), NO_TRANSLATE_WORDS_FILE)
            QMessageBox.information(self, self.tr("Exportiert"), self.tr("Exportiert nach {file}.").format(file=str(out)))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Fehler"), str(e))
