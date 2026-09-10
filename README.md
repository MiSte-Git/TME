# TME – Telegram-to-ODT Message Exporter

![Telegram → LibreOffice](Telegram-LibreOffice.png)

Werkzeugkasten zum Sammeln von Telegram-Nachrichten und dem Erzeugen von ODT-Dokumenten – inklusive Übersetzungen, Bild- und Emoji-Einbettung. Der Fokus liegt auf dem komfortablen UI-Workflow (`python3 ui/app.py`), der alle Schritte von der Schedule-Datei bis zum fertigen ODT orchestriert.

Es gibt zwei Wege, TME zu installieren – such dir den passenden aus:

- **[Für alle anderen](#für-alle-anderen-geführte-installation)** – ein
  geführter Installations-Assistent mit eigener Oberfläche, keine
  Kommandozeile nötig.
- **[Für Entwickler:innen](#schnellstart-ui)** – Quellcode klonen,
  Abhängigkeiten selbst installieren, direkt aus dem Repository heraus
  starten (bisheriger Weg, unverändert).

Beide Wege installieren am Ende dasselbe Programm; der geführte Assistent
macht im Hintergrund nichts anderes, als was im Entwickler-Weg von Hand
gemacht wird (siehe [Architektur](#architektur-der-geführten-installation)
unten). Daneben existiert weiterhin der bisherige PyInstaller-Vollbuild
(`TME.spec`/`scripts/build_win.ps1`/`TME_mac.spec`/`build_linux.sh`) als
dritter, unveränderter Weg für alle, die ihn bereits nutzen.

## Für alle anderen (geführte Installation)

1. Lade die passende Datei für dein Betriebssystem von der
   [Releases-Seite](https://github.com/MiSte-Git/TME/releases) herunter:
   - Windows: `tme-setup-windows.exe`
   - macOS: `tme-setup-macos`
   - Linux: `tme-setup-linux`
2. Datei ausführen. Es öffnet sich ein Fenster, keine Kommandozeile nötig.
3. Der Assistent führt dich durch:
   - **Sprache** – automatisch nach Systemsprache vorausgewählt (Fallback
     Englisch, wenn die Systemsprache weder Deutsch noch Englisch ist),
     jederzeit umschaltbar.
   - **Sprachnachrichten-Transkription** – optional, zusätzlich ca. 4-5 GB
     Download (OpenAI Whisper); läuft auf der CPU, bei vorhandener
     kompatibler NVIDIA-Grafikkarte automatisch beschleunigt. Lässt sich
     auch später noch nachrüsten (`pip install -r requirements-stt.txt`).
   - **Installation** – läuft automatisch im Hintergrund in eine eigene,
     versteckte Umgebung im Benutzerprofil. Dafür sind **keine
     Administrator-/root-Rechte** nötig, es wird nichts systemweit
     installiert.
   - **Telegram-Zugangsdaten** (optional) – API-ID/API-Hash von
     [my.telegram.org](https://my.telegram.org), jederzeit überspringbar
     und beim ersten Lauf sonst automatisch über den bestehenden
     In-App-Login-Dialog nachgefragt.
   - **API-Schlüssel für Übersetzungs-Provider** (optional) – DeepL/Google/
     ChatGPT, jederzeit überspringbar und später über „Einstellungen →
     API-Keys verwalten…" in der App nachholbar.
   - **Fertig** – ein Eintrag im Anwendungsmenü/Startmenü ist angelegt, die
     App kann direkt gestartet werden.
4. Eine Anmerkung zum ersten Start: die Builds sind aktuell **nicht
   signiert**. Windows SmartScreen bzw. macOS Gatekeeper zeigen deshalb beim
   allerersten Start eine Warnung – „Weitere Informationen"/„Trotzdem
   öffnen" (Windows) bzw. Rechtsklick → „Öffnen" (macOS) bestätigt den Start
   einmalig.
5. ffmpeg (für Custom-Emoji-Video-Compositing) sowie LibreOffice oder Pandoc
   (für den DOCX-Export) sind – wie beim Entwickler-Weg auch – separat zu
   installieren; Details siehe [docs/DEPLOY.md](docs/DEPLOY.md). Der
   Assistent bringt sie nicht mit, da es sich um Systemwerkzeuge statt
   Python-Pakete handelt.

Ein Eintrag im Anwendungsmenü ist das Ergebnis, kein `.deb`/`.rpm`/`.msi`
nötig – Details dazu unten unter
[Architektur](#architektur-der-geführten-installation).

## Features
- Schedule-Dateien (JSON, siehe `input/example.json`) einlesen, Nachrichten abrufen und als ODT exportieren
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
- Medien und Custom-Emojis als Bilder einbetten - bei animierten Custom-Emojis
  (.tgs/.webm) werden mehrere Frames über die Animationsdauer per Alpha-
  Compositing zusammengeführt, damit erst später einblendende Inhalte (z. B.
  bei „geschriebenen" Buchstaben-Sets) nicht fehlen (Details, Grenzen und
  Stellschrauben siehe [docs/DEPLOY.md](docs/DEPLOY.md))
- Optional: automatische Transkription von Sprachnachrichten (OpenAI Whisper) -
  das erkannte Transkript wird direkt unter der jeweiligen Nachricht ins ODT
  eingefügt. Benötigt die zusätzlichen Abhängigkeiten aus `requirements-stt.txt`
  (siehe unten); ohne diese Installation erscheinen Sprachnachrichten einfach
  ohne Transkript im ODT, kein Fehler
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
- Laufender Schedule-Export lässt sich im UI jederzeit sauber abbrechen
  („Abbrechen"-Button neben „Schedule → ODT erzeugen")
- Telegram-Login direkt im UI: ist die Session ungültig/abgelaufen oder
  fehlen die API-Zugangsdaten komplett, öffnet sich automatisch ein
  schrittweiser Login-Dialog (ggf. zuerst API ID/API Hash, dann
  Telefonnummer/Code/2FA) - kein manueller Konsolen-Umweg mehr nötig
  (Konsolen-Fallback bleibt als `scripts/telegram_login.py` erhalten)
- API-Keys für externe Übersetzungs-Provider (DeepL/Google/OpenAI) lassen
  sich über „Einstellungen → API-Keys verwalten…" direkt im UI hinterlegen;
  Speicherung bevorzugt verschlüsselt im OS-Keyring (Windows Credential
  Locker/macOS Keychain/Secret Service unter Linux), mit Klartext-
  Fallback auf `credentials.json` inkl. deutlicher Kennzeichnung, falls kein
  Keyring-Backend verfügbar ist
- Zentrales Logging nach `data/tme.log` (zusätzlich Konsolenausgabe) für
  effektive Lauf-Optionen, Nachrichtenzählung pro Section und Fehlerursachen

## Voraussetzungen
- Python 3.11+
- Abhängigkeiten installieren:
  ```bash
  python3 -m pip install -r requirements.txt
  ```
- Optional für automatische Sprachnachrichten-Transkription (siehe Features oben,
  Details in [docs/DEPLOY.md](docs/DEPLOY.md)):
  ```bash
  python3 -m pip install -r requirements-stt.txt
  ```
- Telegram API-Credentials (https://my.telegram.org) hinterlegen - drei gleichwertige Wege:
  - Direkt im UI: fehlen die Zugangsdaten oder ist die Session abgelaufen, öffnet
    sich beim Start eines Laufs automatisch ein Login-Dialog, der sie abfragt
    und speichert (kein manueller Schritt vorab nötig).
  - Als Umgebungsvariablen:
    ```bash
    export TELEGRAM_API_ID=123456
    export TELEGRAM_API_HASH="abcdef..."
    ```
    Unter Windows PowerShell entsprechend `setx TELEGRAM_API_ID 123456` usw.
  - Alternativ: private `credentials.json` unter `~/.config/telegram-odt/` (Details in
    [docs/DEPLOY.md](docs/DEPLOY.md)).

## Projektstruktur
- `ui/app.py` – Qt-basierte Oberfläche (Schedule-Tab, Schedule-Editor-Tab,
  Lettermap-Tab, Tab „Nicht übersetzen"); `ui/login_dialog.py` und
  `ui/api_keys_dialog.py` – Login- bzw. API-Keys-Dialoge
- `pipeline/` – Kernlogik für Telegram-Abfragen, Emoji-Assets, ODT Writer,
  zentrales Logging (`pipeline/logging_setup.py` → `data/tme.log`)
- `credentials.py` – zentrale Zugangsdaten-Verwaltung (Telegram + Provider-
  API-Keys, ENV/OS-Keyring/`credentials.json`)
- `input/` – Beispiel-Schedules (JSON)
- `output/` – erzeugte ODTs
- `data/` – Laufzeitdaten (letter_map.json, reports, UI-Status, `tme.log`)
- `media/` & `cache/` – gespeicherte Medien bzw. Emoji-PNGs
- `scripts/` – Hilfsskripte (siehe [docs/DEPLOY.md](docs/DEPLOY.md)): Build
  (`build_win.ps1`, `build_mac.sh`), UI-Start unter Windows (`run_ui.ps1`),
  Konsolen-Login-Fallback (`telegram_login.py`)
- Linux-Skripte im Repo-Root (siehe [docs/DEPLOY.md](docs/DEPLOY.md)):
  Entwicklung (`run_linux_dev.sh`), Build (`build_linux.sh`), Installation
  (`install_linux.sh`)

## Schnellstart (UI)
1. Abhängigkeiten installieren und API-Credentials setzen.
2. UI starten:
   ```bash
   python3 ui/app.py
   ```
3. Im Schedule-Tab die gewünschte Datei wählen (`input/…`) und Optionen setzen.
4. „Schedule → ODT erzeugen“ starten; Fortschritt, ggf. fehlende Mappings und Ergebnisdialog erscheinen direkt in der Oberfläche.
5. UI merkt sich die letzten Einstellungen in `data/ui_state.json`. Existiert die zuletzt gewählte Datei nicht mehr, bleibt das Feld leer.

Das erzeugte ODT enthält ein Inhaltsverzeichnis mit klickbaren Einträgen (Sprung zur jeweiligen Überschrift) – bereits beim Öffnen befüllt, kein manuelles „Index aktualisieren“ nötig. Seitenzahlen fehlen dabei bewusst: Die tatsächliche Seite lässt sich beim Schreiben ohne echten Layout-Renderer nicht zuverlässig ermitteln. Wer sie dennoch braucht, kann in LibreOffice/Word einmalig per Rechtsklick ins Verzeichnis → „Index aktualisieren“ (bzw. Cursor hineinklicken und F9) die Ansicht mit Seitenzahlen neu erzeugen lassen.

## CLI-Workflows
Das Skript `pipeline/emoji_pipeline.py` bündelt verschiedene Teilaufgaben:
```bash
python3 pipeline/emoji_pipeline.py by-date --schedule input/example.json --mode inline --translate 1 --lang de
python3 pipeline/emoji_pipeline.py collect-letters --links input/links.txt
python3 pipeline/emoji_pipeline.py extract-plain --links input/links.txt
```
`by-date` erwartet eine JSON-Schedule-Datei (siehe `input/example.json`);
`collect-letters`/`extract-plain` erwarten dagegen eine reine Links-Liste
(eine Telegram-Nachrichten-URL pro Zeile, siehe `input/links.txt`) - beide
Formate sind bewusst unterschiedlich und nicht austauschbar.
Details zu den Subcommands stehen im Quelltext (`pipeline/emoji_pipeline.py`). Für alle Befehle mit Telegram-Zugriff gelten die oben genannten API-Variablen.

## Mapping/Lettermap
Der Lettermap-Tab im UI und die zugehörigen Dateien (`data/letter_map.json`, `data/lettermap_ignore.json`) waren ursprünglich für ein Emoji-zu-Buchstaben-Mapping vorgesehen. Aktuell ist dieser Schritt optional; die ODT-Erzeugung funktioniert auch ohne weitere Eingriffe. Das Mapping-Feature bleibt als Vorbereitung für künftige Erweiterungen im Projekt.

## Installation & Build (Desktop-Bundles)
Für fertige Desktop-Bundles (macOS `.app`, Windows `.exe`, Linux-Binary) sowie
Details zur Ablage der Telegram-API-Keys siehe [docs/DEPLOY.md](docs/DEPLOY.md).

Unter Linux stehen drei Skripte im Repo-Root zur Verfügung:

```bash
./run_linux_dev.sh      # Entwicklung: startet ui/app.py aus dem aktuellen Arbeitsstand (venv wird bei Bedarf angelegt)
./build_linux.sh        # Produktiv-Build: eigenständiges Binary via PyInstaller nach dist/
./install_linux.sh      # Installation: kopiert nach ~/.local/share/tme/, Desktop-Eintrag zeigt danach direkt auf das Binary
```

Details zu Optionen (`--release`, `--stt`/`--with-stt` für die optionale
Sprachnachrichten-Transkription, Zielpfade) siehe [docs/DEPLOY.md](docs/DEPLOY.md).

## Hintergrund & Architektur
Für Contributor:innen, die tiefer in Aufbau und Entstehung der Pipeline einsteigen wollen:
- [docs/projekt-struktur.md](docs/projekt-struktur.md) – Architekturüberblick (PySide6, Module, UI-Screens)
- [docs/emoji-odt-kontext.md](docs/emoji-odt-kontext.md) – ursprüngliches Konzept inkl. JSON-Schemas

## Geplante Features (Roadmap)
- **OCR (Tesseract/EasyOCR):** In früheren Planungen vorgesehen, aktuell **nicht implementiert**
  – im Code gibt es keine `pytesseract`/`easyocr`-Imports. Nicht in `requirements.txt`
  oder im Install-Befehl oben enthalten; wird ergänzt, sobald die Funktion umgesetzt ist.

## Release-Prozess (geführte Installation)

Ein Release des Bootstrappers entsteht aus einem Git-Tag; die
Versionsnummer lebt an genau einer Stelle, in `_version.py` (Semver
`MAJOR.MINOR.PATCH`). Betrifft ausschließlich den neuen, geführten
Installations-Weg oben – der bisherige PyInstaller-Vollbuild
(Versionsstempel per Git-Kurz-Hash, `BUILD_VERSION.txt`) bleibt davon
unberührt. Ablauf:

1. Alle Feature-Commits sind auf `main`.
2. `__version__` in `_version.py` erhöhen und **als eigenen Commit**
   einchecken:

   ```bash
   git add _version.py
   git commit -m "Version X.Y.Z"
   ```

3. Tag setzen und pushen – das führende `v` gehört zum Tag, nicht zur
   Versionsnummer:

   ```bash
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

4. Der Tag-Push startet
   [`build-bootstrap.yml`](.github/workflows/build-bootstrap.yml). Die
   Pipeline bricht ab, **bevor** irgendetwas veröffentlicht wird, falls Tag
   und `__version__` nicht übereinstimmen. Sonst baut sie die drei
   Assistenten-Executables (Linux/Windows/macOS) plus das Quellcode-ZIP und
   legt daraus das GitHub-Release an.
5. Kurz in den GitHub Actions prüfen, dass alle drei Matrix-Jobs
   durchgelaufen sind.

Was sich **nicht** ändern darf, ohne beide Stellen anzupassen:
`_version.py` und der Tag müssen zusammenpassen (siehe Punkt 4), und pro
Release darf genau ein `.zip`-Asset existieren – `bootstrap/release_source.py`
nimmt das erste `.zip`, das es findet.

Noch offen (siehe TME-Backlog.md): eine In-App-Update-Prüfung, die
`bootstrap/release_source.py`s GitHub-API-Code wiederverwendet, ist – anders
als beim Vorbild-Projekt PDF-Translator – noch **nicht** umgesetzt; bereits
installierte Versionen erfahren von einem neuen Release also noch nicht von
selbst.

## Architektur der geführten Installation

Der Installations-Assistent (`bootstrap/`) ist ein eigenständiges, sehr
kleines `tkinter`-Programm – Teil jeder Standard-Python-Installation, kein
PySide6/Qt nötig für dieses eine Fenster. Er läuft in zwei Stufen:

1. **Stufe 1 – der Assistent selbst:** führt durch Sprache und die
   Sprachnachrichten-Transkription-Option, lädt dann den eigentlichen
   Programmcode als Release-ZIP von GitHub Releases herunter, legt eine
   eigene virtuelle Python-Umgebung (venv) im Benutzerprofil an und
   installiert die Abhängigkeiten dort mit `pip install -r requirements.txt`
   (plus `requirements-stt.txt` bei gewählter Transkription-Option) –
   **derselbe Befehl**, den auch der Entwickler-Weg oben von Hand ausführt.
2. **Stufe 2 – die eigentliche App:** der heruntergeladene Code plus die
   eben installierten Abhängigkeiten, im eigenen venv. Der
   Anwendungsmenü-Eintrag, den der Assistent am Ende anlegt, zeigt direkt
   auf dieses venv – es gibt also keinen separaten „Build" der eigentlichen
   App, nur den ganz normalen `python -m ui.app`-Start, bloß automatisiert.

Weil dabei nichts außerhalb des Benutzerprofils landet, sind für die
Installation zu keinem Zeitpunkt Administrator-/root-Rechte nötig. Für den
Eintrag im Anwendungsmenü legt der Assistent je nach Betriebssystem selbst
das passende, ebenfalls rechtefreie Äquivalent an: eine `.desktop`-Datei
unter Linux, eine `.lnk`-Verknüpfung im Startmenü unter Windows, ein
`.app`-Bundle unter macOS.

Der Assistenten-Build für alle drei Plattformen läuft automatisiert über
[`.github/workflows/build-bootstrap.yml`](.github/workflows/build-bootstrap.yml)
(PyInstaller, `windows-latest`/`macos-latest`/`ubuntu-latest`) und wird bei
jedem Versions-Tag zusammen mit einem Quellcode-ZIP als GitHub-Release
veröffentlicht. Diese Architektur folgt eng dem bereits produktiven Vorbild
im Schwesterprojekt PDF-Translator (siehe dessen README.md), mit zwei
TME-spezifischen Anpassungen: kein Online/Lokal-Modus (TMEs
Übersetzungs-Provider sind ohnehin alle Cloud-basiert), stattdessen ein
Standard/Transkription-Modus, sowie ein eigener Zugangsdaten-Schritt für
Telegram API-ID/API-Hash statt eines Modus mit GPU-Prüfung.

**Hinweis zum aktuellen Stand:** dieser Weg wurde von Claude (Cowork) am
04.09.2026 nach dem Vorbild von PDF-Translator umgesetzt und ist bislang nur
syntaktisch geprüft (`py_compile`, YAML-Parsing) – noch **nicht** durch
einen echten Build/Install/Start-Zyklus auf einem der drei Betriebssysteme
verifiziert. Siehe TME-Backlog.md für den vollständigen Stand und offene
Punkte.

## Topic-Nachrichten kopieren

Das Kopieren und ein späteres Rückgängigmachen sind auf zwei Tabs verteilt:

Quell- und Zielauswahl merken sich jeweils die letzten zehn erfolgreich
geprüften Topics und zeigen neben dem Link auch den von Telegram gelesenen
Topic-Namen an.

1. Quelle laden, Bot-Gruppen auswählen und Nachrichten chronologisch in das
   Ziel weiterleiten. `/`-Anfragen werden über `@BotName` oder eine direkte
   Bot-Antwort zugeordnet. Beim Abwählen eines Bots entfallen seine Antworten
   und die eindeutig zugeordneten Anfragen. Eine zweite Liste zeigt jede zum
   Kopieren vorgesehene Nachricht; dort kann die Bot-Auswahl noch einzeln
   angepasst werden. Nicht eindeutig zugeordnete `/`-Anfragen erscheinen
   zusätzlich in einer eigenen, mit der Gesamtliste synchronisierten Prüfliste.
   Sie sind zunächst ausgewählt, weil sie von Menschen stammen, können dort
   aber einzeln oder gesammelt abgewählt werden.
2. Der Tab **Rückgängig / Entfernen** gleicht jede protokollierte Ziel-ID
   erneut mit Telegram ab. Sichere Kopien, manuell zu prüfende Fälle,
   fehlende Nachrichten, falsche Topics, nicht kopierbare Medien und bereits
   entfernte Kopien werden getrennt angezeigt. Bot-Anfragen und -Antworten
   können gemeinsam ausgewählt werden. Gelöscht werden nur ausdrücklich neu
   markierte Ziel-IDs in protokollierten 100er-Blöcken; jeder bestätigte Block
   wird gespeichert und kann nach einem Abbruch sicher fortgesetzt werden.

Ein Topic selbst wird niemals gelöscht. Die App verwendet ausschließlich
explizite Nachrichten-IDs und nie Telegrams Funktion zum Löschen einer gesamten
Topic-Historie. Die Kopiernachweise liegen unter `data/topic_copy_runs/`.

## Entwicklung
- Syntax-Check: `python3 -m compileall -q . -x "[\\/](\.venv|build|dist)[\\/]"`
  (Ausschluss nötig, sonst scannt compileall bei lokalem `.venv` im Repo-Root
  auch Fremdpakete mit - z. B. ein PySide6-Jinja2-Template, das als
  ungültiges Python fehlschlägt, siehe auch `scripts/build_win.ps1`)
- Debug-Ausgaben und Reports werden unter `data/` erzeugt (z. B. `missing_lettermap_docs.json`).
- Lauf-Log: `data/tme.log` (Zeitstempel, effektive Optionen pro Lauf, Nachrichtenzählung, Fehler) - zusätzlich auf der Konsole ausgegeben.
- Vor Pull-Requests bitte sicherstellen, dass UI und CLI-Läufe mit einer Beispiel-Schedule erfolgreich sind.

## Lizenz
Copyright (C) 2026 MiSte-Git

Dieses Projekt steht unter der GNU General Public License v3.0 (SPDX: `GPL-3.0-or-later`).
Siehe beiliegende `LICENSE`-Datei für den vollständigen Lizenztext.

Eingebundene Drittanbieter-Assets (Flaggen-Icons der Sprachauswahl) siehe `THIRD_PARTY_LICENSES.md`.
