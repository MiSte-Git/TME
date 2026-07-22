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
from typing import Callable, Optional

from telethon import TelegramClient, functions
from telethon.errors import ApiIdInvalidError, SendCodeUnavailableError

from credentials import get_telegram_credentials

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
    password_callback: Callable[[Optional[str]], str],
) -> TelegramLoginResult:
    """Führt den Telegram-Login aus (erzeugt/erneuert tg_session.session) und
    gibt Infos zum eingeloggten Account zurück.

    phone_callback/code_callback werden 1:1 an telethon.TelegramClient.start()
    durchgereicht. password_callback wird von Telethon nur aufgerufen, wenn
    das Konto tatsächlich 2FA aktiviert hat, und bekommt dabei (anders als bei
    Telethon selbst) den von Telegram hinterlegten Passwort-Hinweis
    (account.password.hint) als Argument übergeben, sofern vorhanden, sonst
    None - Telethons eigener password-Callback-Vertrag liefert diesen Hinweis
    nicht mit (er wird intern erst NACH dem Callback-Aufruf abgefragt, siehe
    AuthMethods.sign_in), daher wird er hier separat vorab per
    GetPasswordRequest geholt.
    Muss aus einem Thread ohne bereits laufende asyncio-Event-Loop aufgerufen
    werden (siehe _ensure_thread_event_loop).
    """
    _ensure_thread_event_loop()
    api_id, api_hash, _phone = get_telegram_credentials()
    # Bewusst der bare, CWD-relative Name (nicht ein __file__-relativer
    # Absolutpfad wie zuvor): pipeline/runner_schedule.py und
    # pipeline/runner_by_ids.py verwenden für ihre TelegramClient-Instanzen
    # ebenfalls den bloßen Namen "tg_session", der von Telethon relativ zum
    # aktuellen Arbeitsverzeichnis aufgelöst wird. Ein __file__-relativer Pfad
    # weicht davon in einem PyInstaller-Bundle ab (dort liegt __file__
    # irgendwo unter _internal/, während die Desktop-Datei das
    # Arbeitsverzeichnis auf den Installationsordner setzt) - Login und
    # Schedule-Lauf würden dann in zwei verschiedene Session-Dateien
    # schreiben/lesen, wodurch ein eigentlich erfolgreicher Login beim
    # nächsten Lauf sofort wieder als "Session ungültig" erscheint.
    client = TelegramClient(SESSION_NAME, api_id, api_hash)

    async def _password_with_hint() -> str:
        hint: Optional[str] = None
        try:
            pwd = await client(functions.account.GetPasswordRequest())
            hint = getattr(pwd, "hint", None) or None
        except Exception:
            # Hinweis ist rein informativ - bei Fehlschlag (z.B. Netzwerk)
            # einfach ohne Hinweis nach dem Passwort fragen, wie bisher.
            hint = None
        return password_callback(hint)

    try:
        try:
            client.start(
                phone=phone_callback,
                code_callback=code_callback,
                password=_password_with_hint,
            )
        except ApiIdInvalidError as exc:
            # Passiert, wenn get_telegram_credentials() zwar WERTE liefert
            # (kein TelegramCredentialsMissing), diese bei Telegram aber
            # ungültig sind - z.B. weil TELEGRAM_API_ID/TELEGRAM_API_HASH
            # (haben Vorrang, siehe credentials.py) noch auf einen alten/
            # Platzhalter-Wert gesetzt sind und dadurch eine an sich korrekte
            # ~/.config/telegram-odt/credentials.json stillschweigend
            # überschreiben. Ohne diese gezielte Meldung zeigt der Dialog nur
            # Telethons rohe, wenig hilfreiche Fehlermeldung an.
            raise RuntimeError(
                "Telegram meldet: API ID/API Hash ungültig. Geprüfte Quellen "
                "(in dieser Reihenfolge, erste vorhandene gewinnt): Umgebungsvariablen "
                "TELEGRAM_API_ID/TELEGRAM_API_HASH, sonst "
                "~/.config/telegram-odt/credentials.json. Bitte prüfen, ob dort "
                "veraltete/Platzhalter-Werte hinterlegt sind, und die echten "
                "Werte von https://my.telegram.org verwenden (siehe docs/DEPLOY.md). "
                "Häufige Ursache ist auch ein Tippfehler in der api_id selbst "
                "(z.B. eine Ziffer zu viel oder zu wenig beim manuellen "
                "Abtippen/Eintragen) - api_id/api_hash am besten per Copy-Paste "
                "direkt von my.telegram.org übernehmen statt abzutippen."
            ) from exc
        except SendCodeUnavailableError as exc:
            # Telegram-Rate-Limit: zu viele Code-Anfragen fuer dieselbe
            # Telefonnummer in kurzer Zeit (z.B. durch mehrere Login-Versuche
            # hintereinander waehrend der Fehlersuche zu Problem 1/2). Ohne
            # diese gezielte Meldung sieht der Nutzer nur Telethons rohen,
            # wenig aussagekraeftigen Fehlertext.
            raise RuntimeError(
                "Telegram hat kurzzeitig zu viele Code-Anfragen für diese "
                "Nummer erhalten. Bitte 10-15 Minuten warten und erneut "
                "versuchen."
            ) from exc
        # get_me() ist (anders als start()/disconnect()) eine reine "async def"-
        # Methode ohne eingebaute Sync-Wandlung - ein direkter Aufruf liefert nur
        # ein Coroutine-Objekt zurueck statt des User-Objekts.
        me = client.loop.run_until_complete(client.get_me())
        if me is None:
            raise RuntimeError("Login abgeschlossen, aber get_me() lieferte kein Ergebnis.")
        return TelegramLoginResult(user_id=me.id, username=getattr(me, "username", None))
    finally:
        client.disconnect()
