"""Gemeinsame Telegram-Login-Logik für scripts/telegram_login.py (Konsole) und
ui/login_dialog.py (Qt-Dialog) - vermeidet doppelte client.start()-Logik.

Telethons client.start() fragt normalerweise blockierend über input()/getpass()
nach Telefonnummer/Code/2FA-Passwort. Hier werden stattdessen Callback-Funktionen
übergeben, die vom jeweiligen Aufrufer bereitgestellt werden (Konsole: input()/
getpass(); GUI: blockiert nur den Login-Worker-Thread, bis der Nutzer ein
Dialogfeld ausgefüllt hat) - so bleibt die eigentliche Login-Logik an einer
einzigen Stelle.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from telethon import TelegramClient

from credentials import get_telegram_credentials

ROOT = Path(__file__).resolve().parents[1]
SESSION_NAME = "tg_session"


class LoginCancelled(Exception):
    """Wird geworfen, wenn der Nutzer den Login-Dialog abbricht, während eine
    Callback-Abfrage (Telefonnummer/Code/Passwort) noch aussteht."""


@dataclass
class TelegramLoginResult:
    user_id: int
    username: Optional[str]


def _ensure_thread_event_loop() -> None:
    """Telethons client.start() läuft (außerhalb einer bereits laufenden Event-
    Loop) synchron über self.loop.run_until_complete() und braucht dafür eine für
    den aktuellen Thread gesetzte Event-Loop. Im Hauptthread eines frischen
    Konsolen-Skripts existiert die implizit; in einem Qt-Worker-Thread dagegen
    nicht - ohne diesen Schritt wirft asyncio.get_event_loop() dort RuntimeError."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def perform_telegram_login(
    phone_callback: Callable[[], str],
    code_callback: Callable[[], str],
    password_callback: Callable[[], str],
) -> TelegramLoginResult:
    """Führt den Telegram-Login aus (erzeugt/erneuert tg_session.session) und
    gibt Infos zum eingeloggten Account zurück.

    phone_callback/code_callback/password_callback werden 1:1 an
    telethon.TelegramClient.start() durchgereicht; password_callback wird von
    Telethon nur aufgerufen, wenn das Konto tatsächlich 2FA aktiviert hat.
    Muss aus einem Thread ohne bereits laufende asyncio-Event-Loop aufgerufen
    werden (siehe _ensure_thread_event_loop).
    """
    _ensure_thread_event_loop()
    api_id, api_hash, _phone = get_telegram_credentials()
    client = TelegramClient(str(ROOT / SESSION_NAME), api_id, api_hash)
    try:
        client.start(
            phone=phone_callback,
            code_callback=code_callback,
            password=password_callback,
        )
        # get_me() ist (anders als start()/disconnect()) eine reine "async def"-
        # Methode ohne eingebaute Sync-Wandlung - ein direkter Aufruf liefert nur
        # ein Coroutine-Objekt zurueck statt des User-Objekts.
        me = client.loop.run_until_complete(client.get_me())
        if me is None:
            raise RuntimeError("Login abgeschlossen, aber get_me() lieferte kein Ergebnis.")
        return TelegramLoginResult(user_id=me.id, username=getattr(me, "username", None))
    finally:
        client.disconnect()
