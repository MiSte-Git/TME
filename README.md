# Emoji-ODT Pipeline

Werkzeugkasten zum Sammeln von Telegram-Nachrichten und dem Erzeugen von ODT-Dokumenten – inklusive Übersetzungen, Bild- und Emoji-Einbettung. Der Fokus liegt auf dem komfortablen UI-Workflow (`python3 ui/app.py`), der alle Schritte von der Schedule-Datei bis zum fertigen ODT orchestriert.

## Features
- Schedule-Dateien (TXT oder JSON) einlesen, Nachrichten abrufen und als ODT exportieren
- Optional Übersetzungen anhängen (inline, am Ende oder als separates Dokument)
- Medien und Custom-Emojis als Bilder einbetten
- Automatisches Nachladen fehlender Emoji-PNGs und Reporting
- Übergreifender CLI-Einstieg (`emoji_pipeline.py`) für Skript-Workflows

## Voraussetzungen
- Python 3.11+
- Abhängigkeiten installieren:
  ```bash
  python3 -m pip install telethon odfpy pillow pytesseract easyocr
  ```
- Telegram API-Credentials als Umgebungsvariablen setzen (https://my.telegram.org):
  ```bash
  export TELEGRAM_API_ID=123456
  export TELEGRAM_API_HASH="abcdef..."
  ```
  Unter Windows PowerShell entsprechend `setx TELEGRAM_API_ID 123456` usw.

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
Das Skript `emoji_pipeline.py` bündelt verschiedene Teilaufgaben:
```bash
python3 emoji_pipeline.py by-date --schedule input/links.txt --mode inline --translate 1 --lang de
python3 emoji_pipeline.py collect-letters --links input/links.txt
python3 emoji_pipeline.py extract-plain --links input/links.txt
```
Details zu den Subcommands stehen im Quelltext (`emoji_pipeline.py`). Für alle Befehle mit Telegram-Zugriff gelten die oben genannten API-Variablen.

## Mapping/Lettermap
Der Lettermap-Tab im UI und die zugehörigen Dateien (`data/letter_map.json`, `data/lettermap_ignore.json`) waren ursprünglich für ein Emoji-zu-Buchstaben-Mapping vorgesehen. Aktuell ist dieser Schritt optional; die ODT-Erzeugung funktioniert auch ohne weitere Eingriffe. Das Mapping-Feature bleibt als Vorbereitung für künftige Erweiterungen im Projekt.

## Entwicklung
- Syntax-Check: `python3 -m compileall -q .`
- Debug-Ausgaben und Reports werden unter `data/` erzeugt (z. B. `missing_lettermap_docs.json`).
- Vor Pull-Requests bitte sicherstellen, dass UI und CLI-Läufe mit einer Beispiel-Schedule erfolgreich sind.

## Lizenz
Siehe beiliegende `LICENSE`-Datei.
