"""Regressionstest: alle _notify(...)-Aufrufe in runner_schedule.py müssen
über QCoreApplication.translate("RunnerSchedule", ...) laufen statt über
hartkodierte deutsche String-/f-string-Literale direkt als Argument.

Hintergrund: self.tr() ist eine Qt-Widget-Methode und im Backend
(pipeline/runner_schedule.py, kein QObject) nicht verfügbar. Die
Statusmeldungen, die _notify() an die UI (status_label/cost_status_label)
durchreicht, liefen deshalb bislang IMMER auf Deutsch, unabhängig von der
in der UI gewählten Sprache. Fix: QCoreApplication.translate() - braucht
keine QObject-Instanz, nutzt dieselbe .ts/.qm-Infrastruktur wie der Rest
der UI.

Kein pytest im Projekt (siehe requirements.txt) - eigenständiges Skript wie
die übrigen tests/test_*.py. Aufruf:
    .venv/bin/python tests/test_notify_i18n.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

RUNNER_SCHEDULE_PATH = REPO_ROOT / "pipeline" / "runner_schedule.py"

# Erkennt _notify(-Aufrufe, deren erstes sichtbares Token direkt ein
# String-Literal ist (mit oder ohne f-Prefix) - also der frühere,
# hartkodiert-deutsche Zustand. Ein korrekt umgestellter Aufruf beginnt
# stattdessen mit _notify(QCoreApplication.translate(...)).
_HARDCODED_NOTIFY_RE = re.compile(r'_notify\(\s*f?"')


def test_no_hardcoded_notify_strings() -> None:
    src = RUNNER_SCHEDULE_PATH.read_text(encoding="utf-8")
    matches = _HARDCODED_NOTIFY_RE.findall(src)
    assert not matches, (
        f"{len(matches)} _notify(...)-Aufruf(e) mit hartkodiertem String-Literal "
        f"gefunden (sollten über QCoreApplication.translate(\"RunnerSchedule\", ...) laufen): {matches}"
    )
    print("[OK] Keine _notify(...)-Aufrufe mit hartkodiertem String-Literal gefunden.")


def test_notify_calls_use_qcoreapplication_translate() -> None:
    src = RUNNER_SCHEDULE_PATH.read_text(encoding="utf-8")
    notify_call_count = len(re.findall(r'\b_notify\(', src)) - 1  # -1 fuer die def _notify(...)-Zeile selbst
    translate_call_count = len(re.findall(r'QCoreApplication\.translate\(\s*"RunnerSchedule"', src))
    assert translate_call_count >= 30, (
        f"Erwartet mindestens 30 QCoreApplication.translate(\"RunnerSchedule\", ...)-Aufrufe, "
        f"gefunden {translate_call_count} (bei {notify_call_count} _notify()-Aufrufen)."
    )
    print(f"[OK] {translate_call_count} QCoreApplication.translate(\"RunnerSchedule\", ...)-Aufrufe "
          f"für {notify_call_count} _notify()-Aufrufe gefunden.")


def test_qcoreapplication_translate_works_without_app_instance() -> None:
    """QCoreApplication.translate() darf im reinen CLI-Pfad (siehe
    pipeline/adapters/existing_scripts.py) NICHT crashen, auch wenn nie eine
    QApplication/QCoreApplication-Instanz erstellt wurde - liefert dann
    einfach den unübersetzten Quelltext zurück."""
    from PySide6.QtCore import QCoreApplication
    result = QCoreApplication.translate("RunnerSchedule", "Fertig.")
    assert result == "Fertig.", f"Fallback ohne App-Instanz sollte Quelltext liefern, war: {result!r}"
    print("[OK] QCoreApplication.translate() funktioniert auch ohne QApplication-Instanz (CLI-Fallback).")


if __name__ == "__main__":
    test_no_hardcoded_notify_strings()
    test_notify_calls_use_qcoreapplication_translate()
    test_qcoreapplication_translate_works_without_app_instance()
    print("ALLE TESTS BESTANDEN")
