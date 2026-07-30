# TME – Backlog & Projektstand

*Rekonstruiert aus dem Gesprächsverlauf (kein Original mehr im Projekt vorhanden) + aktueller Session, Stand 2026-07-30. Gegen den tatsächlichen Repo-Stand per Claude Code abgeglichen und korrigiert (2026-07-30).*

---

## Projekt

**TME (Telegram Nachrichten kopieren / Telegram Message Exporter)** — Python/PySide6-Desktop-App, exportiert Telegram-Nachrichten nach ODT/DOCX, optional mit Side-by-side-Übersetzung.
Repo: github.com/MiSte-Git/TME (öffentlich)
Stack: Python, Telethon, PySide6, odfpy, PyInstaller. Ziel: Linux + Windows.

---

## Fertige Kernfeatures (1–4, 6–7)

| # | Feature | Status |
|---|---|---|
| 1 | Side-by-side-Spaltenlayout (Tabellenzeilen pro Nachricht) | ✅ fertig |
| 2 | Chronologisches Interleaving mehrerer Kanäle | ✅ fertig |
| 3 | Emoji-Wort-Segmentierung / Übersetzungsausschluss (Lettermap) | ✅ fertig, siehe offene Punkte unten |
| 4 | Übersetzungs-Provider-Abstraktion (DeepL/Google/ChatGPT) inkl. Kostentracking | ✅ fertig |
| 6 | ODT/DOCX-Formatwahl | ✅ fertig |
| 7 | Inkrementelle Dokument-Updates via Message-Store | ✅ fertig |

---

## Session 2026-07-30 — Windows-Build-Härtung & Bildhöhen-Bug

### Erledigt
- **Versionsstempel Windows** (`scripts/build_win.ps1`, `scripts/windows-install.ps1`, `ui/app.py`): Git-Kurzhash (+`-dirty`) wird gebaut, ins Bundle gelegt, installiert und beim Start nach `tme.log` geloggt (`Build-Version: commit=...`). Zwei Bugs im ersten Entwurf gefunden & gefixt: `--add-data` behält Quelldateinamen (Temp-Datei musste im Verzeichnisnamen die GUID tragen, nicht im Dateinamen), PowerShell-`-Encoding utf8` erzeugt BOM (→ `-Encoding ascii`). **Committet.**
- **config.yaml im Windows-Install** (`scripts/windows-install.ps1`): wird jetzt ins Install-Verzeichnis kopiert, mit drei Fällen: fehlt → kopieren; identisch zum Repo → überspringen; lokal abweichend → nicht überschreiben, Hinweis mit Pfad zur Repo-Version. Real verifiziert inkl. Laufzeit-Beweis (abweichende Config-Werte wie `timeout`/`lettermap_case_mode` greifen tatsächlich). **Committet.**
- **Kritischer Bug: Textverlust bei großen Bildern** — Ursache: `ImageRun.width_cm` fest auf 10cm, Höhe rein proportional ohne Cap; bei Hochformat-Bildern (z.B. 720×1280) ergab das bis zu 17,78cm Höhe, nahe/über nutzbarer Seitenhöhe. Als `as-char`-Element im Tabellen-Absatz führte das dazu, dass der nachfolgende Text beim Seitenumbruch komplett verschwand (verifiziert per LibreOffice-Headless-Rendering + PDF-Textextraktion; Kipppunkt zwischen 17,0cm und 17,78cm bei Landscape).
  **Fix:** `_fit_image_box_cm()` in `odt_writer.py` — echte Bounding-Box-Skalierung (`scale = min(width_ratio, height_ratio)`), Höhen-Cap dynamisch aus Seitenlayout abgeleitet (`_max_image_height_cm(landscape)`): Landscape/Side-by-side → 15,0cm, Portrait (übrige Modi) → 23,7cm. Regressionstest (12/15/16/17/17,78/20cm) für beide Layouts grün, bestehender Test `tests/test_side_by_side_images.py` weiterhin grün. **Committet, per echtem Build/Install/Export-Zyklus verifiziert.**
- **Leere Trailing-Seite (kosmetisch, kein Datenverlust):** Nach dem Bildhöhen-Fix trat bei einem echten Export eine fast leere letzte Seite auf (nur Fußzeile). Root Cause: Dokument liegt hauchdünn über der Kapazität der letzten Seite (Zellrahmen/Padding von `TCell.Base` passt nicht mehr). Drei unabhängige Faktoren (Bildhöhen-Cap, reiner Textumfang, Zellrahmen-Overhead) können das jeweils einzeln auslösen. **Bewusst kein Fix** — keine aufwandsarme, zuverlässige Lösung möglich (Pagination passiert erst beim Rendern, nicht beim XML-Schreiben); als bekannte, tolerierbare Nebenwirkung eingestuft. Nur bei gehäuftem Auftreten in der Praxis erneut aufgreifen.

### Nebenbefunde aus der Windows-Analyse (dokumentiert, kein Fix-Bedarf aktuell)
- Credentials kommen aus `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` (Umgebungsvariablen), nicht aus dem Keyring.
- Toter Import `ensure_pngs_for_twe` in `pipeline/runner_schedule.py:1344` (existiert nie, seit erstem Commit) — durch `except Exception` lautlos geschluckt, praktisch folgenlos (Pre-Pass deckt das ab), aber unnötige Exception pro Nachricht. Noch nicht bereinigt.

---

## Offene Punkte (nächste Schritte)

1. **Custom-Emoji-Cache leer / 🔠-Platzhalter statt Emoji-Bild** — Root Cause gefunden: `ffmpeg` und `lottie_convert.py` fehlen komplett im gebauten Bundle (alle drei Plattformen: `build_win.ps1`, `build_linux.sh`, `build_mac.sh`), obwohl `docs/projekt-struktur.md` das Bundling explizit vorschreibt. Zusätzlich strukturelles Problem: `lottie_convert.py` liegt nur in `.venv\Scripts\`, landet nie auf PATH (auch nicht im Dev-Modus, da `run_ui.ps1` die venv-Python ohne Aktivierung aufruft). Fehler werden über verschachtelte `except Exception: pass`-Blöcke lautlos verschluckt.
   **Prompt bereit**, noch nicht umgesetzt. Vorschlag aus Analyse: `--add-binary`/`--add-data` für ffmpeg + lottie in allen drei Build-Skripten ergänzen, stille Excepts um Log-Zeilen erweitern, toten Import bereinigen.

2. **Lettermap-Mapping (`data/letter_map.json`) auf Windows verschwunden** — Ursache bestätigt (Abgleich 2026-07-30): Datei ist im Dev-Repo-Checkout vollständig befüllt (Stand Dez. 2025), fehlt aber im installierten Windows-Build komplett. `LETTERMAP_FILE_DEFAULT = Path("data/letter_map.json")` (`lettermap.py:10`) ist CWD-relativ, `windows-install.ps1` kopiert die Datei nicht mit — bestätigt durch `data/missing_lettermap_docs.json` im Install-Verzeichnis, dessen fehlende doc_ids sich 1:1 mit dem Repo-Mapping decken (z.B. `5357372145800334358="A"`).
   **Wichtiger Unterschied zu `config.yaml`:** `data/` ist gitignored (reine Nutzerdaten), es gibt kein Repo-Template zum Kopieren. Lösungsraum ist daher **Datenmigration** (Datei einmalig aus dem Dev-Checkout ins Install-Verzeichnis übertragen) **oder Pfad-Umstellung** auf einen installationsunabhängigen, persistenten Speicherort (`QStandardPaths`, analog zur bereits erledigten Theme/Sprache-Lösung, siehe Punkt 4 unten) — letzteres wäre die nachhaltigere Lösung, da sie auch künftige Neuinstallationen/Updates übersteht.
   **Nächster Schritt:** Umsetzungs-Prompt für Pfad-Umstellung (bevorzugt) oder Migrations-Skript (pragmatischer Zwischenschritt) noch zu erstellen.

3. **Feature 5 (Bildübersetzung, OCR → Übersetzen → Re-Rendern)** — Architekturentscheidung getroffen: eigenständiges Tool, Subprocess/CLI-Grenze zu TME. Scope v1: horizontaler, nicht rotierter Text, naives Inpainting, kein Font-Matching, kein Vektor-Text-Pfad. Offene Entscheidung: OCR-Engine (Cloud-OCR default, Tesseract Fallback). Mögliche Wiederverwendung von Layout-/Rendering-Komponenten aus dem Projekt "Translate PDF" (github.com/MiSte-Git) — Prüfung durch Michael nicht abgeschlossen berichtet. Separates, breiteres Tool für PDF/PowerPoint-eingebettete Bilder (inkl. Vektor-Text-Pfad über TME's Provider-Abstraktion) ebenfalls grob gescoped, nicht begonnen.

4. **UI-State-Bugs (Theme/Sprache)** — ✅ **behoben seit Commit `f574e33` (16.07.2026)**, bestätigt beim Abgleich 2026-07-30. `_load_theme_preference`/`_load_language_preference`/`_save_language_preference` nutzen im aktuellen Code (`ui/app.py:1677-1769`) korrekt die Accessor-Funktionen `_theme_state_file()`/`_lang_state_file()` statt der zuvor referenzierten, nirgends definierten Globals `THEME_STATE_FILE`/`LANG_STATE_FILE`.

5. **Lettermap/Emoji-Wort-OCR-Auto-Vorschlag** — frühere Analyse ergab: aktuell kein automatischer OCR-Vorschlag implementiert (siehe Punkt 2). Falls gewünscht, wäre ein Tesseract-basierter Vorschlagsschritt gegen die gecachten Emoji-PNGs denkbar, mit expliziter Einschränkung: unzuverlässig bei verzierten/künstlerischen Schriftstilen, eher Vorschlag als Automatik.

---

## Key Learnings & Prinzipien

- **Analyse vor Änderung:** Standardmuster "nur Analyse, nichts ändern/committen" vor jedem Fix.
- **Nur echte Läufe zählen:** Fixes gelten erst nach Regressionstest + echtem (nicht simuliertem) Lauf als bestätigt.
- **Hypothesen lose halten:** Erste Vermutungen waren mehrfach falsch (Session-Ablauf vs. Rich-Text vs. Bildgröße) — erst echte Logs/Renderings haben Klarheit gebracht.
- **Prompt-Größe:** Kleine, sequenzielle Claude-Code-Prompts (große Prompts haben Claude Code zum Absturz gebracht).
- **Credential-Hygiene:** API-IDs immer aus offizieller Quelle copy-pasten, nicht manuell in Shell-Configs tippen.
- **Git-Vorsicht:** `git filter-repo --force` kann uncommittete Änderungen löschen; Working Tree vor History-Rewrites immer clean prüfen.
- **Commit-Bündelung vermeiden:** `git show <hash> --stat` vor dem Push prüfen, wenn Commits mehrere Sessions überspannen.

---

## Tools & Ressourcen

- **Sprachen/Frameworks:** Python, PySide6, Telethon, odfpy, PyInstaller, cairosvg
- **Übersetzungs-Provider:** DeepL (Free + Pro), Google Translate, ChatGPT; Telegram-nativ. Gemini nur in der Preistabelle vorbereitet (`pricing.py:59`), **kein eigener `gemini_provider.py` implementiert**.
- **Build:** `scripts/generate_build_files.py` (Linux), `scripts/build_win.ps1`, `TME_mac.spec`
- **Versionskontrolle:** Git, GitHub (github.com/MiSte-Git/TME), `git filter-repo` für History-Rewrites
- **IDE:** VS Code mit Claude Code Extension (Fable 5 für Code-Review)
- **Assets:** `flag-icons` (npm, MIT) für SVG-Flaggen, gerastert via cairosvg
- **OS-Keyring:** Windows Credential Locker / macOS Keychain / Linux Secret Service, Fallback `credentials.json`
