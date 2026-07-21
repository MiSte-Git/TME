# Deployment (macOS & Windows)

Dieser Leitfaden beschreibt, wie du aus diesem Projekt Desktop-Bundles erzeugst und wie die Telegram-API-Zugangsdaten hinterlegt werden.

## Telegram API Keys (API ID, API Hash)

- Diese Schlüssel stammen von Telegram. So erhältst du sie:
  - Besuche https://my.telegram.org/ und melde dich mit deiner Telegram-Nummer an.
  - Klicke auf "API development tools".
  - Lege eine neue Anwendung an, anschließend bekommst du:
    - API ID (eine Zahl)
    - API Hash (eine Zeichenkette)
- Diese Keys sind geheim. Bitte NIEMALS ins Git-Repository committen.

### Ablage der Keys (ohne Repo-Leak)

Die App liest die Keys in dieser Reihenfolge:

1) Umgebungsvariablen (wenn du aus einem Terminal startest):

```bash
export TELEGRAM_API_ID=123456
export TELEGRAM_API_HASH=your_api_hash
python3 ui/app.py
```

2) Private XDG-Konfiguration (empfohlen für Doppelklick-Start):

- Erstelle eine Datei `~/.config/telegram-odt/credentials.json` mit folgendem Inhalt:

```json
{ "api_id": 123456, "api_hash": "your_api_hash" }
```

- Alternativ werden auch `credentials.yaml`/`credentials.yml` oder eine `.env`-Datei in diesem Ordner akzeptiert.
- Beim ersten Start fragt die App die Keys ab und legt diese JSON-Datei automatisch an (POSIX-Rechte 0600), falls keine Quelle gefunden wurde.

### Login direkt im UI (empfohlener Weg)

Am einfachsten müssen API ID/API Hash überhaupt nicht vorab manuell hinterlegt
werden: Fehlen sie komplett oder ist die gespeicherte Telegram-Session
ungültig/abgelaufen, öffnet die App beim Start eines Laufs automatisch einen
schrittweisen Login-Dialog:

1. Falls nötig zuerst API ID/API Hash (wird über `save_telegram_credentials()`
   in dieselbe `credentials.json` wie oben geschrieben).
2. Telefonnummer, per Telegram zugesandter Bestätigungscode, ggf. 2FA-Passwort.

Der Dialog lässt sich jederzeit erneut über den Button „Jetzt einloggen…" im
Schedule-Tab öffnen. Für Umgebungen ohne UI (z. B. Server) gibt es weiterhin
den Konsolen-Fallback:

```bash
.venv\Scripts\python.exe scripts\telegram_login.py   # Windows
python3 scripts/telegram_login.py                    # macOS/Linux
```

Beide Wege nutzen dieselbe Login-Logik (`pipeline/telegram_login.py`) und
erzeugen/erneuern dieselbe `tg_session.session`-Datei im Repo-Root.

## Übersetzungs-Provider (DeepL, Google Translate, ChatGPT)

Standardmäßig übersetzt die App über Telegrams eigene Übersetzungsfunktion
(`provider: telegram` in `config.yaml`) - dafür ist kein zusätzlicher API-Key
nötig. Optional lassen sich externe Provider einstellen (`translation.provider`
in `config.yaml` bzw. Dropdown "Übersetzungs-Provider" im UI, `--provider` im
CLI). Jeder externe Provider braucht einen eigenen API-Key, abgelegt nach
demselben Muster wie die Telegram-Keys:

1) Direkt im UI (empfohlen): Menü „Einstellungen → API-Keys verwalten…" öffnet
einen Dialog mit einem Eingabefeld je Provider (Augen-Symbol zum kurzzeitigen
Einblenden des Klartexts). Gespeichert wird bevorzugt verschlüsselt im
**OS-Keyring** - Windows Credential Locker, macOS Keychain, unter Linux
Secret Service (GNOME Keyring/KWallet, benötigt einen laufenden Keyring-
Daemon mit D-Bus-Session; auf headless/minimalen Linux-Installationen ist das
**nicht garantiert** verfügbar). Findet die App kein nutzbares Keyring-Backend,
weicht sie automatisch auf `credentials.json` im Klartext aus und markiert das
deutlich im Dialog sowie im Log (`data/tme.log`). Bereits aktivierte
Übersetzung/Provider-Auswahl im Schedule-Tab prüft zusätzlich sofort beim
Aktivieren bzw. Providerwechsel, ob ein Key vorliegt, statt erst beim
Laufstart mit einem API-Fehler zu scheitern.

2) Umgebungsvariablen (haben stets Vorrang vor Keyring/`credentials.json`):

```bash
export DEEPL_API_KEY=your_deepl_key
export GOOGLE_TRANSLATE_API_KEY=your_google_key
export OPENAI_API_KEY=your_openai_key
```

3) Oder manuell in derselben `~/.config/telegram-odt/credentials.json` wie die
Telegram-Keys, als zusätzliche Felder (identisch zum Klartext-Fallback des
UI-Dialogs):

```json
{
  "api_id": 123456,
  "api_hash": "your_api_hash",
  "deepl_api_key": "your_deepl_key",
  "google_translate_api_key": "your_google_key",
  "openai_api_key": "your_openai_key"
}
```

Es muss nur der Key des tatsächlich genutzten Providers gesetzt sein. Fehlt er,
bricht die Übersetzung für den jeweiligen Lauf kontrolliert ab (Warnung statt
Absturz) - das ODT/DOCX wird trotzdem erzeugt, nur ohne Übersetzung.

Kostenanzeige: Nach jedem Lauf mit externem Provider wird eine grobe, klar als
Schätzung gekennzeichnete Kostenübersicht ausgegeben (CLI-Ausgabe bzw.
Fertig-Dialog im UI). Das ist **keine Live-Preisabfrage** beim Anbieter -
Preistabellen liegen als Konstanten in `pipeline/translation/pricing.py` bzw.
überschreibbar unter `translation.pricing` in `config.yaml`.

Hinweis zu Formatierung: DeepL und Google erhalten Zeilenumbrüche, Fett/Kursiv/
etc. sowie eingebettete Custom-Emojis API-seitig zuverlässig (Tag-Handling).
Bei ChatGPT ist das Best-Effort per Prompt-Anweisung, ohne API-seitige
Garantie - siehe `pipeline/translation/formatting.py` für Details.

## Übersetzungsdateien (*.qm)

- UI-Texte sind lokalisiert. Die kompilierten Qt-Übersetzungen (`ui/translations/app_*.qm`) werden mit ausgeliefert.
- Falls du eigene Strings ergänzt, baue die Übersetzungen neu:

```bash
cd ui/translations
./build_qm.sh
```

## Build mit PyInstaller

Voraussetzungen (einmalig):

```bash
python3 -m pip install pyinstaller PySide6 telethon odfpy pillow easyocr keyring
```

### macOS

```bash
# aus dem Repo-Root
./scripts/build_mac.sh
# Ergebnis: dist/TME.app
```

Optionales Signieren/Notarisieren (mit Developer-ID):

```bash
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: <Dein Name>" \
  dist/TME.app
xcrun notarytool submit dist/TME.app --keychain-profile <profil> --wait
xcrun stapler staple dist/TME.app
```

### Windows

PowerShell (als Benutzer mit Python/py installiert; legt bei Bedarf automatisch
ein `.venv` an):

```powershell
# aus dem Repo-Root
./scripts/build_win.ps1
# Ergebnis (Standard, --onedir - schnellerer lokaler Test-Build,
# PyInstaller-Analyse-Cache bleibt erhalten): dist\TME\TME.exe

./scripts/build_win.ps1 -Release
# Ergebnis (--onefile + --clean - für tatsächliche Weitergabe an Endnutzer):
# dist\TME.exe

./scripts/build_win.ps1 -Clean
# wie Standard, erzwingt aber zusätzlich --clean (z.B. nach Änderungen an
# Abhängigkeiten/Hidden-Imports)
```

Das Skript ergänzt automatisch die für `keyring`s dynamische Backend-Auswahl
nötigen PyInstaller-Hidden-Imports und weist zu Beginn darauf hin, falls
Windows Defender Echtzeitschutz aktiv zu sein scheint (verlangsamt Builds
spürbar - `C:\Projekte\TME` ggf. als Ausschluss eintragen).

Zum reinen Starten der UI aus der venv, ohne Build:

```powershell
./scripts/run_ui.ps1
```

Installation als Start-Menü-Eintrag (erwartet `dist\TME.exe`, also einen
`-Release`-Build):

```powershell
./scripts/windows-install.ps1 [-AllUsers] [-CreateDesktopShortcut]
# Deinstallation: ./scripts/windows-uninstall.ps1
```

Optionaler Installer: mit Inno Setup kann aus `dist\TME\` (bzw. `dist\TME.exe`
bei `-Release`) ein Installer gebaut werden (nicht im Repo enthalten).

### Linux

Für Linux gibt es kein PyInstaller-Bundle, stattdessen eine `.desktop`-Datei für die
Desktop-Integration (Anwendungsmenü). Sie wird **nicht** eingecheckt, sondern bei Bedarf
lokal generiert – die hinterlegten `Exec=`/`Path=`/`Icon=`-Pfade würden sonst auf das System
zeigen, auf dem sie erzeugt wurde.

Generieren/installieren (aus dem Repo-Root):

```bash
python3 scripts/generate_build_files.py
```

Unter Linux wird die `.desktop`-Datei automatisch mitgeneriert und nach
`~/.local/share/applications/tme.desktop` geschrieben (inkl. Aufruf von
`update-desktop-database`, falls installiert). `Exec=`/`TryExec=` zeigen dabei auf
`.venv/bin/python3`, falls ein lokales venv existiert, sonst auf das im `PATH` gefundene
`python3`.

Auf anderen Plattformen ausgeführt (z. B. zum Testen) oder um die Generierung explizit
zu steuern:

```bash
python3 scripts/generate_build_files.py --with-desktop-entry   # erzwingen
python3 scripts/generate_build_files.py --no-desktop-entry     # überspringen
```

Das Skript erzeugt außerdem standardmäßig `<name>.spec` (Standardname `TME.spec`)
für einen möglichen PyInstaller-Build - mit lokalen Absolut-Pfaden in `pathex`/`datas`.
Da Linux (s. o.) kein PyInstaller-Bundle nutzt, wird diese Generierung unter Linux
**standardmäßig übersprungen**; `TME.spec` wird daher auch nicht eingecheckt. Bei
Bedarf (z. B. zum Testen eines Linux-Bundles) explizit erzwingen:

```bash
python3 scripts/generate_build_files.py --with-spec
```

Wieder entfernen:

```bash
python3 scripts/generate_build_files.py --uninstall-desktop
```

## Sprachnachrichten-Transkription (optional)

Sprach-/Audionachrichten werden während eines Schedule-Laufs automatisch heruntergeladen
und per [OpenAI Whisper](https://github.com/openai/whisper) transkribiert
(`pipeline/speech_to_text.py`); das Transkript erscheint direkt unterhalb der
jeweiligen Nachricht im ODT.

Dafür sind zusätzliche, nicht standardmäßig installierte Abhängigkeiten nötig
(`torch` inkl. CUDA-Paketen, `openai-whisper`, zusammen mehrere GB):

```bash
python3 -m pip install -r requirements-stt.txt
```

Ohne diese Installation läuft die App unverändert weiter - `transcribe_voice()`
schlägt dann kontrolliert fehl (`SpeechToTextError`, z. B. weil `whisper` fehlt),
der Lauf wird ohne Absturz fortgesetzt und Sprachnachrichten erscheinen im ODT
einfach ohne Transkript. Unerwartete Transkriptionsfehler (z. B. bei bereits
installiertem `whisper`) werden nach `data/tme.log` geloggt, brechen den Lauf
aber ebenfalls nicht ab.

Per Umgebungsvariable `STT_DEVICE=cuda` lässt sich (bei vorhandener GPU) CUDA statt
CPU für die Transkription erzwingen; ohne Setzen der Variable wird immer CPU genutzt.

## Custom-Emoji-Cache (cache/emoji/)

Gerenderte PNGs für Custom-Emojis (inkl. animierter .tgs/.webm, siehe
`pipeline/frame_compositing.py`) werden dauerhaft unter `cache/emoji/<doc_id>.png`
zwischengespeichert - der Cache-Check prüft nur, ob die Datei existiert, nicht
mit welchem Verfahren/welcher Version sie erzeugt wurde.

Ändert sich künftig das Render-Verfahren (z. B. andere `DEFAULT_FRAME_SAMPLES`,
andere Compositing-Logik), muss dafür `RENDERER_VERSION` in
`pipeline/frame_compositing.py` erhöht werden, damit neu erzeugte Einträge
korrekt versioniert werden. Bereits vorhandene Alt-Einträge werden dadurch
*nicht* automatisch neu gerendert (das würde bei tausenden Cache-Dateien
unnötig teuer). Stattdessen danach einmalig ausführen:

```bash
python3 scripts/rescan_emoji_cache.py                # Dry-Run, nur Report
python3 scripts/rescan_emoji_cache.py --apply         # betroffene Alt-Einträge in cache/emoji/_quarantine/ verschieben
```

Das Skript erkennt Alt-Einträge heuristisch als "wahrscheinlich unvollständig"
(fast leeres Bild - typisches Symptom eines zu früh gerenderten Frames) und
verschiebt nur diese nach `cache/emoji/_quarantine/`, statt den ganzen Cache
zu verwerfen; beim nächsten echten Lauf werden sie automatisch neu von
Telegram geladen und mit dem aktuellen Verfahren gerendert. Einschränkung:
Einträge, die bei Frame 0 bereits ein vollständiges, nur um einzelne Elemente
unvollständiges Bild zeigen, erkennt die Heuristik nicht (siehe Docstring in
`scripts/rescan_emoji_cache.py`).

## Laufzeit-Hinweise

- Credentials liegen außerhalb des Repos unter `~/.config/telegram-odt/` (oder per ENV/OS-Keyring). Die App fragt bei Bedarf direkt im UI danach (Login-Dialog bzw. API-Keys-Dialog, siehe oben).
- OCR: Für Tesseract-OCR ist eine Systeminstallation von Tesseract erforderlich; EasyOCR lädt Modelle bei Bedarf. Standardmäßig ist OCR optional (siehe `config.yaml`).
- Logs/Reports liegen unter `data/` und `out/`, insbesondere `data/tme.log` für den Verlauf einzelner Läufe.
