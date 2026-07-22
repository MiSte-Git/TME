"""Regressionstest: Message-Header (Zeitstempel/Link/Kanal) im
side_by_side-Nebeneinander-Layout fehlte in der Übersetzungsspalte.

Befund: die DE-Zelle begann direkt mit dem Nachrichteninhalt, ohne den
P.MessageHeader-Absatz (Zeitstempel, Nachrichtentyp, Link, Kanalname), der
in der EN-Zelle jeder Nachricht vorangeht. Ursache war NICHT eine fehlende
Datenkopie in runner_schedule.py (header_runs landet dort bereits korrekt in
tr_meta) - odt_writer.write_odt_for_record_pairs() hat den Header für die
Übersetzungsspalte schlicht nie gebaut (bewusste, aber unerwünschte
Design-Entscheidung, siehe Kommentar-Historie). Der Header ist
sprachunabhängig (Zeitstempel/Link/Kanal) und wird daher unübersetzt 1:1 in
beide Spalten kopiert - analog zur Bild-Duplizierung, siehe
tests/test_side_by_side_images.py.

Kein pytest im Projekt (siehe requirements.txt) - eigenständiges Skript wie
tests/test_side_by_side_images.py. Aufruf:
    .venv/bin/python tests/test_side_by_side_header.py
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.runs import RecordPair, RunsRecord, TextRun, LineBreak  # noqa: E402
from pipeline.odt_writer import write_odt_for_record_pairs  # noqa: E402

NS = {"text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
      "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0"}


def _header_paragraphs_per_cell(odt_path: Path) -> list[tuple[list[str], list[str]]]:
    """Liest content.xml und liefert pro Datenzeile (Textinhalte der
    P.MessageHeader-Absätze in Zelle 1, in Zelle 2)."""
    with zipfile.ZipFile(odt_path) as z:
        content = z.read("content.xml")
    root = ET.fromstring(content)
    rows = root.findall(".//table:table-row", NS)[1:]  # erste Zeile = Spaltenüberschriften
    result = []
    for row in rows:
        cells = row.findall("table:table-cell", NS)
        assert len(cells) == 2
        texts_per_cell = []
        for cell in cells:
            headers = [p for p in cell.findall("text:p", NS) if p.get(f"{{{NS['text']}}}style-name") == "P.MessageHeader"]
            texts_per_cell.append(["".join(p.itertext()) for p in headers])
        result.append((texts_per_cell[0], texts_per_cell[1]))
    return result


def test_header_present_and_identical_in_both_columns() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        header_runs = [
            TextRun(kind="TextRun", text="2026-01-01 12:00:00 – Bild"),
            LineBreak(kind="LineBreak"),
            TextRun(kind="TextRun", text="https://t.me/testchannel/42", href="https://t.me/testchannel/42", bold=True, underline=True),
            LineBreak(kind="LineBreak"),
            TextRun(kind="TextRun", text="@testchannel (Test Channel)"),
        ]
        meta = {"header_runs": header_runs, "link": "https://t.me/testchannel/42"}

        # Nachricht 1: mit Übersetzung
        pair1 = RecordPair(
            original=RunsRecord(chat="Chat", message_id=1, runs=[TextRun(kind="TextRun", text="Hello")], meta=meta),
            translation=RunsRecord(chat="Chat - DE", message_id=1, runs=[TextRun(kind="TextRun", text="Hallo")]),
        )
        # Nachricht 2: OHNE Übersetzung (pair.translation is None) - Header
        # muss trotzdem in beiden Spalten erscheinen (siehe else-Zweig in
        # write_odt_for_record_pairs).
        pair2 = RecordPair(
            original=RunsRecord(chat="Chat", message_id=2, runs=[TextRun(kind="TextRun", text="No translation")], meta=meta),
            translation=None,
        )

        out_path = Path(tmp) / "test_header.odt"
        write_odt_for_record_pairs(
            [pair1, pair2], out_path, styles={},
            doc_title="Test", original_label="Original (EN)", translation_label="Übersetzung (DE)",
        )

        rows = _header_paragraphs_per_cell(out_path)
        assert len(rows) == 2, f"erwartet 2 Datenzeilen, gefunden {len(rows)}"

        for i, (en_headers, de_headers) in enumerate(rows, 1):
            assert len(en_headers) == 1, f"Nachricht {i}: EN-Zelle sollte genau 1 P.MessageHeader-Absatz haben, war {en_headers}"
            assert len(de_headers) == 1, f"Nachricht {i}: DE-Zelle sollte genau 1 P.MessageHeader-Absatz haben, war {de_headers}"
            assert en_headers[0] == de_headers[0], (
                f"Nachricht {i}: Header-Text weicht zwischen EN und DE ab - "
                f"sollte 1:1 identisch (unübersetzt) sein: EN={en_headers[0]!r} DE={de_headers[0]!r}"
            )
            assert "2026-01-01 12:00:00" in en_headers[0] and "@testchannel" in en_headers[0]

        print(f"[OK] P.MessageHeader in beiden Spalten vorhanden und identisch, "
              f"auch ohne Übersetzung (Nachricht 2): {rows}")


if __name__ == "__main__":
    test_header_present_and_identical_in_both_columns()
    print("ALLE TESTS BESTANDEN")
