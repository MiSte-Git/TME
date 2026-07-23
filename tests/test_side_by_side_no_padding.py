"""Regressionstest: side_by_side-Layout blähte Dokumente durch Leer-Absätze
massiv auf (Befund: eine einzelne Nachricht erzeugte 46 EN- bzw. 22
DE-Absätze, davon 46 bzw. 22 komplett leer).

Ursache war die satzweise Zeilen-Ausgleichslogik (_render_sentence_balanced/
_CHARS_PER_CM/_split_runs_into_sections/_estimate_section_lines): sie
versuchte EN/DE-Spalten durch Auffüllen mit Leer-Absätzen auf gleicher Höhe
zu halten - unnötig, da die Tabelle bereits eine echte Zeile pro Nachricht
hat und ODF/LibreOffice die Zeilenhöhe automatisch an die längere Spalte
anpasst (siehe write_odt_for_record_pairs). Die gesamte Balancierungslogik
wurde entfernt; beide Zellen werden jetzt einfach vollständig über
render_runs_into_container() gerendert.

Kein pytest im Projekt (siehe requirements.txt) - eigenständiges Skript wie
die übrigen tests/test_*.py. Aufruf:
    .venv/bin/python tests/test_side_by_side_no_padding.py
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.runs import RecordPair, RunsRecord, TextRun  # noqa: E402
from pipeline.odt_writer import write_odt_for_record_pairs  # noqa: E402

TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
NS = {"text": TEXT_NS, "table": TABLE_NS}

# Viele Sätze mit deutlich unterschiedlicher EN/DE-Länge - genau das Muster,
# das die frühere Balancierungslogik zum Aufblähen brachte.
EN_TEXT = " ".join([
    "Buy now.", "The market moved sharply today after the announcement.",
    "Sell.", "Analysts expect continued volatility in the coming weeks as investors digest the news.",
    "Hold your position.", "This is a very important update regarding the collateral requirements for the fund.",
    "Watch closely.", "The token allocation schedule has been revised following community feedback on the proposal.",
])
DE_TEXT = " ".join([
    "Jetzt kaufen.", "Der Markt bewegte sich heute nach der Ankündigung stark und unerwartet in beide Richtungen.",
    "Verkaufen.", "Analysten erwarten in den kommenden Wochen weiterhin erhebliche Volatilität, während Anleger die Nachrichten verarbeiten.",
    "Position halten.", "Dies ist ein sehr wichtiges Update bezüglich der Sicherheitsanforderungen für den Fonds.",
    "Genau beobachten.", "Der Zeitplan für die Token-Zuteilung wurde nach Feedback der Community zum Vorschlag überarbeitet.",
])


def test_no_empty_padding_paragraphs() -> None:
    pair = RecordPair(
        original=RunsRecord(chat="Chat", message_id=1, runs=[TextRun(kind="TextRun", text=EN_TEXT)]),
        translation=RunsRecord(chat="Chat - DE", message_id=1, runs=[TextRun(kind="TextRun", text=DE_TEXT)]),
    )
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "test_no_padding.odt"
        write_odt_for_record_pairs([pair], out_path, styles={}, doc_title="Test")

        with zipfile.ZipFile(out_path) as z:
            content = z.read("content.xml")
        root = ET.fromstring(content)
        rows = root.findall(".//table:table-row", NS)[1:]  # ohne Tabellen-Kopfzeile
        assert len(rows) == 1, f"erwartet 1 Datenzeile (keine eigene Header-Zeile ohne header_runs), gefunden {len(rows)}"
        cells = rows[0].findall("table:table-cell", NS)
        assert len(cells) == 2

        for label, cell in zip(("EN", "DE"), cells):
            paragraphs = cell.findall("text:p", NS)
            empty = [p for p in paragraphs if not "".join(p.itertext()).strip()]
            assert not empty, f"{label}-Zelle sollte keine leeren Absätze mehr enthalten, gefunden: {len(empty)} von {len(paragraphs)}"
            assert len(paragraphs) == 1, f"{label}-Zelle sollte die Nachricht als einen Absatz rendern, gefunden: {len(paragraphs)}"

        print(f"[OK] Keine Leer-Absätze mehr: EN-Zelle 1 Absatz, DE-Zelle 1 Absatz (vorher: satzweise + Auffüllung).")


if __name__ == "__main__":
    test_no_empty_padding_paragraphs()
    print("ALLE TESTS BESTANDEN")
