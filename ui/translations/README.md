# Übersetzungen (Qt / PySide6)

- Quelle: UI-Strings sind mit `self.tr("…")` markiert (Kontext = Klassenname).
- Sprachdateien:
  - app_de.ts → Deutsch (Ausgangssprache)
  - app_en.ts → Englisch
- Build (benötigt Qt-Tools `lrelease`):

```bash
# Im Projektroot oder im Ordner ui/
cd ui/translations
# Automatisches Finden von lrelease (Qt6/Qt5):
./build_qm.sh

# Alternativ manuell (je nach Distribution):
/usr/lib/qt6/bin/lrelease app_de.ts -qm app_de.qm
/usr/lib/qt6/bin/lrelease app_en.ts -qm app_en.qm
# oder
lrelease-qt6 app_de.ts -qm app_de.qm
lrelease-qt6 app_en.ts -qm app_en.qm
```

- Laufzeit: `ui/app.py` lädt `ui/translations/app_<lang>.qm` gemäß `data/ui_lang.json` (`{"lang": "de"|"en"}`).
- Umschalten im Menü: Sprache → Deutsch/Englisch erfolgt live (ohne Neustart).
