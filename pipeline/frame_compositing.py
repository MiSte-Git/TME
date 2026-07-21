"""
Gemeinsames Frame-Compositing fuer animierte Custom-Emojis (.tgs/.webm).

Ein einzelner Frame (bisher: Frame 0 / Zeitpunkt 0) reicht bei vielen
animierten Telegram-Custom-Emojis nicht aus: Layer, die per Fade-in,
verzoegertem In-Point oder Trim-Path-Reveal erst spaeter in der Animation
sichtbar werden (typisch bei "geschriebenen" Buchstaben-Sets), fehlen dann
komplett im exportierten PNG. Diese Funktionen rendern stattdessen mehrere
ueber die Animationsdauer verteilte Frames und legen sie per Alpha-
Compositing uebereinander, sodass alles, was zu irgendeinem Zeitpunkt
sichtbar war, im Ergebnis-PNG erhalten bleibt.

Genutzt von pipeline/assets.py (ensure_custom_emoji_pngs) und
pipeline/extract_ce.py (ensure_pngs_for_doc_ids).
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

# Anzahl der ueber die Animationsdauer verteilten Sample-Frames. 8-12 ist ein
# guter Kompromiss zwischen Trefferquote bei kurzen Fade-in-Fenstern (siehe
# Analyse: betroffene Layer sind oft nur ueber ~10-15% der Composition-Laenge
# sichtbar) und Laufzeit/Anzahl der lottie_convert.py-/ffmpeg-Subprozesse.
DEFAULT_FRAME_SAMPLES = 10


def sample_indices(start: int, end: int, count: int = DEFAULT_FRAME_SAMPLES) -> list[int]:
    """Liefert bis zu `count` gleichmaessig verteilte, aufsteigend sortierte,
    eindeutige Ganzzahl-Indizes zwischen start und end (beide inklusive).

    Bei count<=1 oder end<=start wird nur [start] zurueckgegeben (Fallback
    auf Einzelframe-Verhalten).
    """
    if count <= 1 or end <= start:
        return [start]
    span = end - start
    indices = {start + round(span * i / (count - 1)) for i in range(count)}
    return sorted(indices)


def sample_timestamps(duration_s: float, count: int = DEFAULT_FRAME_SAMPLES) -> list[float]:
    """Wie sample_indices, aber als Sekunden-Zeitstempel innerhalb [0, duration_s)
    fuer die Frame-Extraktion aus Videodateien (.webm)."""
    if count <= 1 or duration_s <= 0:
        return [0.0]
    # Letzten Zeitstempel knapp vor dem Ende ansetzen, damit ein Seek auf
    # exakt die Dauer nicht ins Leere/EOF laeuft.
    end = max(duration_s - (duration_s / max(count * 4, 1)), 0.0)
    step = end / (count - 1) if count > 1 else 0.0
    return [round(step * i, 3) for i in range(count)]


def composite_pngs(frame_paths: Sequence[Path], out_png: Path) -> bool:
    """Legt mehrere RGBA-PNG-Frames alpha-compositet uebereinander (in der
    gegebenen Reihenfolge) und schreibt das Ergebnis nach out_png.

    Frames, die nicht existieren oder nicht geladen werden koennen, werden
    uebersprungen. Kein vorzeitiges Flatten auf RGB - Transparenz bleibt bis
    zum finalen Speichern erhalten.

    Gibt True zurueck, wenn mindestens ein Frame verarbeitet wurde.
    """
    from PIL import Image

    composed: "Image.Image | None" = None
    for p in frame_paths:
        if not p.exists():
            continue
        try:
            with Image.open(p) as im:
                frame = im.convert("RGBA")
                if composed is None:
                    composed = frame.copy()
                else:
                    composed = Image.alpha_composite(composed, frame)
        except Exception:
            continue

    if composed is None:
        return False

    out_png.parent.mkdir(parents=True, exist_ok=True)
    composed.save(out_png, "PNG")
    return True


def render_tgs_multiframe(
    raw_path: Path,
    out_png: Path,
    frame_samples: int = DEFAULT_FRAME_SAMPLES,
    size: "int | None" = None,
) -> bool:
    """Rendert mehrere ueber die Composition-Laenge verteilte Frames aus einer
    gzip-komprimierten TGS/Lottie-Datei (unabhaengig von deren tatsaechlicher
    Dateiendung - wird hier immer explizit entpackt, siehe Hinweis unten) via
    lottie_convert.py und komponiert sie per composite_pngs() zu out_png.

    Hinweis: lottie_convert.py waehlt den Importer anhand der Dateiendung -
    eine z.B. als "<id>.bin" zwischengespeicherte Rohdatei wuerde mit
    "Unknown importer" fehlschlagen. Deshalb wird hier immer zuerst nach
    .json entpackt, bevor lottie_convert.py aufgerufen wird.

    Gibt True zurueck, wenn mindestens ein Frame erfolgreich gerendert und
    out_png geschrieben wurde (sonst False, z.B. wenn lottie_convert.py nicht
    installiert ist oder die Datei kein gueltiges gzip/Lottie-JSON ist).
    """
    import gzip
    import json
    import shutil
    import subprocess
    import sys
    import tempfile

    lc = shutil.which("lottie_convert.py")
    if not lc:
        return False

    with tempfile.TemporaryDirectory(prefix="tgs_render_") as tmpdir:
        tmpdir_p = Path(tmpdir)
        json_path = tmpdir_p / "anim.json"
        try:
            with gzip.open(raw_path, "rb") as gz, open(json_path, "wb") as jf:
                jf.write(gz.read())
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return False

        ip = int(data.get("ip", 0) or 0)
        op = int(data.get("op", 0) or 0)
        last_frame = max(op - 1, ip)
        indices = sample_indices(ip, last_frame, frame_samples)

        frame_files: list[Path] = []
        for idx in indices:
            frame_png = tmpdir_p / f"frame_{idx}.png"
            # lottie_convert.py explizit ueber sys.executable starten statt
            # direkt auszufuehren: dessen Shebang-Zeile bricht, wenn der
            # venv-Pfad ein Leerzeichen enthaelt (haeufig bei diesem Projekt).
            cmd = [sys.executable, lc, str(json_path), str(frame_png), "--frame", str(idx)]
            if size:
                cmd += ["--width", str(size), "--height", str(size)]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                continue
            if frame_png.exists():
                frame_files.append(frame_png)

        return composite_pngs(frame_files, out_png)


def render_webm_multiframe(
    raw_path: Path,
    out_png: Path,
    frame_samples: int = DEFAULT_FRAME_SAMPLES,
) -> bool:
    """Extrahiert mehrere ueber die Laufzeit verteilte Frames aus einer
    Videodatei (.webm) via ffmpeg/ffprobe-Seeks und komponiert sie per
    composite_pngs() zu out_png.

    Gibt True zurueck, wenn mindestens ein Frame erfolgreich extrahiert und
    out_png geschrieben wurde (sonst False, z.B. wenn ffmpeg nicht installiert
    ist). Ist ffprobe nicht verfuegbar oder liefert keine Dauer, wird auf ein
    einzelnes Frame bei Zeitstempel 0 zurueckgefallen (bisheriges Verhalten).
    """
    import shutil
    import subprocess
    import tempfile

    ff = shutil.which("ffmpeg")
    if not ff:
        return False

    duration = 0.0
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            proc = subprocess.run(
                [
                    ffprobe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(raw_path),
                ],
                capture_output=True, text=True, check=True,
            )
            duration = float(proc.stdout.strip())
        except Exception:
            duration = 0.0

    timestamps = sample_timestamps(duration, frame_samples) if duration > 0 else [0.0]

    with tempfile.TemporaryDirectory(prefix="webm_render_") as tmpdir:
        tmpdir_p = Path(tmpdir)
        frame_files: list[Path] = []
        for i, ts in enumerate(timestamps):
            frame_png = tmpdir_p / f"frame_{i}.png"
            cmd = [
                ff, "-y", "-ss", str(ts), "-i", str(raw_path),
                "-frames:v", "1", "-pix_fmt", "rgba", str(frame_png),
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                continue
            if frame_png.exists():
                frame_files.append(frame_png)

        return composite_pngs(frame_files, out_png)
