
# Option B: Python-only mit Qt (PySide6)

## Architektur

* UI: PySide6 (Widgets oder QML).
* Logik: deine 875-Zeilen als Modul.
* Worker-Threads für lange Jobs.

## Module

1. **Ingest**: t.me-URLs → `message.json`.
2. **Assets**: `document_id` → PNG (WEBP/WEBM/TGS, ffmpeg+lottie+cairosvg).
3. **Runs**: `runs.original.json` aus Text+Entities.
4. **ODT Original**: `original.odt`.
5. **Letter-Map**: Auto-Vorschlag + UI-Kuratierung → `letter_map.json`.
6. **Plaintext**: `plain_source.txt` (Emoji→Buchstabe).
7. **Recompose**: `runs.translated.json`.
8. **ODT Übersetzung**: `translated.odt`.
9. **Report**: Lücken, Fallbacks, Fehler.

## UI-Screens

* Eingabe (URLs/Range) + Start.
* Assets-Status (gerendert/offen, Set-Infos).
* Vorschau Original.
* Letter-Map Editor (A–Z, 0–9, Satzzeichen).
* Übersetzen (Quelle/Ziel).
* Exporte + Report.

## Packaging

* PyInstaller/Briefcase für Win/Linux/macOS.
* Runtimes bündeln: `ffmpeg`, `lottie`, `cairosvg`.

---

# Gemeinsame Kernpunkte

* **Cache global pro `document_id`** (PNG nur einmal rendern).
* **emoji\_sets.json** mit Set-Metadaten.
* **assets.json** mit `letter_hint` + Pfad.
* **letter\_map.json** einmal kuratieren, dann vollautomatisch.
* **as-char Frames** im ODT für korrekte Zeilenumbrüche.
* **Fehlende Zeichen** → Text-Fallback oder Transliteration.

# Verzeichnisse

```
config.yaml
cache/emoji/<doc_id>.png
data/message[s].json
data/assets.json
data/emoji_sets.json
data/runs.original.json
data/letter_map.json
data/runs.translated.json
out/original.odt
out/translated.odt
out/report.txt
```
