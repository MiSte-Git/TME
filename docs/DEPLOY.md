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

## Übersetzungs-Provider (DeepL, Google Translate, ChatGPT)

Standardmäßig übersetzt die App über Telegrams eigene Übersetzungsfunktion
(`provider: telegram` in `config.yaml`) - dafür ist kein zusätzlicher API-Key
nötig. Optional lassen sich externe Provider einstellen (`translation.provider`
in `config.yaml` bzw. Dropdown "Übersetzungs-Provider" im UI, `--provider` im
CLI). Jeder externe Provider braucht einen eigenen API-Key, abgelegt nach
demselben Muster wie die Telegram-Keys:

1) Umgebungsvariablen:

```bash
export DEEPL_API_KEY=your_deepl_key
export GOOGLE_TRANSLATE_API_KEY=your_google_key
export OPENAI_API_KEY=your_openai_key
```

2) Oder in derselben `~/.config/telegram-odt/credentials.json` wie die
Telegram-Keys, als zusätzliche Felder:

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
python3 -m pip install pyinstaller PySide6 telethon odfpy pillow easyocr
```

### macOS

```bash
# aus dem Repo-Root
./scripts/build_mac.sh
# Ergebnis: dist/Telegram-ODT.app
```

Optionales Signieren/Notarisieren (mit Developer-ID):

```bash
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: <Dein Name>" \
  dist/Telegram-ODT.app
xcrun notarytool submit dist/Telegram-ODT.app --keychain-profile <profil> --wait
xcrun stapler staple dist/Telegram-ODT.app
```

### Windows

PowerShell (als Benutzer mit Python/py installiert):

```powershell
# aus dem Repo-Root
./scripts/build_win.ps1
# Ergebnis: dist\Telegram-ODT\Telegram-ODT.exe
```

Optionaler Installer: mit Inno Setup kann aus `dist\Telegram-ODT\` ein Installer gebaut werden (nicht im Repo enthalten).

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
`~/.local/share/applications/telegram-odt.desktop` geschrieben (inkl. Aufruf von
`update-desktop-database`, falls installiert). `Exec=`/`TryExec=` zeigen dabei auf
`.venv/bin/python3`, falls ein lokales venv existiert, sonst auf das im `PATH` gefundene
`python3`.

Auf anderen Plattformen ausgeführt (z. B. zum Testen) oder um die Generierung explizit
zu steuern:

```bash
python3 scripts/generate_build_files.py --with-desktop-entry   # erzwingen
python3 scripts/generate_build_files.py --no-desktop-entry     # überspringen
```

Wieder entfernen:

```bash
python3 scripts/generate_build_files.py --uninstall-desktop
```

## Laufzeit-Hinweise

- Credentials liegen außerhalb des Repos unter `~/.config/telegram-odt/` (oder per ENV). Die App fragt bei Bedarf einmalig nach.
- OCR: Für Tesseract-OCR ist eine Systeminstallation von Tesseract erforderlich; EasyOCR lädt Modelle bei Bedarf. Standardmäßig ist OCR optional (siehe `config.yaml`).
- Logs/Reports liegen unter `data/` und `out/`.
