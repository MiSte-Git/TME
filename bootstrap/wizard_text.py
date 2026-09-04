"""Statisch mitgelieferter DE/EN-Textkatalog für die eigenen Bildschirme des
Bootstrapper-Assistenten (Sprachwahl, Modus, Installation, Zugangsdaten,
Fertig).

TME hat - anders als PDF-Translator, dessen ui/i18n_data.py ein direkt
importierbares Python-Dict bereitstellt - ein Qt-natives Übersetzungssystem
(ui/translations/*.qm, geladen via QTranslator, siehe ui/app.py). Das kann
dieses bewusst Qt-freie Paket (siehe bootstrap/__init__.py-Docstring) nicht
nutzen, ohne PySide6 vorauszusetzen - der Assistent muss aber rendern,
BEVOR überhaupt etwas heruntergeladen wurde. Deshalb hier ein eigener,
kleiner, hart einkompilierter Katalog statt eines Imports aus ui/.

Nur Deutsch und Englisch (siehe bootstrap/system_lang.py) - TMEs eigene,
deutlich umfangreichere Qt-Übersetzungen sind davon unabhängig und
gelten erst nach dem Start der eigentlichen App.
"""
from __future__ import annotations

DE: dict[str, str] = {
    "bootstrap.window_title": "TME installieren",
    "bootstrap.back_button": "Zurück",
    "bootstrap.next_button": "Weiter",
    "bootstrap.language_label": "Sprache",
    "bootstrap.welcome_title": "Willkommen bei TME",
    "bootstrap.welcome_text": (
        "Dieser Assistent installiert TME (Telegram → ODT Message Exporter) "
        "auf diesem Rechner. Es werden keine Administrator-/root-Rechte "
        "benötigt, es wird nichts systemweit installiert."
    ),
    "bootstrap.mode_title": "Sprachnachrichten-Transkription",
    "bootstrap.mode_intro": (
        "TME kann Sprachnachrichten optional automatisch transkribieren "
        "(OpenAI Whisper) und den erkannten Text ins Dokument einfügen. "
        "Das ist ein größerer, einmaliger Zusatz-Download (ca. 4-5 GB) und "
        "lässt sich jederzeit später nachrüsten."
    ),
    "bootstrap.mode_standard_label": "Standard",
    "bootstrap.mode_standard_desc": "Ohne automatische Sprachnachrichten-Transkription. Kleinerer Download.",
    "bootstrap.mode_stt_label": "Standard + Sprachnachrichten-Transkription",
    "bootstrap.mode_stt_desc": (
        "Zusätzlich ca. 4-5 GB Download. Läuft auf der eigenen CPU; ist eine "
        "unterstützte NVIDIA-Grafikkarte vorhanden, wird sie automatisch "
        "genutzt und beschleunigt die Transkription deutlich."
    ),
    "bootstrap.install_title": "Installation läuft…",
    "bootstrap.install_step_source": "Lade Programmcode herunter…",
    "bootstrap.install_step_venv": "Lege eigene Python-Umgebung an…",
    "bootstrap.install_step_deps": "Installiere Abhängigkeiten ({name})…",
    "bootstrap.install_step_shortcut": "Erstelle Eintrag im Anwendungsmenü…",
    "bootstrap.install_failed_title": "Installation fehlgeschlagen",
    "bootstrap.install_failed": "Es ist ein Fehler aufgetreten:\n\n{error}",
    "bootstrap.credentials_title": "Zugangsdaten (optional)",
    "bootstrap.credentials_intro": (
        "Diese Schritte lassen sich überspringen und später jederzeit direkt "
        "in der App nachholen."
    ),
    "bootstrap.credentials_telegram_label": "Telegram API-Zugangsdaten",
    "bootstrap.credentials_telegram_explain": (
        "Für den Zugriff auf Telegram werden eine API-ID und ein API-Hash "
        "benötigt, kostenlos erhältlich auf my.telegram.org. Wird dieser "
        "Schritt übersprungen, fragt TME beim ersten Lauf automatisch danach."
    ),
    "bootstrap.credentials_telegram_api_id_label": "API-ID",
    "bootstrap.credentials_telegram_api_hash_label": "API-Hash",
    "bootstrap.credentials_telegram_phone_label": "Telefonnummer (optional)",
    "bootstrap.credentials_open_signup_button": "Seite öffnen",
    "bootstrap.credentials_save_button": "Speichern",
    "bootstrap.credentials_skip_button": "Überspringen",
    "bootstrap.credentials_saved": "Gespeichert.",
    "bootstrap.credentials_saved_plaintext_warning": (
        "Gespeichert - allerdings ohne verschlüsseltes System-Schlüsselbund "
        "(kein Backend gefunden), deshalb als Klartext in credentials.json."
    ),
    "bootstrap.credentials_provider_intro": "Optional: API-Schlüssel für externe Übersetzungs-Provider hinterlegen.",
    "bootstrap.credentials_provider_deepl": "DeepL",
    "bootstrap.credentials_provider_google": "Google Translate",
    "bootstrap.credentials_provider_openai": "ChatGPT / OpenAI",
    "bootstrap.credentials_explain_deepl": "DeepL API-Schlüssel (kostenlose und kostenpflichtige Stufen verfügbar).",
    "bootstrap.credentials_explain_google": "Google Cloud Translation API-Schlüssel.",
    "bootstrap.credentials_explain_openai": "OpenAI API-Schlüssel (für ChatGPT-Übersetzung).",
    "bootstrap.credentials_key_label": "API-Schlüssel für {provider}:",
    "bootstrap.credentials_continue_button": "Weiter",
    "bootstrap.credentials_skip_all_button": "Alle überspringen",
    "bootstrap.finish_title": "Fertig",
    "bootstrap.finish_text": (
        "TME wurde installiert und ist im Anwendungsmenü/Startmenü zu finden. "
        "Von dort lässt es sich auch an die Taskleiste/das Dock anheften."
    ),
    "bootstrap.finish_launch_button": "TME jetzt starten",
    "bootstrap.finish_close_button": "Schließen",
}

EN: dict[str, str] = {
    "bootstrap.window_title": "Install TME",
    "bootstrap.back_button": "Back",
    "bootstrap.next_button": "Next",
    "bootstrap.language_label": "Language",
    "bootstrap.welcome_title": "Welcome to TME",
    "bootstrap.welcome_text": (
        "This wizard installs TME (Telegram to ODT Message Exporter) on this "
        "computer. No administrator/root rights are needed, and nothing is "
        "installed system-wide."
    ),
    "bootstrap.mode_title": "Voice message transcription",
    "bootstrap.mode_intro": (
        "TME can optionally transcribe voice messages automatically (OpenAI "
        "Whisper) and insert the recognized text into the document. This is "
        "a larger, one-time extra download (about 4-5 GB) and can always be "
        "added later."
    ),
    "bootstrap.mode_standard_label": "Standard",
    "bootstrap.mode_standard_desc": "Without automatic voice message transcription. Smaller download.",
    "bootstrap.mode_stt_label": "Standard + voice message transcription",
    "bootstrap.mode_stt_desc": (
        "About 4-5 GB extra download. Runs on the CPU; if a supported "
        "NVIDIA GPU is present it is used automatically and speeds up "
        "transcription significantly."
    ),
    "bootstrap.install_title": "Installing…",
    "bootstrap.install_step_source": "Downloading application code…",
    "bootstrap.install_step_venv": "Creating dedicated Python environment…",
    "bootstrap.install_step_deps": "Installing dependencies ({name})…",
    "bootstrap.install_step_shortcut": "Creating application-menu entry…",
    "bootstrap.install_failed_title": "Installation failed",
    "bootstrap.install_failed": "An error occurred:\n\n{error}",
    "bootstrap.credentials_title": "Credentials (optional)",
    "bootstrap.credentials_intro": (
        "These steps can be skipped and completed later directly in the app."
    ),
    "bootstrap.credentials_telegram_label": "Telegram API credentials",
    "bootstrap.credentials_telegram_explain": (
        "Accessing Telegram requires an API ID and API hash, available for "
        "free at my.telegram.org. If this step is skipped, TME will ask for "
        "them automatically on the first run."
    ),
    "bootstrap.credentials_telegram_api_id_label": "API ID",
    "bootstrap.credentials_telegram_api_hash_label": "API hash",
    "bootstrap.credentials_telegram_phone_label": "Phone number (optional)",
    "bootstrap.credentials_open_signup_button": "Open page",
    "bootstrap.credentials_save_button": "Save",
    "bootstrap.credentials_skip_button": "Skip",
    "bootstrap.credentials_saved": "Saved.",
    "bootstrap.credentials_saved_plaintext_warning": (
        "Saved - but without an encrypted system keyring (no backend "
        "found), so as plain text in credentials.json."
    ),
    "bootstrap.credentials_provider_intro": "Optional: add API keys for external translation providers.",
    "bootstrap.credentials_provider_deepl": "DeepL",
    "bootstrap.credentials_provider_google": "Google Translate",
    "bootstrap.credentials_provider_openai": "ChatGPT / OpenAI",
    "bootstrap.credentials_explain_deepl": "DeepL API key (free and paid tiers available).",
    "bootstrap.credentials_explain_google": "Google Cloud Translation API key.",
    "bootstrap.credentials_explain_openai": "OpenAI API key (for ChatGPT translation).",
    "bootstrap.credentials_key_label": "API key for {provider}:",
    "bootstrap.credentials_continue_button": "Next",
    "bootstrap.credentials_skip_all_button": "Skip all",
    "bootstrap.finish_title": "Done",
    "bootstrap.finish_text": (
        "TME has been installed and can be found in the application/start "
        "menu. From there it can also be pinned to the taskbar/dock."
    ),
    "bootstrap.finish_launch_button": "Launch TME now",
    "bootstrap.finish_close_button": "Close",
}

CATALOGUES: dict[str, dict[str, str]] = {"de": DE, "en": EN}
