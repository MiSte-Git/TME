"""Regressionstest: Bild-Duplizierung ins side_by_side-Nebeneinander-Layout.

Befund: die Übersetzungs-Tabelle hatte pro Spalte unterschiedlich viele
draw:frame-Elemente (EN mehr als DE) - vollständige Nachrichtenbilder
(ImageRun) landeten nie in der Übersetzungsseite, weil weder
translate_runs() noch der Telegram-native Übersetzungspfad je ein ImageRun
zu Gesicht bekommen (sie arbeiten nur auf dem reinen Nachrichtentext).
Inline-Custom-Emojis (EmojiRun) waren davon nicht betroffen, weil sie den
Masken-/Übersetzungs-Rundlauf selbst überleben.

Kein pytest im Projekt (siehe requirements.txt) - eigenständiges Skript wie
die übrigen Ad-hoc-Testskripte dieser Codebase, hier aber dauerhaft im Repo,
da explizit als Regressionstest angefordert. Aufruf:
    .venv/bin/python tests/test_side_by_side_images.py
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PIL import Image as PILImage  # noqa: E402

from pipeline.runs import ImageRun, RecordPair, RunsRecord, TextRun  # noqa: E402
from pipeline.odt_writer import write_odt_for_record_pairs  # noqa: E402
from pipeline.runner_schedule import _duplicate_images_into_translation_record  # noqa: E402

NS = {"draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
      "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0"}


def _make_test_png(path: Path) -> None:
    PILImage.new("RGB", (200, 300), color="red").save(path)


def _count_frames_per_table_cell(odt_path: Path) -> list[tuple[int, int]]:
    """Liest content.xml und liefert pro Tabellenzeile (draw:frame in Zelle
    1, draw:frame in Zelle 2) - eine Tabellenzeile entspricht einer
    Nachricht im side_by_side-Layout (siehe write_odt_for_record_pairs)."""
    with zipfile.ZipFile(odt_path) as z:
        content = z.read("content.xml")
    root = ET.fromstring(content)
    rows = root.findall(".//table:table-row", NS)
    counts = []
    for row in rows:
        cells = row.findall("table:table-cell", NS)
        if len(cells) != 2:
            continue
        n1 = len(cells[0].findall(".//draw:frame", NS))
        n2 = len(cells[1].findall(".//draw:frame", NS))
        counts.append((n1, n2))
    return counts


def test_duplicate_images_into_translation_record_pure() -> None:
    """Reine Unit-Logik ohne ODT-Rendering: prüft alle drei Fälle der
    Hilfsfunktion isoliert."""
    img = ImageRun(kind="ImageRun", path="fake.png", width_cm=10.0)
    txt_en = TextRun(kind="TextRun", text="hello")
    original_runs = [img, txt_en]

    # Fall 1: Übersetzung vorhanden -> Bild wird vorn eingefügt
    tr = RunsRecord(chat="c - DE", message_id=1, runs=[TextRun(kind="TextRun", text="hallo")])
    result = _duplicate_images_into_translation_record(original_runs, tr, "c - DE", 1)
    assert result is tr, "sollte den bestehenden Record in-place erweitern"
    assert isinstance(result.runs[0], ImageRun) and result.runs[0] is img, \
        f"Bild sollte als erstes Element übernommen werden, war: {result.runs}"
    assert len(result.runs) == 2

    # Fall 2: keine Übersetzung vorhanden (Nachricht ohne Caption) -> neuer
    # Record nur mit dem Bild, statt das Bild zu verlieren
    result2 = _duplicate_images_into_translation_record(original_runs, None, "c - DE", 1)
    assert result2 is not None
    assert [type(r).__name__ for r in result2.runs] == ["ImageRun"]

    # Fall 3: kein Bild im Original -> unverändert durchgereicht (kein
    # unnötiger neuer Record, keine Seiteneffekte)
    tr3 = RunsRecord(chat="c - DE", message_id=2, runs=[TextRun(kind="TextRun", text="hallo")])
    result3 = _duplicate_images_into_translation_record([txt_en], tr3, "c - DE", 2)
    assert result3 is tr3
    assert len(result3.runs) == 1

    print("[OK] _duplicate_images_into_translation_record: alle drei Faelle korrekt")


def test_side_by_side_odt_frame_counts_match() -> None:
    """End-to-End: erzeugt ein side_by_side-ODT mit einer Bild-Nachricht und
    einer reinen Text-Nachricht (inkl. Inline-Emoji) und prüft direkt über
    content.xml, dass EN- und DE-Zelle pro Zeile dieselbe Anzahl
    draw:frame-Elemente haben - exakt der vom User beschriebene Befund
    (EN-Zelle 39, DE-Zelle 38 draw:frame)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        img_path = tmp_path / "msg_image.png"
        _make_test_png(img_path)

        # Nachricht 1: Bild + Text, Original + Übersetzung. translation_record
        # wird hier wie in runner_schedule.py NACH
        # _duplicate_images_into_translation_record() aufgebaut - also mit
        # dem Fix, nicht wie vor dem Fix (kein Bild in tr_runs). Bewusst ohne
        # EmojiRun (dessen Rendering braucht eine gecachte PNG unter
        # cache/emoji/<id>.png, siehe _add_emoji_as_char - das war laut
        # Befund ohnehin nicht das betroffene Verhalten, nur volle Bilder).
        orig_runs_1 = [
            ImageRun(kind="ImageRun", path=str(img_path), width_cm=10.0),
            TextRun(kind="TextRun", text="Hello"),
        ]
        tr_runs_1 = [TextRun(kind="TextRun", text="Hallo")]
        tr_runs_1 = _duplicate_images_into_translation_record(orig_runs_1, RunsRecord(chat="Chat - DE", message_id=1, runs=tr_runs_1), "Chat - DE", 1).runs

        pair1 = RecordPair(
            original=RunsRecord(chat="Chat", message_id=1, runs=orig_runs_1),
            translation=RunsRecord(chat="Chat - DE", message_id=1, runs=tr_runs_1),
        )

        # Nachricht 2: reiner Text ohne Bild, zur Kontrolle (0 == 0 Frames).
        pair2 = RecordPair(
            original=RunsRecord(chat="Chat", message_id=2, runs=[TextRun(kind="TextRun", text="No image here")]),
            translation=RunsRecord(chat="Chat - DE", message_id=2, runs=[TextRun(kind="TextRun", text="Kein Bild hier")]),
        )

        out_path = tmp_path / "test_side_by_side.odt"
        write_odt_for_record_pairs(
            [pair1, pair2], out_path, styles={},
            doc_title="Test", original_label="Original (EN)", translation_label="Übersetzung (DE)",
        )

        counts = _count_frames_per_table_cell(out_path)
        # counts[0] ist die Kopfzeile (Original/Übersetzung-Beschriftung, keine Frames)
        data_rows = counts[1:]
        assert len(data_rows) == 2, f"erwartet 2 Datenzeilen (eine pro Nachricht), gefunden: {len(data_rows)}"

        en1, de1 = data_rows[0]
        assert en1 == de1, f"Nachricht 1 (mit Bild): EN={en1} draw:frame, DE={de1} draw:frame - sollten gleich sein"
        assert en1 == 1, f"erwartet 1 draw:frame (Bild) in Nachricht 1, war {en1}"

        en2, de2 = data_rows[1]
        assert en2 == de2 == 0, f"Nachricht 2 (ohne Bild): erwartet 0/0 draw:frame, war {en2}/{de2}"

        print(f"[OK] side_by_side ODT: EN/DE draw:frame-Anzahl stimmt pro Zeile ueberein ({data_rows})")


if __name__ == "__main__":
    test_duplicate_images_into_translation_record_pure()
    test_side_by_side_odt_frame_counts_match()
    print("ALLE TESTS BESTANDEN")
