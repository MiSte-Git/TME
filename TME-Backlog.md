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

## Session 2026-08-21 (Teil 2) — UI/UX-Fixes Punkt 9–14 (Claude, per Cowork)

Analyse + Umsetzung im Arbeitsverzeichnis. Anders als bei Punkt 1–8 sind das reine Qt-Widget-/Validierungslogik-Änderungen ohne Windows-spezifisches Verhalten — deshalb hier **real gegen echte `PySide6`-Widgets verifiziert** (Offscreen-`QApplication`, `QT_QPA_PLATFORM=offscreen`, kein Mock): `ScheduleEditorTab` instanziiert, Zeilen hinzugefügt, Checkbox umgeschaltet, `_collect_schedule()` mit den unten beschriebenen Fehlerfällen aufgerufen — alle Assertions grün. **Noch NICHT committet** (kein Shell/Git-Zugriff in dieser Session, siehe unten).

9. **[UI/UX] "Kanal (optional)"-Feld irreführend beschriftet** — Root Cause bestätigt: `schedule_json.schedule_to_blocks()` wirft bereits zur Laufzeit einen Fehler, wenn `fetch_by_date=True` und weder Zeilen-Kanal noch `default_channel` gesetzt sind ("benötigt einen default_channel, da keine Links angegeben sind") — das Feld ist also bedingt zwingend, nicht generell optional. Fix in `ui/schedule_editor_tab.py`: Spaltenüberschrift von "Kanal (optional)" zu "Kanal (siehe Hinweis)", Tooltip der Spalte präzisiert ("Erforderlich, wenn 'Nach Datum holen' aktiv ist und kein Default-Channel gesetzt ist - sonst wird der Lauf fehlschlagen"), Hinweistext unter der Tabelle umformuliert und erklärt jetzt explizit beide Wege (Zeilen-Kanal vs. Default-Channel).

10. **[UI/UX] Fehlende Eingabe-Plausibilitätsprüfung beim Start** — Der "Telegram-Export"-Tab (`ui/app.py::run_schedule_file()`) hatte bereits umfangreiche Preflight-Checks (leerer Pfad, Datei existiert, Credentials, API-Key, DOCX-Konverter aus Punkt 7). Die eigentliche Lücke lag im Schedule-Editor: `_collect_schedule()` prüfte nur Datum/Titel, nicht aber die Kanal/Links-Kombination aus Punkt 9 — der Fehler aus `schedule_to_blocks()` (siehe oben) schlug bisher erst mitten im echten Lauf zu, nach Telegram-Login und Zeichen-Vorschau. Fix: `_collect_schedule()` prüft jetzt pro Zeile zusätzlich (a) `fetch_by_date=True` ohne Zeilen-Kanal und ohne Default-Channel → Fehler, (b) `fetch_by_date=False` ohne Links → Fehler ("würde keine Nachrichten enthalten"), jeweils mit Zeilennummer in der Meldung. Beide Fälle greifen sowohl beim "Speichern" als auch beim "Speichern & Starten" (ruft intern `_save_doc()` auf).

11. **[UI/UX] Datumsfelder nicht kontextabhängig deaktiviert** — Präzisierung nach Code-Analyse: Die Spalte "Datum" selbst wird immer benötigt (dient als Überschrift, unabhängig vom Lade-Modus). Gemeint sind die Zeitfenster-Spalten "Von"/"Bis" (Zeitpunkt innerhalb des Tages) — die werden nur ausgewertet, wenn "Nach Datum holen" für die Zeile aktiv ist; bei expliziten Links spielen sie keine Rolle. Fix: neue Methode `_update_time_fields_enabled(row)`, verdrahtet über `itemChanged`-Signal auf die Checkbox-Spalte sowie explizit beim Anlegen/Laden von Zeilen — schaltet "Von"/"Bis" der jeweiligen Zeile per Item-Flags (`ItemIsEnabled`/`ItemIsEditable`) aus, sobald "Nach Datum holen" abgewählt wird, und wieder ein beim erneuten Anwählen (Werte bleiben dabei erhalten, werden nicht geleert).

12. **[UI/UX] Textfelder benötigen Doppelklick vor Eingabe** — Root Cause: `QTableWidget` verlangt per Qt-Default einen Doppelklick (oder F2/Enter), um eine Zelle in den Editier-Modus zu versetzen — reines Anklicken markiert die Zelle nur. Fix: `EditTriggers` auf `CurrentChanged | EditKeyPressed | AnyKeyPressed` gesetzt — ein einfacher Klick macht die Zelle zur aktuellen Zelle und öffnet damit sofort den Editor; Tippen sowie F2/Enter funktionieren weiterhin. Nebenbefund dabei: die Checkbox-Zelle (Spalte "Nach Datum holen") hatte bisher zusätzlich zum Checkbox-Flag auch `ItemIsEditable` gesetzt (Altlast) — mit `CurrentChanged` hätte das versucht, beim Anklicken zusätzlich einen (leeren) Text-Editor über der Checkbox zu öffnen. Das `ItemIsEditable`-Flag wird jetzt für diese Zelle gar nicht mehr gesetzt (nur noch `ItemIsUserCheckable` + `ItemIsEnabled` + `ItemIsSelectable`), das Umschalten der Checkbox selbst ist davon unberührt.

13. **[UI/UX] "Übersetzen"-Bereich im Schedule-Editor entfernen** — Bestätigt redundant: `_run_now()` sprang schon bisher immer in den "Telegram-Export"-Tab und rief dort `run_schedule_file()` auf; die tatsächlich wirksamen, persistierten Übersetzungseinstellungen (`cb_translate`/`mode_combo`/`lang_edit`, inkl. `_save_state()`) leben in `ScheduleTab` (`ui/app.py`). Der Editor-Tab hatte eine eigene, nicht persistierte Kopie dieser drei Widgets, die beim Klick auf "Starten" die echten Einstellungen im Export-Tab stumm überschrieben hat — bei jedem Öffnen einer neuen/geladenen Schedule-Datei stand die lokale Kopie wieder auf den Defaults (unchecked/"inline"/leer), was leicht zu einem versehentlich deaktivierten Übersetzen-Lauf führen konnte, ohne dass im Editor-Tab ersichtlich war, dass die Export-Tab-Einstellung gerade überschrieben wird. Fix: `translate_cb`/`mode_combo`/`lang_edit` sowie die zugehörige UI-Zeile komplett aus `ScheduleEditorTab` entfernt; `_run_now()` überschreibt die Export-Tab-Einstellungen nicht mehr, sondern übernimmt einfach das dort bereits Konfigurierte. Nebenbefund im selben Codebereich mitgefixt: der "Starten"-Button dieses Tabs hatte zwei widersprüchliche Beschriftungen (Konstruktor: "Schedule → ODT erzeugen", `retranslate()`: "Telegram-Export → ODT erzeugen") — vereinheitlicht zu "Speichern & Starten" (bewusst nicht nur "Starten" wie in Punkt 14, da dieser Button zusätzlich speichert und in den Export-Tab wechselt).

14. **[UI/UX] Start-Button-Beschriftung im Telegram-Export-Tab** — `ui/app.py`: beide Stellen (`QPushButton`-Konstruktion, vorher "Schedule → ODT erzeugen", sowie `retranslate()`, vorher "Telegram-Export → ODT erzeugen" — die beiden waren zusätzlich inkonsistent zueinander) auf "Starten" vereinheitlicht.

**Nicht committet.** Michael: bitte Diff sichten (insbesondere Punkt 10/11, da hier neues Verhalten beim Speichern/Editieren entsteht), kurz im echten UI durchklicken, dann committen — Windows-spezifisches Risiko besteht hier anders als bei Punkt 1–8 nicht, da reine Qt-Widget-Logik ohne Plattformabhängigkeit.

---

## Offene Punkte (nächste Schritte)

1. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

2. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

3. **Feature 5 (Bildübersetzung, OCR → Übersetzen → Re-Rendern)** — **zurückgestellt** (21.08.2026). Richtung geändert, siehe unten: Standalone-CLI-Tool auf Basis von PDF-Translators bestehender Pipeline statt geteilte Python-Bibliothek, aber noch nicht begonnen — PDF-Translators Bildübersetzung ist selbst noch nicht 100% fertig (bekannte Restfälle: OCR-Fehllesungen bei Mehrspalten-/Infografik-Layouts, Cloud-Inpainting fehlt noch, siehe unten), eine Einbindung jetzt wäre doppelter Aufwand während der Reifephase. Erst wieder aufgreifen, wenn sich an PDF-Translators Bild-Pipeline nicht mehr viel ändert.

   **Prüfung "Translate PDF"-Wiederverwendung abgeschlossen (21.08.2026, Claude per Cowork):** `PDF-Translator`-Projekt (lokal `~/Projekte/PDF-Translator`, Repo `TranslatePDF.git`) lokal analysiert (README dort ist veraltet und meldet die Bildübersetzung noch als "geplant" — der tatsächliche Code widerspricht dem). `pipeline/images/` (ocr.py, inpainting.py, translate_image.py, zusammen ~66 KB) plus UI-Anbindung (`ui/image_job.py`, `ui/image_correction_dialog.py`, ~61 KB) sind dort bereits eine **fertige, getestete Funktion** — inkl. eines eigenständigen "Bilder übersetzen"-Modus (`TranslationMode.IMAGES`) in PDF-Translators eigener GUI, mit Batch-Verarbeitung mehrerer Bilder und Korrektur-Dialog: Tesseract-OCR, drei Rückschreibe-Backends (Box-Overlay, klassisches OpenCV-Inpainting, GPU-Inpainting via LaMa — auf Michaels echter GPU verifiziert), 251 bestandene Tests (Stand RoadMap.md), kalibriert an mehreren echten, von Michael gemeldeten Screenshots (Textüberlauf, falsch gelesene UI-Icons als Text, Mehrspalten-Layouts). Genau der in Scope v1 hier gewünschte Funktionsumfang (kein Font-Matching, kein Vektor-Text-Pfad) — nur bereits umgesetzt statt geplant. Cloud-OCR und Cloud-Inpainting (OpenAI) sind dort noch offen, ebenso bekannte OCR-Restfälle bei Mehrspalten-/Infografik-Layouts (mittelkonfidente Icon-Fehllesungen) — betrifft TME also ebenfalls noch, solange nicht behoben.

   **Verworfen: geteilte Python-Bibliothek.** `pipeline/images/` ist zwar sauber von PDF-Translators PDF/Word/PPTX-Code entkoppelt (einzige externe Abhängigkeit: `pipeline.translation.base.TranslationProvider`-Protocol + `protected_terms`), ein direkter Import in TME wäre aber trotzdem nicht 1:1 möglich (PDF-Translators `TranslationProvider.translate()` ist synchron, TMEs eigene Provider-Abstraktion `async def`) und würde eine enge Kopplung an PDF-Translators interne Python-APIs bedeuten, die sich während der laufenden Reifephase noch verändern.

   **Neue Richtung: Standalone-CLI-Tool.** Statt einer geteilten Bibliothek ein eigenständiges, per Subprocess aufrufbares Tool auf Basis der bestehenden Pipeline bauen (deckt sich mit der ursprünglich hier notierten Architekturentscheidung "eigenständiges Tool, Subprocess/CLI-Grenze zu TME"): Bildpfad(e) + kleine Config (Zielsprache, Provider, geschützte Begriffe) rein, übersetzte(s) Bild(er) + JSON-Report raus — analog zur bereits vorhandenen `run_image_batch_job()`. Einbindung in TME dann über dasselbe Muster wie ffmpeg/ffprobe bzw. LibreOffice/Pandoc: Tool als eigene .exe bündeln (`--add-binary`, `_find_bundled_tool()`-Check) oder als optionale externe Abhängigkeit dokumentieren, Aufruf über `run_hidden()`. Vorteile gegenüber der Bibliothek: die CLI-Schnittstelle bleibt stabil, auch während sich PDF-Translators interne OCR-/Inpainting-Logik noch weiterentwickelt (einmal integrieren, danach automatisch von Verbesserungen profitieren, ohne TME nochmal anzufassen); das async/sync-Problem der Übersetzungs-Provider entfällt komplett, da das Tool seine eigenen, synchronen Provider mitbringt. Noch nicht begonnen.

4. **UI-State-Bugs (Theme/Sprache)** — ✅ **behoben seit Commit `f574e33` (16.07.2026)**, bestätigt beim Abgleich 2026-07-30. `_load_theme_preference`/`_load_language_preference`/`_save_language_preference` nutzen im aktuellen Code (`ui/app.py:1677-1769`) korrekt die Accessor-Funktionen `_theme_state_file()`/`_lang_state_file()` statt der zuvor referenzierten, nirgends definierten Globals `THEME_STATE_FILE`/`LANG_STATE_FILE`.

5. **Lettermap/Emoji-Wort-OCR-Auto-Vorschlag** — frühere Analyse ergab: aktuell kein automatischer OCR-Vorschlag implementiert (siehe Punkt 2). Falls gewünscht, wäre ein Tesseract-basierter Vorschlagsschritt gegen die gecachten Emoji-PNGs denkbar, mit expliziter Einschränkung: unzuverlässig bei verzierten/künstlerischen Schriftstilen, eher Vorschlag als Automatik.

6. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

7. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

8. Siehe Session 2026-08-21 oben — Fix umgesetzt, Verifikation in echter Windows-Umgebung noch offen.

9. Siehe Session 2026-08-21 (Teil 2) oben — Fix umgesetzt und real gegen echte Qt-Widgets verifiziert, Commit noch offen.

10. Siehe Session 2026-08-21 (Teil 2) oben — Fix umgesetzt und real gegen echte Qt-Widgets verifiziert, Commit noch offen.

11. Siehe Session 2026-08-21 (Teil 2) oben — Fix umgesetzt und real gegen echte Qt-Widgets verifiziert, Commit noch offen.

12. Siehe Session 2026-08-21 (Teil 2) oben — Fix umgesetzt und real gegen echte Qt-Widgets verifiziert, Commit noch offen.

13. Siehe Session 2026-08-21 (Teil 2) oben — Fix umgesetzt und real gegen echte Qt-Widgets verifiziert, Commit noch offen.

14. Siehe Session 2026-08-21 (Teil 2) oben — Fix umgesetzt und real gegen echte Qt-Widgets verifiziert, Commit noch offen.

*Rückmeldungen vom 2026-07-30. Punkte 1, 2, 6, 7, 8, 9–14 mittlerweile analysiert und gefixt (siehe Sessions 2026-08-21 oben), noch nicht committet. Offen bleiben nur Punkt 3 (Feature 5, Architekturentscheidung getroffen, Umsetzung nicht begonnen) und Punkt 5 (OCR-Auto-Vorschlag, nicht begonnen).*

---

## Session 2026-09-04 — Geführte Installation (Bootstrapper), analog PDF-Translator

**Auftrag (Michael):** "Kannst Du mal schauen ob wir für dieses Projekt die gleiche Deployment Strategie wie beim 'Translate PDF' Projekt einbauen können? So das der Laie über den Downloadlink zu den jeweiligen Installer des aktuellen Releases kommt?"

**Vorbild geprüft:** PDF-Translator hat seit der letzten Prüfung (siehe Session 2026-08-21 oben) einen produktiven Zwei-Stufen-"Bootstrapper" bekommen: ein winziges, PySide6-freies `tkinter`-Programm (`bootstrap/`) führt durch Sprache/Modus, lädt den eigentlichen App-Code als GitHub-Release-ZIP herunter, legt eine Pro-Benutzer-venv an, installiert `requirements*.txt` per normalem `pip install` (keine Admin-/root-Rechte nötig) und legt einen Menüeintrag an, der direkt auf `python -m ui.app` in diesem venv zeigt — kein PyInstaller-Freeze der eigentlichen App. Gebaut/veröffentlicht über `.github/workflows/build-bootstrap.yml` (Matrix Linux/Windows/macOS, PyInstaller nur für den Bootstrapper selbst), ausgelöst durch einen Versions-Tag `vX.Y.Z`, der gegen `_version.py`s `__version__` geprüft wird (Release bricht sonst ab, bevor etwas veröffentlicht wird). Details siehe PDF-Translators README.md ("Architektur der geführten Installation").

**Entscheidung (Michael, zwei Fragen beantwortet):**
1. Bestehender PyInstaller-Vollbuild (`TME.spec`/`scripts/build_win.ps1`/`TME_mac.spec`/`build_linux.sh`) wird **nicht ersetzt** — "Parallel anbieten". Beide Wege bestehen nebeneinander, README.md dokumentiert jetzt beide.
2. **Direkt umsetzen** statt erst nur einen Plan ins Backlog zu schreiben.

**Umgesetzt (dieselbe Sitzung):** neues Paket `bootstrap/` (`__init__.py`, `__main__.py`, `paths.py`, `system_lang.py`, `wizard_text.py`, `release_source.py`, `credentials_step.py`, `installer.py`, `desktop_integration.py`, `controller.py`, `app.py`), neue `_version.py` (Start bei `0.1.0`), neuer Workflow `.github/workflows/build-bootstrap.yml`, README.md um Abschnitte "Für alle anderen (geführte Installation)", "Release-Prozess (geführte Installation)" und "Architektur der geführten Installation" ergänzt (bestehende Entwickler-/Vollbuild-Abschnitte unverändert).

**TME-spezifische Anpassungen gegenüber dem PDF-Translator-Vorbild:**
- Kein Online/Lokal-Modus mit GPU-Prüfung: TMEs Übersetzungs-Provider sind ohnehin alle Cloud-basiert. Stattdessen ein einfacherer Standard/Transkription-Modus (`InstallMode.STANDARD`/`WITH_STT`) für die bereits bestehende optionale Sprachnachrichten-Transkription (`requirements-stt.txt`, ~4-5 GB, siehe `pipeline/speech_to_text.py`). Kein `gpu_check.py`-Äquivalent — `get_torch_device()` erkennt eine nutzbare GPU zur Laufzeit selbst und fällt sonst auf CPU zurück, PyPIs Standard-Torch-Wheels brauchen dafür (anders als PDF-Translators LaMa) keinen speziellen CUDA-Wheel-Index. **Diese Vereinfachung ist nicht durch einen echten Lauf verifiziert.**
- Eigener Zugangsdaten-Schritt für Telegram API-ID/API-Hash (+ optionales Telefon) statt eines GPU-Schritts, zusätzlich zur bereits vom Vorbild übernommenen Provider-Schlüssel-Checkliste (DeepL/Google/OpenAI — kein Grok bei TME). Nutzt TMEs bereits bestehendes Top-Level `credentials.py` wieder (dort bereits vorhanden: `save_telegram_credentials`, `save_provider_api_key`, `get_provider_api_key_source` — kein neuer Code auf App-Seite nötig).
- Eigener Text-Katalog `bootstrap/wizard_text.py` (DE/EN) statt Import aus `ui/i18n_data.py`: TME nutzt Qt-natives `.qm`/`.ts` für seine eigentliche Mehrsprachigkeit, kein direkt importierbares Python-Dict wie beim Vorbild. Der Assistent selbst bleibt DE/EN; TMEs deutlich umfangreichere Qt-Übersetzungen sind davon unabhängig.
- Icon: `assets/icon.ico`/`.icns`/`.png` existieren in TME noch nicht (anders als PDF-Translators `assets/icon.svg` + `tools/build_icon.py`). `desktop_integration.py` fällt deshalb unter Linux auf die bereits vorhandene `Telegram-LibreOffice.png` zurück, unter Windows/macOS gibt es vorerst kein eingebettetes Icon (`build-bootstrap.yml`s `icon_arg` bleibt leer). PyInstaller-Build und App selbst funktionieren auch ohne, nur kosmetisch unvollständig.

**Nicht umgesetzt / offene Folgeschritte:**
- **Kein echter Build/Install/Start-Zyklus durchgeführt** — nur `py_compile` (alle neuen `.py`-Dateien) und YAML-Parsing der Workflow-Datei geprüft, kein echter `pyinstaller`-Lauf, kein echter `pip install` in eine venv, kein echter Start von `python -m ui.app` aus dem venv heraus. Vor dem ersten echten Release-Tag unbedingt einmal komplett lokal durchspielen (`python -m bootstrap` bzw. `python -m bootstrap.app`).
- Kein Selbst-Update in der laufenden App (anders als PDF-Translators `ui/workers.py`-Integration) — `bootstrap/release_source.py` bietet dieselbe GitHub-API-Grundlage bereits an, eine Anbindung in `ui/app.py` wurde bewusst nicht blind an der 91-KB-Datei vorgenommen, ohne sie testen zu können.
- Sprachmarkierungsdatei (`bootstrap/paths.py::language_marker_file()`) wird geschrieben, aber von `ui/app.py` noch nicht gelesen — kleiner, risikoarmer Folgeschritt.
- `assets/icon.ico`/`.icns`/`.png` fehlen noch (siehe oben) — ließen sich aus der bestehenden `Telegram-LibreOffice.png` erzeugen, analog zu PDF-Translators `tools/build_icon.py`.
- Kein Code-Signing (wie beim Vorbild bewusst zurückgestellt) — SmartScreen/Gatekeeper-Warnung beim ersten Start bleibt bestehen.
- ffmpeg/LibreOffice/Pandoc bleiben separat zu installierende Systemwerkzeuge, wie schon im Entwickler-Weg (siehe docs/DEPLOY.md) — der Bootstrapper bündelt nur Python-Pakete.

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
