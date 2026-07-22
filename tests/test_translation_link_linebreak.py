"""Regressionstest: fehlerhafter Zeilenumbruch + Link-Verlust an Run-Grenzen
bei externen Übersetzungs-Providern (DeepL/Google/ChatGPT, siehe
pipeline/translation/service.py::translate_runs).

Befund (verifiziert an frisch generiertem Test-ODT): an der Grenze zwischen
einem kurzen, unformatierten Run ("*") und einem Link-Run ("lobstr.co")
wurde in der Übersetzung 1) ein <text:line-break/> eingefügt, das im
Original nicht vorhanden war, und 2) die <text:a>-Verlinkung ist komplett
verloren gegangen.

Ursache: mask_runs()/unmask_to_runs() (pipeline/translation/formatting.py)
kodierten href überhaupt nicht (dokumentierte, aber unerwünschte
Einschränkung) und interpretierten JEDEN rohen "\\n"-Zeichen im
Provider-Output 1:1 als echten LineBreak - ein Provider, der beim
Reformatieren einen Zeilenumbruch an einer Run-Grenze einfügt (z.B. ChatGPT),
erzeugte dadurch einen Zeilenumbruch, der im Original keine Entsprechung
hatte.

Fix: href wird jetzt als <a href="...">...</a>-Tag mitkodiert (rekursiv
aufgelöst beim Zurücklesen), LineBreak wird als explizites <lb/>-Tag
kodiert statt als rohes "\\n" - nur <lb/> gilt beim Zurücklesen als echter
Zeilenumbruch, rohe "\\n"/"\\r" im übersetzten Text werden zu Leerzeichen
normalisiert.

Kein pytest im Projekt (siehe requirements.txt) - eigenständiges Skript wie
die übrigen tests/test_*.py. Aufruf:
    .venv/bin/python tests/test_translation_link_linebreak.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.runs import RecordPair, RunsRecord, TextRun  # noqa: E402
from pipeline.odt_writer import write_odt_for_record_pairs  # noqa: E402
from pipeline.translation.base import TranslationResult  # noqa: E402
from pipeline.translation.formatting import mask_runs, unmask_to_runs  # noqa: E402
from pipeline.translation.service import translate_runs  # noqa: E402

TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
NS = {"text": TEXT_NS, "table": TABLE_NS}


class SpuriousLineBreakProvider:
    """Simuliert einen Provider (analog zu ChatGPT-Reformatierung), der beim
    Übersetzen einen zusätzlichen Zeilenumbruch an einer Run-Grenze einfügt -
    genau das am Testdokument beobachtete Symptom."""
    name = "fake"

    async def translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> TranslationResult:
        # Fuegt nach dem ersten </b> einen rohen Zeilenumbruch ein - simuliert
        # einen Provider, der an einer Tag-/Run-Grenze umformatiert.
        spurious = text.replace("</b>", "</b>\n", 1)
        return TranslationResult(text=spurious, provider=self.name, target_lang=target_lang, source_lang=source_lang)


def test_unmask_preserves_link_and_ignores_spurious_linebreak() -> None:
    """Unit-Ebene: mask_runs()/unmask_to_runs() direkt, mit demselben
    Run-Muster wie im Befund (Fett-Text, einzelnes "*", Link-Run)."""
    runs = [
        TextRun(kind="TextRun", text="QSIGF-COLLATERALIA", bold=True),
        TextRun(kind="TextRun", text="*"),
        TextRun(kind="TextRun", text="lobstr.co", href="lobstr.co"),
    ]
    masked, emoji_map = mask_runs(runs)
    assert '<a href="lobstr.co">lobstr.co</a>' in masked, f"href sollte beim Maskieren erhalten bleiben: {masked!r}"

    # Provider fuegt einen rohen Zeilenumbruch an einer Run-Grenze ein.
    spurious = masked.replace("</b>", "</b>\n", 1)
    result, _found = unmask_to_runs(spurious, emoji_map)

    from pipeline.runs import LineBreak
    linebreaks = [r for r in result if isinstance(r, LineBreak)]
    assert not linebreaks, f"kein LineBreak erwartet (Original hatte keinen), gefunden: {result}"

    link_runs = [r for r in result if isinstance(r, TextRun) and r.href]
    assert len(link_runs) == 1, f"erwartet genau 1 Run mit href, gefunden: {link_runs}"
    assert link_runs[0].href == "lobstr.co" and link_runs[0].text == "lobstr.co"

    print("[OK] unmask_to_runs: href erhalten, spurious Zeilenumbruch korrekt ignoriert (zu Leerzeichen normalisiert).")


def test_translate_runs_end_to_end_no_extra_linebreak_same_link_count() -> None:
    """Über translate_runs() (die tatsächliche Produktionsfunktion) mit einem
    Fake-Provider, der wie im Befund einen Zeilenumbruch einstreut."""
    runs = [
        TextRun(kind="TextRun", text="QSIGF-COLLATERALIA", bold=True),
        TextRun(kind="TextRun", text="*"),
        TextRun(kind="TextRun", text="lobstr.co", href="lobstr.co"),
    ]
    translated_runs, _result = asyncio.run(
        translate_runs(runs, "de", SpuriousLineBreakProvider())
    )

    from pipeline.runs import LineBreak
    assert not any(isinstance(r, LineBreak) for r in translated_runs), (
        f"translate_runs() sollte keinen zusaetzlichen LineBreak durchreichen: {translated_runs}"
    )
    orig_link_count = sum(1 for r in runs if isinstance(r, TextRun) and r.href)
    tr_link_count = sum(1 for r in translated_runs if isinstance(r, TextRun) and r.href)
    assert orig_link_count == tr_link_count == 1, (
        f"Link-Anzahl sollte erhalten bleiben: Original={orig_link_count}, Übersetzung={tr_link_count}"
    )
    print("[OK] translate_runs(): kein zusaetzlicher LineBreak, Link-Anzahl unveraendert.")


def test_odt_output_has_matching_link_and_linebreak_counts() -> None:
    """End-to-End: generiertes ODT - EN- und DE-Zelle sollen dieselbe Anzahl
    text:a-Elemente haben und die DE-Zelle keine zusaetzlichen
    text:line-break-Elemente ohne Entsprechung im Original."""
    orig_runs = [
        TextRun(kind="TextRun", text="QSIGF-COLLATERALIA", bold=True),
        TextRun(kind="TextRun", text="*"),
        TextRun(kind="TextRun", text="lobstr.co", href="lobstr.co"),
    ]
    translated_runs, _ = asyncio.run(translate_runs(orig_runs, "de", SpuriousLineBreakProvider()))

    pair = RecordPair(
        original=RunsRecord(chat="Chat", message_id=1, runs=orig_runs),
        translation=RunsRecord(chat="Chat - DE", message_id=1, runs=translated_runs),
    )

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "test_link_linebreak.odt"
        write_odt_for_record_pairs([pair], out_path, styles={}, doc_title="Test")

        with zipfile.ZipFile(out_path) as z:
            content = z.read("content.xml")
        root = ET.fromstring(content)
        rows = root.findall(".//table:table-row", NS)[1:]
        assert len(rows) == 1
        cells = rows[0].findall("table:table-cell", NS)
        assert len(cells) == 2
        en_cell, de_cell = cells

        en_links = len(en_cell.findall(".//text:a", NS))
        de_links = len(de_cell.findall(".//text:a", NS))
        en_breaks = len(en_cell.findall(".//text:line-break", NS))
        de_breaks = len(de_cell.findall(".//text:line-break", NS))

        print(f"EN: {en_links} text:a, {en_breaks} text:line-break | DE: {de_links} text:a, {de_breaks} text:line-break")
        assert en_links == de_links == 1, f"erwartet je 1 text:a in EN/DE, gefunden EN={en_links} DE={de_links}"
        assert en_breaks == de_breaks == 0, f"erwartet 0 text:line-break in EN/DE, gefunden EN={en_breaks} DE={de_breaks}"

        print("[OK] ODT-Ausgabe: identische text:a-Anzahl EN/DE, kein zusaetzlicher text:line-break in DE.")


if __name__ == "__main__":
    test_unmask_preserves_link_and_ignores_spurious_linebreak()
    test_translate_runs_end_to_end_no_extra_linebreak_same_link_count()
    test_odt_output_has_matching_link_and_linebreak_counts()
    print("ALLE TESTS BESTANDEN")
