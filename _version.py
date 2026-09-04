"""Single source of truth for TME's version number.

04.09.2026 - eingeführt zusammen mit dem neuen Installations-Assistenten
(bootstrap/, siehe dessen __init__.py-Docstring), analog zum bereits
produktiven Vorbild in PDF-Translator (_version.py dort, siehe dessen
Docstring für die volle Begründung). TME hatte vorher keine Versionsnummer,
nur einen bei jedem PyInstaller-Vollbuild eingebetteten Git-Kurz-Hash
("Versionsstempel", BUILD_VERSION.txt) - der bleibt für den bestehenden
Vollbuild-Weg unverändert bestehen (siehe TME.spec/build_win.ps1/
build_linux.sh/TME_mac.spec), __version__ hier ist ausschließlich für den
NEUEN, parallel existierenden Bootstrapper-Weg relevant.

Gelesen von zwei Stellen, aus je einem anderen Grund:
- .github/workflows/build-bootstrap.yml's build-source-archive-Job liest
  diese Datei (ein kurzer `python -c "from _version import __version__; ..."`-
  Check) und bricht das gesamte Release ab, BEVOR irgendetwas veröffentlicht
  wird, falls der gepushte `vX.Y.Z`-Tag und __version__ hier nicht
  übereinstimmen - der Tag löst das Release aus und ist das, wonach
  bootstrap/release_source.py's GitHub-API-Aufrufe suchen, __version__ ist
  das, wonach ein zukünftiger Selbst-Update-Check sich selbst vergleichen
  würde. Beide dürfen nie stillschweigend auseinanderlaufen.
- bootstrap/release_source.py importiert nichts von hier (bleibt bewusst
  dependency-frei, siehe dessen Docstring), aber ein künftiger
  Selbst-Update-Check in ui/app.py (noch nicht umgesetzt, siehe
  TME-Backlog.md) würde das analog zu PDF-Translators ui/app.py tun.

Bewusst ein einzelnes Top-Level-Modul ohne Imports, nicht in pipeline/ oder
ui/ - damit jeder Teil des Projekts (bootstrap/, pipeline/, ui/) es
gefahrlos importieren kann, ohne etwas anderes mitzuziehen.

**Ablauf für ein neues Release** (siehe auch README.md-Abschnitt
"Release-Prozess" - dort ausführlicher für Release-Durchführende, hier nur
als Kurzreferenz für wer diese Datei ändert):
1. __version__ unten erhöhen (Semver: MAJOR.MINOR.PATCH - siehe
   https://semver.org).
2. Als eigenen Commit einchecken ("Version X.Y.Z").
3. `git tag vX.Y.Z && git push origin main vX.Y.Z` - das führende "v" gehört
   zum Git-Tag, nicht zu __version__ selbst.
4. .github/workflows/build-bootstrap.yml's `push: tags: v*`-Trigger baut die
   drei Assistenten-Executables und hängt das Quellcode-ZIP als Release-
   Asset an.
"""
from __future__ import annotations

__version__ = "0.1.0"
