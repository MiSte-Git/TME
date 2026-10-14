# Übersetzungen (Qt / PySide6)

- Quelle: UI-Strings sind mit `self.tr("…")` markiert (Kontext = Klassenname), Ausgangssprache ist Deutsch.
- Sprachen (je `app_<lang>.ts` + kompilierte `app_<lang>.qm`): de, en, fr, it, ru, pl, es, hr, nl, fi.
- Laufzeit: `ui/app.py` lädt `ui/translations/app_<lang>.qm` gemäß `data/ui_lang.json` (`{"lang": "<code>"}`).
- Umschalten: über die Flaggenleiste oben im Hauptfenster, live ohne Neustart.

## Neue oder geänderte Texte übernehmen

```bash
# 1. .ts-Dateien aus dem Quellcode aktualisieren (im Projektroot)
pyside6-lupdate ui/*.py -ts ui/translations/app_*.ts

# 2. Neue Einträge (type="unfinished") übersetzen, z.B. mit pyside6-linguist

# 3. .qm-Dateien bauen (findet lrelease / pyside6-lrelease automatisch)
cd ui/translations && ./build_qm.sh
```

Prüfen, ob noch etwas offen ist:

```bash
grep -c 'type="unfinished"' ui/translations/app_*.ts
```
