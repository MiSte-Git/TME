"""Interaktiver Telegram-Login: erzeugt/erneuert die Session-Datei tg_session.session.

Aufruf:
    .venv\\Scripts\\python.exe scripts\\telegram_login.py

Fragt bei Bedarf interaktiv nach Telefonnummer, Login-Code und ggf. 2FA-Passwort
(Telethon client.start()) und bestätigt anschliessend, welcher Account eingeloggt
ist. Danach ist die Session wieder für runner_schedule.py / runner_by_ids.py gültig.
"""
from __future__ import annotations

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

from telethon import TelegramClient  # noqa: E402

from credentials import get_telegram_credentials  # noqa: E402


def main() -> None:
    api_id, api_hash, phone = get_telegram_credentials()

    print(f"Verbinde mit Telegram (Session: {ROOT / 'tg_session.session'}) ...")
    client = TelegramClient("tg_session", api_id, api_hash)

    # client.start() ist hier bewusst synchron aufgerufen (kein eigener asyncio-Code
    # noetig) - fragt interaktiv nach Telefonnummer/Code/2FA-Passwort, falls die
    # Session noch nicht (mehr) autorisiert ist.
    start_kwargs = {"phone": phone} if phone else {}
    client.start(**start_kwargs)

    me = client.get_me()
    if me is not None:
        username = f"@{me.username}" if getattr(me, "username", None) else "(kein Username gesetzt)"
        print(f"Erfolgreich eingeloggt als: {username}, user_id={me.id}")
    else:
        print("Warnung: Login abgeschlossen, aber get_me() lieferte kein Ergebnis - bitte pruefen.")

    client.disconnect()
    print("Session gespeichert. Schedule-/By-IDs-Läufe sollten jetzt wieder funktionieren.")


if __name__ == "__main__":
    main()
