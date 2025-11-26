from __future__ import annotations

from pathlib import Path
from typing import Optional
import os


class SpeechToTextError(Exception):
    """Eigener Fehler für STT-Probleme."""


def get_torch_device() -> str:
    """Wählt das STT-Device: standardmäßig CPU, optional per Env-Var STT_DEVICE auf CUDA schaltbar."""
    forced = os.getenv("STT_DEVICE")
    if forced is None or not forced.strip():
        print("STT: Kein STT_DEVICE gesetzt, verwende CPU.")
        return "cpu"

    forced_norm = forced.strip().lower()
    if forced_norm not in {"cpu", "cuda", "cuda:0"}:
        print(f"STT: Unbekanntes STT_DEVICE '{forced}', verwende CPU.")
        return "cpu"

    if forced_norm == "cpu":
        return "cpu"

    try:
        import torch  # type: ignore[import]
    except Exception as exc:
        print(f"STT: Torch nicht verfügbar ({exc}), verwende CPU.")
        return "cpu"

    try:
        if not torch.cuda.is_available():
            print("STT: CUDA nicht verfügbar, verwende CPU.")
            return "cpu"
    except Exception as exc:
        print(f"STT: CUDA-Check fehlgeschlagen ({exc}), verwende CPU.")
        return "cpu"

    try:
        dev = torch.device("cuda")
        torch.tensor([0], device=dev)  # minimaler Probe-Call
        return "cuda"
    except Exception as exc:
        print(f"STT: CUDA nicht nutzbar ({exc}), falle auf CPU zurück.")
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        return "cpu"


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

        device = get_torch_device()
        model = whisper.load_model(model_size, device=device)
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


if __name__ == "__main__":
    print("STT device:", get_torch_device())
