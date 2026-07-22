"""Regressionstest: Message-Header (Zeitstempel/Link/Kanal) im
side_by_side-Nebeneinander-Layout.

Verlauf: zunächst fehlte der P.MessageHeader-Absatz in der DE-Zelle komplett
(die Ursache lag NICHT in runner_schedule.py - header_runs landet dort
bereits korrekt in tr_meta - sondern rein im Rendering in odt_writer.py).
Der erste Fix duplizierte den Header 1:1 in beide Zellen. Da der Header
sprachunabhängig ist (Zeitstempel/Link/Kanal), ist die sauberere Lösung
stattdessen eine eigene, spaltenübergreifende Header-Zeile VOR der
zweispaltigen Datenzeile jeder Nachricht (table:number-columns-spanned=2 +
covered-table-cell für die zweite Spalte) - kein Duplikat mehr in den
Datenzellen selbst.

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

TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
NS = {"text": TEXT_NS, "table": TABLE_NS}


def _rows_after_header(odt_path: Path) -> list:
    """Alle table:table-row-Elemente ohne die erste Zeile (Spalten-
    überschriften Original/Übersetzung)."""
    with zipfile.ZipFile(odt_path) as z:
        content = z.read("content.xml")
    root = ET.fromstring(content)
    return root.findall(".//table:table-row", NS)[1:]


def _header_text(cell) -> str | None:
    ps = [p for p in cell.findall("text:p", NS) if p.get(f"{{{TEXT_NS}}}style-name") == "P.MessageHeader"]
    if not ps:
        return None
    return "".join(ps[0].itertext())


def test_header_row_spans_both_columns_once_per_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # Trailing LineBreak am Ende bewusst wie in runner_schedule.py (jede
        # Kopfzeile bekommt dort der Einfachheit halber einen nachgestellten
        # LineBreak, auch die letzte - siehe _build_header_paragraph in
        # odt_writer.py für den Fix, der diesen überflüssigen Zeilenumbruch
        # vor </text:p> entfernt).
        header_runs = [
            TextRun(kind="TextRun", text="2026-01-01 12:00:00 – Bild"),
            LineBreak(kind="LineBreak"),
            TextRun(kind="TextRun", text="https://t.me/testchannel/42", href="https://t.me/testchannel/42", bold=True, underline=True),
            LineBreak(kind="LineBreak"),
            TextRun(kind="TextRun", text="@testchannel (Test Channel)"),
            LineBreak(kind="LineBreak"),
        ]
        meta = {"header_runs": header_runs, "link": "https://t.me/testchannel/42"}

        # Nachricht 1: mit Übersetzung
        pair1 = RecordPair(
            original=RunsRecord(chat="Chat", message_id=1, runs=[TextRun(kind="TextRun", text="Hello")], meta=meta),
            translation=RunsRecord(chat="Chat - DE", message_id=1, runs=[TextRun(kind="TextRun", text="Hallo")]),
        )
        # Nachricht 2: OHNE Übersetzung (pair.translation is None) - die
        # Header-Zeile muss trotzdem erscheinen (siehe else-Zweig in
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

        rows = _rows_after_header(out_path)
        # Pro Nachricht: 1 Header-Zeile + 1 Datenzeile = 2 Zeilen -> 4 insgesamt
        assert len(rows) == 4, f"erwartet 4 Zeilen (2x Header+Daten fuer 2 Nachrichten), gefunden {len(rows)}"

        for msg_idx in range(2):
            header_row = rows[msg_idx * 2]
            data_row = rows[msg_idx * 2 + 1]

            # Header-Zeile: genau eine table:table-cell mit
            # number-columns-spanned="2", gefolgt von einer
            # covered-table-cell - keine zweite eigenständige Zelle.
            header_cells = header_row.findall("table:table-cell", NS)
            covered_cells = header_row.findall("table:covered-table-cell", NS)
            assert len(header_cells) == 1, f"Nachricht {msg_idx + 1}: Header-Zeile sollte genau 1 table-cell haben, war {len(header_cells)}"
            assert len(covered_cells) == 1, f"Nachricht {msg_idx + 1}: Header-Zeile sollte genau 1 covered-table-cell haben, war {len(covered_cells)}"
            span = header_cells[0].get(f"{{{TABLE_NS}}}number-columns-spanned")
            assert span == "2", f"Nachricht {msg_idx + 1}: erwartet number-columns-spanned='2', war {span!r}"

            header_text = _header_text(header_cells[0])
            assert header_text is not None, f"Nachricht {msg_idx + 1}: kein P.MessageHeader-Absatz in der Header-Zeile gefunden"
            assert "2026-01-01 12:00:00" in header_text and "@testchannel" in header_text

            # Kein ueberfluessiger <text:line-break/> unmittelbar vor
            # </text:p> - obwohl header_runs oben mit einem trailing
            # LineBreak endet (wie in runner_schedule.py).
            header_p = [p for p in header_cells[0].findall("text:p", NS) if p.get(f"{{{TEXT_NS}}}style-name") == "P.MessageHeader"][0]
            children = list(header_p)
            assert children, f"Nachricht {msg_idx + 1}: Header-Absatz sollte nicht leer sein"
            assert children[-1].tag != f"{{{TEXT_NS}}}line-break", (
                f"Nachricht {msg_idx + 1}: letztes Kind des Header-Absatzes sollte KEIN line-break sein, "
                f"war {children[-1].tag}"
            )

            # Datenzeile: genau 2 normale Zellen, OHNE eigenen
            # P.MessageHeader-Absatz (nicht mehr dupliziert).
            data_cells = data_row.findall("table:table-cell", NS)
            assert len(data_cells) == 2, f"Nachricht {msg_idx + 1}: Datenzeile sollte 2 Zellen haben, war {len(data_cells)}"
            for cell in data_cells:
                assert _header_text(cell) is None, (
                    f"Nachricht {msg_idx + 1}: Datenzelle sollte keinen P.MessageHeader-Absatz mehr enthalten "
                    f"(jetzt in eigener Zeile) - gefunden: {_header_text(cell)!r}"
                )

        print(f"[OK] Pro Nachricht genau 1 spaltenübergreifende Header-Zeile (number-columns-spanned=2) "
              f"+ 1 Datenzeile ohne Header-Duplikat, auch ohne Übersetzung (Nachricht 2).")


if __name__ == "__main__":
    test_header_row_spans_both_columns_once_per_message()
    print("ALLE TESTS BESTANDEN")
