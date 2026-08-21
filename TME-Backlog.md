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

## Session 2026-08-21 — Fixes für Punkt 1, 2, 6, 7, 8 (Claude, per Cowork)

Analyse + Umsetzung im Arbeitsverzeichnis, **noch NICHT committet** und **noch NICHT durch einen echten Build/Install/Export-Zyklus verifiziert** (kein Windows-Zugriff in dieser Session) — nächster Schritt vor dem Committen: genau dieser reale Testlauf, insbesondere für Punkt 1 (siehe Risikohinweis dort). Wo möglich, wurden Teile der neuen Logik hier in einer Linux-Sandbox mit echten Tools (ffmpeg, LibreOffice, lottie+cairosvg) end-to-end gegen synthetische Test-Dateien verifiziert (kein simulierter Mock) — siehe je Punkt unten.

1. **Custom-Emoji-Cache leer** — zwei unabhängige Fixes statt einem:
   - WEBM-Pfad: ffmpeg/ffprobe werden jetzt in `build_win.ps1`/`build_linux.sh`/`TME_mac.spec` gebündelt (`--add-binary`, nur wenn auf dem Build-Rechner via PATH gefunden), plus Laufzeit-Fallback-Suche `_find_bundled_tool()` in `frame_compositing.py` (PATH → Bundle-Root). **Real verifiziert:** `render_webm_multiframe()` gegen ein synthetisches WEBM per echtem ffmpeg-Aufruf getestet, PNG korrekt erzeugt.
   - TGS-Pfad: **nicht wie ursprünglich geplant gebündelt**, sondern `render_tgs_multiframe()` komplett umgebaut — ruft die `lottie`-Bibliothek jetzt direkt in-process auf (`lottie.parsers.tgs.parse_tgs` + `lottie.exporters.cairo.export_png`) statt `lottie_convert.py` per Subprocess. Grund: `lottie_convert.py` wurde bisher über `sys.executable` gestartet — in einem PyInstaller-gefrorenen Build zeigt das auf die gefrorene `TME.exe` selbst, nicht auf einen echten Python-Interpreter; das Skript wäre damit selbst nach Bundling gar nicht ausführbar gewesen. `requirements.txt` um `cairosvg` ergänzt (bisher nur indirekt vorausgesetzt, nirgends deklariert — ohne cairosvg ist `lottie.exporters.cairo.export_png` gar nicht definiert). **Real verifiziert:** `render_tgs_multiframe()` gegen eine synthetische, minimal gültige .tgs-Datei getestet (echter `lottie`+`cairosvg`-Aufruf, kein Mock) — 128×128-RGBA-PNG korrekt erzeugt.
     **Restrisiko (noch offen):** cairosvg hängt nativ von libcairo ab; ob PyInstaller das unter Windows zuverlässig bündelt, ist NICHT verifiziert — erster Test nach diesem Fix: Release-Build mit einem echten TGS-Custom-Emoji-Export durchspielen.
   - `extract_ce.py`: stille `except Exception: pass`-Blöcke geben jetzt eine `logger.warning()`-Zeile aus (auch wenn WEBM/TGS-Rendering ohne Exception `False` liefert, z.B. weil ein Tool fehlt) — der tote Import in `runner_schedule.py:1344` (Nebenbefund, kein aktiver Bug) wurde NICHT angefasst, war nicht Teil der beauftragten fünf Punkte.

2. **Lettermap-Mapping auf Windows verschwunden** — Pfad-Umstellung umgesetzt (die bevorzugte Lösung aus der Analyse). Neue Funktion `lettermap_file_path()` in `pipeline/lettermap.py` löst `QStandardPaths.AppConfigLocation` auf (derselbe Ort wie `ui_theme.json`/`ui_lang.json`, siehe Punkt 4) und migriert einmalig eine vorhandene `data/letter_map.json` dorthin (Original bleibt erhalten). Fallback auf das alte CWD-relative Verhalten, wenn PySide6/QApplication nicht verfügbar ist (reine CLI-Nutzung). Alle vier weiteren Stellen, die den alten Pfad hart codiert hatten (`lettermap_tools.py`, `runner_by_ids.py`, `runner_schedule.py` ×2, `emoji_pipeline.py`), wurden auf die zentrale Funktion umgestellt — sonst hätte der Fix nur `lettermap.py` selbst betroffen, das vom eigentlichen Laufzeit-Code gar nicht genutzt wurde. **Real verifiziert:** Migration + `QStandardPaths`-Auflösung mit echtem `QCoreApplication` (Org/App-Name wie in `ui/app.py`) getestet — löst korrekt nach `~/.config/MiSte/TME/letter_map.json` auf, migriert vorhandene Datei, Original bleibt erhalten; Fallback ohne PySide6 ebenfalls verifiziert.

3. **Feature 5 (Bildübersetzung)** — unverändert, nicht Teil dieser Session.

4. **UI-State-Bugs (Theme/Sprache)** — unverändert ✅ erledigt (siehe unten).

5. **Lettermap/Emoji-Wort-OCR-Auto-Vorschlag** — unverändert, nicht Teil dieser Session.

6. **[Bug] ChatGPT-Übersetzung schlägt still fehl** — Root Cause war anders als vermutet: Fehler wurden bereits sauber als `TranslationError` gefangen und geloggt, aber nur transient über `_notify()` in `status_label` angezeigt — von der nächsten Fortschrittsmeldung sofort überschrieben und am Lauf-Ende endgültig durch `"Fertig."` ersetzt. `ScheduleRunResult` hatte für `docx_error` ein Feld, für Übersetzungsfehler aber keins. Fix: neues Feld `translation_errors` (gedeckelt auf 20 Einträge + Hinweis auf `tme.log`), gesammelt bei Provider-Init-Fehlern, pro Nachricht fehlgeschlagenen Übersetzungen und `tr_result.warnings`; `ui/app.py::_on_worker_finished` zeigt sie jetzt dauerhaft im Abschluss-Dialog (inkl. `QMessageBox.warning`-Titel statt `.information`, analog zu `docx_error`).

7. **[Bug] DOCX-Export nicht funktionsfähig** — Root Cause bestätigt: reine fehlende Abhängigkeit, nirgends dokumentiert oder installiert (`docs/DEPLOY.md` erwähnte LibreOffice/Pandoc bisher gar nicht). Fix: neuer Abschnitt in `DEPLOY.md`, plus Preflight-Check `has_docx_converter()` in `pipeline/docx_convert.py`, den `ui/app.py::run_schedule_file()` vor Lauf-Start aufruft (bei DOCX-Auswahl ohne gefundenes Tool: Warn-Dialog mit Ja/Nein statt erst nach dem kompletten Export zu scheitern). **Real verifiziert:** `convert_odt_to_docx()` end-to-end gegen eine echte, per LibreOffice erzeugte ODT-Datei getestet.

8. **[Bug] Shell-Fenster poppen auf** — Root Cause bestätigt (Vermutung war richtig): `docx_convert.py` (soffice/pandoc) und `frame_compositing.py` (ffmpeg/ffprobe) riefen `subprocess.run()` ohne `creationflags=subprocess.CREATE_NO_WINDOW` auf. Neuer gemeinsamer Helper `pipeline/subprocess_utils.py::run_hidden()` (Windows-only Flag, No-Op auf Linux/macOS) wird jetzt an beiden Stellen verwendet. Zeitlich hängt das direkt mit Punkt 7 zusammen (DOCX-Schritt läuft direkt nach der Übersetzung, "während der Übersetzung" war vermutlich diese Wahrnehmung). **Nicht verifizierbar in dieser Session** (Windows-only Verhalten, kein Windows-Zugriff) — `NO_WINDOW_KWARGS` bleibt auf Linux bewusst leer (verifiziert), das Windows-Flag selbst basiert auf der `subprocess`-Dokumentation, nicht auf einem echten Lauf.

**Nicht committet.** Michael: bitte Diff sichten, dann in einer echten Windows-Umgebung Build → Install → Export durchspielen (insbesondere Punkt 1, cairosvg/libcairo-Bundling) — erst danach committen.

---

## Offene Punkte (nächste Schritte)

1. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

2. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

3. **Feature 5 (Bildübersetzung, OCR → Übersetzen → Re-Rendern)** — Architekturentscheidung getroffen: eigenständiges Tool, Subprocess/CLI-Grenze zu TME. Scope v1: horizontaler, nicht rotierter Text, naives Inpainting, kein Font-Matching, kein Vektor-Text-Pfad. Offene Entscheidung: OCR-Engine (Cloud-OCR default, Tesseract Fallback). Mögliche Wiederverwendung von Layout-/Rendering-Komponenten aus dem Projekt "Translate PDF" (github.com/MiSte-Git) — Prüfung durch Michael nicht abgeschlossen berichtet. Separates, breiteres Tool für PDF/PowerPoint-eingebettete Bilder (inkl. Vektor-Text-Pfad über TME's Provider-Abstraktion) ebenfalls grob gescoped, nicht begonnen.

4. **UI-State-Bugs (Theme/Sprache)** — ✅ **behoben seit Commit `f574e33` (16.07.2026)**, bestätigt beim Abgleich 2026-07-30. `_load_theme_preference`/`_load_language_preference`/`_save_language_preference` nutzen im aktuellen Code (`ui/app.py:1677-1769`) korrekt die Accessor-Funktionen `_theme_state_file()`/`_lang_state_file()` statt der zuvor referenzierten, nirgends definierten Globals `THEME_STATE_FILE`/`LANG_STATE_FILE`.

5. **Lettermap/Emoji-Wort-OCR-Auto-Vorschlag** — frühere Analyse ergab: aktuell kein automatischer OCR-Vorschlag implementiert (siehe Punkt 2). Falls gewünscht, wäre ein Tesseract-basierter Vorschlagsschritt gegen die gecachten Emoji-PNGs denkbar, mit expliziter Einschränkung: unzuverlässig bei verzierten/künstlerischen Schriftstilen, eher Vorschlag als Automatik.

6. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

7. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

8. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

9. **[UI/UX] "Kanal (optional)"-Feld irreführend beschriftet** — beim Sammeln eines ganzen Kanals ist die Eingabe eines Kanal-Links faktisch erforderlich, das Feld heißt aber "optional". Label/Hilfetext sollte klarstellen, wann das Feld zwingend ist.

10. **[UI/UX] Fehlende Eingabe-Plausibilitätsprüfung beim Start** — keine Validierung, ob die eingegebenen Parameter für den gewählten Modus sinnvoll/vollständig sind. Sollte vor Start geprüft und mit klarer Fehlermeldung abgefangen werden.

11. **[UI/UX] Datumsfelder nicht kontextabhängig deaktiviert** — sollten ausgegraut sein, wenn "Nach Datum holen" nicht angewählt ist.

12. **[UI/UX] Textfelder benötigen Doppelklick vor Eingabe** — sollte mit einfachem Klick funktionieren (Fokus-/Klick-Handling prüfen).

13. **[UI/UX] "Übersetzen"-Bereich im Schedule-Editor entfernen** — vermutlich redundant/veraltet gegenüber aktuellem Übersetzungs-Workflow. Genauer Umfang noch zu klären.

14. **[UI/UX] Start-Button-Beschriftung im Telegram-Export-Tab** — aktuell "Telegram-Export → ODT erzeugen", soll zu "Starten" vereinfacht werden, da seit Feature 6 (Format-Wahl) nicht mehr zwingend ODT erzeugt wird, auch DOCX ist möglich.

*Rückmeldungen vom 2026-07-30, noch nicht analysiert/reproduziert — nächste Session: priorisieren und einzeln in Analyse-Prompts überführen.*

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
