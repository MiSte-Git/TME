"""Einmal-Skript: liest ueber die bestehende, bereits autorisierte TME-Session
die letzten Nachrichten vom offiziellen Telegram-Systemkontakt (777000), um
einen Login-Code zu finden, der von einer ANDEREN App (z.B. TUA) angefordert
wurde, aber vom Nutzer nirgends sichtbar ankam.

Rein lesend - es wird nichts angemeldet, kein Code angefordert, keine
Nachricht geschickt. Nutzt exakt dieselbe Session-Datei/Credentials wie das
TME-Projekt selbst (SESSION_NAME "tg_session", credentials.py), damit die
bestehende Autorisierung wiederverwendet wird statt eine neue anzufordern.

Ausfuehren im TME-Projektordner:
    .venv/bin/python check_login_code.py
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.tl.types import PeerUser

from credentials import get_telegram_credentials

SESSION_NAME = "tg_session"
TELEGRAM_SERVICE_USER_ID = 777000  # offizieller "Telegram"-Systemkontakt


async def main() -> None:
    api_id, api_hash, _phone = get_telegram_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            print(
                "Diese Session ist NICHT (mehr) autorisiert - kann darüber "
                "also keine Nachrichten lesen. Bitte TME einmal regulär "
                "starten/einloggen, dann dieses Skript erneut ausführen."
            )
            return

        me = await client.get_me()
        print(f"Autorisiert als: {getattr(me, 'phone', '?')} (user_id={me.id})")
        print("-" * 60)

        try:
            entity = await client.get_entity(PeerUser(TELEGRAM_SERVICE_USER_ID))
        except Exception as e:
            print(f"Konnte den 'Telegram'-Systemkontakt nicht auflösen: {e}")
            return

        messages = await client.get_messages(entity, limit=10)
        if not messages:
            print("Keine Nachrichten von 'Telegram' (777000) gefunden.")
            return

        for msg in messages:
            date = msg.date.strftime("%Y-%m-%d %H:%M:%S %Z") if msg.date else "?"
            text = (msg.message or "").replace("\n", " ")
            print(f"[{date}] {text}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
