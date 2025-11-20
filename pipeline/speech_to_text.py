from __future__ import annotations

from pathlib import Path
from typing import Optional


class SpeechToTextError(Exception):
    """Eigener Fehler für STT-Probleme."""


def transcribe_voice(
    audio_path: Path,
    language: str = "de",
    model_size: str = "small",
) -> Optional[str]:
    """Transkribiert eine Sprachnachricht mit OpenAI Whisper.

    Erwartet eine lokale Audiodatei (z.B. OGG/OPUS) und gibt den erkannten
    Text als String zurück. Bei Fehlern wird None geliefert.

    Hinweis: Benötigt zusätzlich ffmpeg im System und das Python-Paket
    "openai-whisper" (oder kompatibel):

        pip install openai-whisper
    """
    try:
        import whisper  # type: ignore[import]
    except Exception as exc:  # pragma: no cover - reine Laufzeitabhängigkeit
        raise SpeechToTextError(
            "Whisper-Modell konnte nicht geladen werden. "
            "Stelle sicher, dass das Paket 'openai-whisper' installiert ist."
        ) from exc

    try:
        if not audio_path.is_file():
            raise SpeechToTextError(f"Audiodatei nicht gefunden: {audio_path}")

        model = whisper.load_model(model_size)
        result = model.transcribe(str(audio_path), language=language)
        text = (result or {}).get("text", "")
        text = text.strip()
        if not text:
            return None
        return text
    except SpeechToTextError:
        raise
    except Exception as exc:  # pragma: no cover - Schutz vor Laufzeitfehlern
        raise SpeechToTextError(f"Fehler bei der Transkription von {audio_path}: {exc}") from exc
