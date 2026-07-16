# TME – Telegram-to-ODT Message Exporter

![Telegram → LibreOffice](Telegram-LibreOffice.png)

Werkzeugkasten zum Sammeln von Telegram-Nachrichten und dem Erzeugen von ODT-Dokumenten – inklusive Übersetzungen, Bild- und Emoji-Einbettung. Der Fokus liegt auf dem komfortablen UI-Workflow (`python3 ui/app.py`), der alle Schritte von der Schedule-Datei bis zum fertigen ODT orchestriert.

## Features
- Schedule-Dateien (TXT oder JSON) einlesen, Nachrichten abrufen und als ODT exportieren
- Optional Übersetzungen anhängen (inline, am Ende oder als separates Dokument)
- Austauschbarer Übersetzungs-Provider: Telegram (Default, kein API-Key nötig),
  DeepL, Google Translate oder ChatGPT/OpenAI (`translation.provider` in
  `config.yaml`, `--provider` im CLI, Dropdown im UI); grobe Kostenschätzung
  nach jedem Lauf mit externem Provider (siehe `docs/DEPLOY.md`)
- Emoji-Wort-Erkennung: mit Buchstaben-Emojis geschriebene Wörter (laut
  `data/letter_map.json`) werden bei externen Übersetzungs-Providern erkannt
  und mitübersetzt (als Klartext, keine Rückübersetzung in Emoji-Sequenzen),
  außer sie stehen auf der erweiterbaren Ausnahmeliste `data/no_translate_words.json`
  (Tab „Nicht übersetzen" im UI, inkl. CSV-Import/Export)
- Medien und Custom-Emojis als Bilder einbetten
- Optional: Nachrichten mehrerer Kanäle chronologisch mischen statt blockweise pro
  Kanal ausgeben (`interleave_channels` in `config.yaml` bzw. Checkbox „Kanäle
  chronologisch mischen" im UI; Kanalname bleibt als Label pro Nachricht sichtbar)
- Optional: inkrementelles Dokument-Update über einen persistenten Message-Store
  (`incremental_mode` in `config.yaml` bzw. Checkbox „Inkrementelles Update
  (Store)" im UI) - pro Lauf werden nur neue Nachrichten je Kanal/Zeitfenster
  geholt (inkl. bereits übersetzter Runs, keine doppelten Übersetzungskosten),
  das Dokument wird komplett aus dem Store neu geschrieben statt angehängt
- Optional: Layout „Übersetzung neben Original" (`layout: side_by_side` in
  `config.yaml` bzw. Dropdown im UI) - Original und Übersetzung als
  zweispaltige Tabellenzeile pro Nachricht statt hintereinander; fehlt eine
  Übersetzung, zeigt die Zelle einen Platzhaltertext
- Automatisches Nachladen fehlender Emoji-PNGs und Reporting
- Übergreifender CLI-Einstieg (`pipeline/emoji_pipeline.py`) für Skript-Workflows

## Voraussetzungen
- Python 3.11+
- Abhängigkeiten installieren:
  ```bash
  python3 -m pip install -r requirements.txt
  ```
- Telegram API-Credentials als Umgebungsvariablen setzen (https://my.telegram.org):
  ```bash
  export TELEGRAM_API_ID=123456
  export TELEGRAM_API_HASH="abcdef..."
  ```
  Unter Windows PowerShell entsprechend `setx TELEGRAM_API_ID 123456` usw.
  Alternativ: private `credentials.json` unter `~/.config/telegram-odt/` (Details in
  [docs/DEPLOY.md](docs/DEPLOY.md)).

## Projektstruktur
- `ui/app.py` – Qt-basierte Oberfläche (Schedule-Tab, Lettermap-Tab)
- `pipeline/` – Kernlogik für Telegram-Abfragen, Emoji-Assets, ODT Writer
- `input/` – Beispiel-Schedules (TXT/JSON)
- `output/` – erzeugte ODTs
- `data/` – Laufzeitdaten (letter_map.json, reports, UI-Status)
- `media/` & `cache/` – gespeicherte Medien bzw. Emoji-PNGs

## Schnellstart (UI)
1. Abhängigkeiten installieren und API-Credentials setzen.
2. UI starten:
   ```bash
   python3 ui/app.py
   ```
3. Im Schedule-Tab die gewünschte Datei wählen (`input/…`) und Optionen setzen.
4. „Schedule → ODT erzeugen“ starten; Fortschritt, ggf. fehlende Mappings und Ergebnisdialog erscheinen direkt in der Oberfläche.
5. UI merkt sich die letzten Einstellungen in `data/ui_state.json`. Existiert die zuletzt gewählte Datei nicht mehr, bleibt das Feld leer.

## CLI-Workflows
Das Skript `pipeline/emoji_pipeline.py` bündelt verschiedene Teilaufgaben:
```bash
python3 pipeline/emoji_pipeline.py by-date --schedule input/links.txt --mode inline --translate 1 --lang de
python3 pipeline/emoji_pipeline.py collect-letters --links input/links.txt
python3 pipeline/emoji_pipeline.py extract-plain --links input/links.txt
```
Details zu den Subcommands stehen im Quelltext (`pipeline/emoji_pipeline.py`). Für alle Befehle mit Telegram-Zugriff gelten die oben genannten API-Variablen.

## Mapping/Lettermap
Der Lettermap-Tab im UI und die zugehörigen Dateien (`data/letter_map.json`, `data/lettermap_ignore.json`) waren ursprünglich für ein Emoji-zu-Buchstaben-Mapping vorgesehen. Aktuell ist dieser Schritt optional; die ODT-Erzeugung funktioniert auch ohne weitere Eingriffe. Das Mapping-Feature bleibt als Vorbereitung für künftige Erweiterungen im Projekt.

## Installation & Build (Desktop-Bundles)
Für fertige Desktop-Bundles (macOS `.app`, Windows `.exe`, Linux-Desktop-Eintrag) sowie
Details zur Ablage der Telegram-API-Keys siehe [docs/DEPLOY.md](docs/DEPLOY.md).

## Hintergrund & Architektur
Für Contributor:innen, die tiefer in Aufbau und Entstehung der Pipeline einsteigen wollen:
- [docs/projekt-struktur.md](docs/projekt-struktur.md) – Architekturüberblick (PySide6, Module, UI-Screens)
- [docs/emoji-odt-kontext.md](docs/emoji-odt-kontext.md) – ursprüngliches Konzept inkl. JSON-Schemas

## Geplante Features (Roadmap)
- **OCR (Tesseract/EasyOCR):** In früheren Planungen vorgesehen, aktuell **nicht implementiert**
  – im Code gibt es keine `pytesseract`/`easyocr`-Imports. Nicht in `requirements.txt`
  oder im Install-Befehl oben enthalten; wird ergänzt, sobald die Funktion umgesetzt ist.

## Entwicklung
- Syntax-Check: `python3 -m compileall -q .`
- Debug-Ausgaben und Reports werden unter `data/` erzeugt (z. B. `missing_lettermap_docs.json`).
- Vor Pull-Requests bitte sicherstellen, dass UI und CLI-Läufe mit einer Beispiel-Schedule erfolgreich sind.

## Lizenz
Copyright (C) 2026 MiSte-Git

Dieses Projekt steht unter der GNU General Public License v3.0 (SPDX: `GPL-3.0-or-later`).
Siehe beiliegende `LICENSE`-Datei für den vollständigen Lizenztext.
