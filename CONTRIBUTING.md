# Contributing

Danke für dein Interesse an diesem Projekt! Ein paar kurze Hinweise, bevor du loslegst.

## Sprache

Durchgehend Deutsch – Commits, Code-Kommentare sowie Diskussionen in Issues/PRs bitte
auf Deutsch verfassen.

## Lokal testen & bauen

- Abhängigkeiten installieren: `python3 -m pip install -r requirements.txt`
- Syntax-Check (ohne Telegram-Zugriff): `python3 -m compileall -q . -x "[\\/](\.venv|build|dist)[\\/]"`
  (Ausschluss nötig, sonst scannt compileall bei lokalem `.venv` im Repo-Root
  auch Fremdpakete mit, was fälschlich fehlschlagen kann)
- UI starten: `python3 ui/app.py`
- Desktop-Bundles (macOS/Windows/Linux) sowie Ablage der Telegram-API-Keys: siehe
  [docs/DEPLOY.md](docs/DEPLOY.md)
- Vor einem PR: UI und mindestens ein CLI-Lauf (`pipeline/emoji_pipeline.py`) mit einer
  Beispiel-Schedule (z. B. `input/example.json`) sollten fehlerfrei durchlaufen.

## Pull Requests

1. Fork/Branch von `main` erstellen.
2. Änderungen klein und fokussiert halten; bestehenden Code-Stil beibehalten (PEP 8,
   snake_case Funktionen, UPPER_CASE Konstanten – siehe [AGENTS.md](AGENTS.md)).
3. Keine echten Zugangsdaten, Session-Dateien oder private Chat-/Nutzerdaten committen.
4. PR mit kurzer Beschreibung öffnen: was ändert sich und warum.
5. Wird bei Gelegenheit reviewt – bei größeren Änderungen vorher gerne ein Issue zur
   Abstimmung anlegen.
