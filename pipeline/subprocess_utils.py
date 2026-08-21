"""
Kleiner Helfer für subprocess-Aufrufe externer Kommandozeilen-Tools
(ffmpeg/ffprobe in frame_compositing.py, LibreOffice/Pandoc in
docx_convert.py).

Hintergrund (siehe TME-Backlog.md, Punkt 8): Unter Windows öffnet ein per
subprocess.run()/Popen() gestartetes Konsolen-Tool ohne besondere Maßnahmen
kurz ein sichtbares Cmd-Fenster - bei mehreren Aufrufen hintereinander (z.B.
mehrere gesampelte Frames pro Custom-Emoji, oder die DOCX-Konvertierung nach
jedem Lauf) sichtbares "Fenster-Poppen"/Flackern. subprocess.CREATE_NO_WINDOW
unterdrückt das zuverlässig; auf Linux/macOS existiert das Flag nicht (und
ist dort auch nicht nötig - dort läuft ohnehin kein separates Konsolenfenster
auf).
"""
from __future__ import annotations

import subprocess
import sys

# Nur unter Windows gesetzt - subprocess.CREATE_NO_WINDOW existiert auf
# anderen Plattformen nicht als Attribut.
NO_WINDOW_KWARGS: dict = {}
if sys.platform.startswith("win"):
    NO_WINDOW_KWARGS["creationflags"] = subprocess.CREATE_NO_WINDOW


def run_hidden(cmd, **kwargs) -> subprocess.CompletedProcess:
    """Wie subprocess.run(cmd, **kwargs), unterdrückt aber unter Windows das
    kurz aufpoppende Konsolenfenster des gestarteten Tools (siehe
    NO_WINDOW_KWARGS). Ein explizit übergebenes 'creationflags' in kwargs
    hat Vorrang (wird nicht überschrieben)."""
    merged = {**NO_WINDOW_KWARGS, **kwargs}
    return subprocess.run(cmd, **merged)
