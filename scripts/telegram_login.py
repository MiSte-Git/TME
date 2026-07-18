"""Interaktiver Telegram-Login: erzeugt/erneuert die Session-Datei tg_session.session.

Aufruf:
    .venv\\Scripts\\python.exe scripts\\telegram_login.py

Fragt bei Bedarf interaktiv nach Telefonnummer, Login-Code und ggf. 2FA-Passwort
und bestätigt anschliessend, welcher Account eingeloggt ist. Danach ist die
Session wieder für runner_schedule.py / runner_by_ids.py gültig.

Konsolen-Fallback zum Login-Dialog in der UI (ui/login_dialog.py) - beide nutzen
dieselbe Login-Logik aus pipeline/telegram_login.py, nur die Ein-/Ausgabe
unterscheidet sich (hier: input()/getpass(), dort: Dialogfelder).
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Session-Datei "tg_session.session" soll im selben Verzeichnis landen, das auch
# runner_schedule.py/runner_by_ids.py verwenden (dort ebenfalls relativer Name
# "tg_session", ausgehend vom Repo-Root).
os.chdir(ROOT)

from credentials import get_telegram_credentials  # noqa: E402
from pipeline.telegram_login import perform_telegram_login  # noqa: E402


def _phone_callback() -> str:
    # Bereits bekannte Telefonnummer (ENV/credentials.json) ohne Rückfrage
    # verwenden - identisches Verhalten wie vor dem Refactoring auf die
    # gemeinsame Login-Logik.
    _, _, cfg_phone = get_telegram_credentials()
    if cfg_phone:
        return cfg_phone
    return input("Bitte Telefonnummer eingeben (inkl. Ländervorwahl, z.B. +49...): ")


def _code_callback() -> str:
    return input("Bitte den per Telegram erhaltenen Bestätigungscode eingeben: ")


def _password_callback() -> str:
    return getpass.getpass("Bitte 2FA-Passwort eingeben: ")


def main() -> None:
    print(f"Verbinde mit Telegram (Session: {ROOT / 'tg_session.session'}) ...")
    result = perform_telegram_login(
        phone_callback=_phone_callback,
        code_callback=_code_callback,
        password_callback=_password_callback,
    )
    username = f"@{result.username}" if result.username else "(kein Username gesetzt)"
    print(f"Erfolgreich eingeloggt als: {username}, user_id={result.user_id}")
    print("Session gespeichert. Schedule-/By-IDs-Läufe sollten jetzt wieder funktionieren.")


if __name__ == "__main__":
    main()
