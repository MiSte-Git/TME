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

import json
import sys
from pathlib import Path
from typing import Sequence

# Anzahl der ueber die Animationsdauer verteilten Sample-Frames. 8-12 ist ein
# guter Kompromiss zwischen Trefferquote bei kurzen Fade-in-Fenstern (siehe
# Analyse: betroffene Layer sind oft nur ueber ~10-15% der Composition-Laenge
# sichtbar) und Laufzeit/Anzahl der lottie_convert.py-/ffmpeg-Subprozesse.
DEFAULT_FRAME_SAMPLES = 10

# Version des Render-Verfahrens fuer .tgs/.webm-Custom-Emojis. Wird nach
# jedem erfolgreichen Multi-Frame-Render in der Cache-Metadatendatei
# (RENDER_META_FILENAME) pro doc_id vermerkt. Bei einer kuenftigen Aenderung
# an render_tgs_multiframe()/render_webm_multiframe() (z.B. andere
# Frame-Anzahl oder Compositing-Logik) muss diese Konstante erhoeht werden -
# damit erkennt scripts/rescan_emoji_cache.py (und jede zukuenftige Variante
# davon) bereits vorhandene Cache-Eintraege zuverlaessig als veraltet, ohne
# auf die Bild-Heuristik angewiesen zu sein. Version 1 = alte
# Einzelframe-Erzeugung vor Commit 61163aa (nie explizit vermerkt - Eintraege
# ohne Metadaten gelten daher automatisch als "Version < 2", siehe
# get_render_version()).
RENDERER_VERSION = 2

RENDER_META_FILENAME = "_render_meta.json"


def _meta_path(cache_dir: Path) -> Path:
    return cache_dir / RENDER_META_FILENAME


def load_render_meta(cache_dir: Path) -> dict:
    """Laedt die Cache-Metadaten (doc_id -> Renderer-Version) aus
    cache_dir/_render_meta.json. Liefert {} falls die Datei fehlt oder
    beschaedigt ist."""
    p = _meta_path(cache_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_render_version(cache_dir: Path, doc_id) -> "int | None":
    """Liefert die vermerkte Renderer-Version fuer doc_id, oder None, wenn
    kein Eintrag existiert (z.B. Alt-Cache von vor Einfuehrung dieser
    Metadatendatei, oder Format ohne Versionsrelevanz wie webp/png)."""
    return load_render_meta(cache_dir).get(str(doc_id))


def mark_rendered(cache_dir: Path, doc_id, version: int = RENDERER_VERSION) -> None:
    """Vermerkt, dass doc_id erfolgreich mit der angegebenen Renderer-Version
    erzeugt wurde. Best-effort - Fehler beim Schreiben werden verschluckt,
    analog zum restlichen Cache-Verhalten in assets.py/extract_ce.py."""
    meta = load_render_meta(cache_dir)
    meta[str(doc_id)] = version
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _meta_path(cache_dir).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception:
        pass


def looks_blank(
    png_path: Path,
    alpha_threshold: int = 16,
    min_visible_ratio: float = 0.02,
) -> bool:
    """Heuristik: True, wenn ein bereits gerendertes PNG wahrscheinlich (fast)
    leer ist - typisches Symptom des alten Frame-0-Verfahrens bei Emojis, bei
    denen zum Render-Zeitpunkt (Frame 0) noch kein Layer sichtbar war (z.B.
    Layer mit ip > 0 oder Opacity-Keyframe s=0 bei t=0).

    Zaehlt den Anteil "sichtbarer" Pixel (Alpha > alpha_threshold) an der
    Gesamtflaeche. Liegt dieser unter min_visible_ratio, gilt das Bild als
    (fast) leer.

    Wichtige Einschraenkung (siehe docs/DEPLOY.md): Erkennt zuverlaessig nur
    Faelle, in denen bei Frame 0 nichts oder fast nichts sichtbar war. Faelle,
    in denen Frame 0 bereits ein vollstaendiges, nicht-leeres Bild zeigt und
    nur zusaetzliche Elemente fehlen (z.B. ein Layer, der einer bereits
    sichtbaren Szene erst spaeter etwas hinzufuegt), werden NICHT erkannt -
    dafuer muesste die Quelldatei erneut analysiert werden (Ansatz c), die
    fuer bereits im Cache liegende Eintraege i.d.R. nicht mehr vorliegt.
    """
    from PIL import Image

    try:
        with Image.open(png_path) as im:
            im = im.convert("RGBA")
            alpha = im.getchannel("A")
            total = im.width * im.height
            if total == 0:
                return True
            hist = alpha.histogram()
            visible = sum(hist[alpha_threshold:])
            return (visible / total) < min_visible_ratio
    except Exception:
        return False


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


def _find_bundled_tool(name: str) -> "str | None":
    """Sucht ein externes Kommandozeilen-Tool (ffmpeg/ffprobe) zuerst auf
    PATH (Dev-Umgebung/System-Installation), dann im PyInstaller-Bundle-
    Verzeichnis selbst (sys._MEIPASS bei --onefile, sonst das Verzeichnis
    der ausfuehrbaren Datei bei --onedir).

    Hintergrund (TME-Backlog.md, Punkt 1): shutil.which() allein findet nur
    Tools, die auf PATH liegen - ein gebuendeltes ffmpeg (siehe --add-binary
    in scripts/build_win.ps1, build_linux.sh, TME_mac.spec) landet im
    Bundle-Root, nicht auf PATH. Wird ffmpeg auf dem Build-Rechner nicht
    gefunden, buendeln diese Skripte es gar nicht erst mit - dann bleibt
    diese Funktion hier ohne Fund (kein Hard-Fail, siehe render_webm_multiframe).
    """
    import shutil

    found = shutil.which(name)
    if found:
        return found

    bundle_dir = getattr(sys, "_MEIPASS", None)
    if not bundle_dir and getattr(sys, "frozen", False):
        bundle_dir = str(Path(sys.executable).parent)
    if bundle_dir:
        exe_name = f"{name}.exe" if sys.platform.startswith("win") else name
        candidate = Path(bundle_dir) / exe_name
        if candidate.exists():
            return str(candidate)
    return None


def render_tgs_multiframe(
    raw_path: Path,
    out_png: Path,
    frame_samples: int = DEFAULT_FRAME_SAMPLES,
    size: "int | None" = None,
) -> bool:
    """Rendert mehrere ueber die Composition-Laenge verteilte Frames aus einer
    gzip-komprimierten TGS/Lottie-Datei ueber die lottie-Python-Bibliothek
    (direkter In-Prozess-Aufruf, nicht mehr per Subprocess) und komponiert
    sie per composite_pngs() zu out_png.

    Hinweis (TME-Backlog.md, Punkt 1): Vor diesem Fix wurde hierfuer das
    externe Skript lottie_convert.py per subprocess ueber sys.executable
    aufgerufen. In einem PyInstaller-gefrorenen Build zeigt sys.executable
    aber auf die gefrorene TME.exe selbst, nicht auf einen echten
    Python-Interpreter - lottie_convert.py liesse sich darueber gar nicht
    ausfuehren, selbst wenn die Datei mitgebuendelt wuerde (das eigentliche
    Ziel des ersten Fix-Versuchs). Der direkte Bibliotheksaufruf umgeht das
    Problem vollstaendig: PyInstaller buendelt die lottie-Bibliothek
    automatisch, weil sie hier direkt importiert wird - kein manuelles
    --add-data/--add-binary noetig (anders als ffmpeg fuer den WEBM-Pfad,
    siehe render_webm_multiframe/_find_bundled_tool).

    Benoetigt zusaetzlich zu 'lottie' auch 'cairosvg' als PNG-Render-Backend
    von lottie (siehe requirements.txt) - ohne cairosvg ist
    lottie.exporters.cairo.export_png gar nicht definiert und der Import
    schlaegt fehl (siehe except ImportError unten). ACHTUNG: cairosvg haengt
    nativ von libcairo ab - ob PyInstaller das auf Windows zuverlaessig mit-
    buendelt, ist bisher NICHT durch einen echten Build/Install/Export-
    Zyklus verifiziert (siehe Projekt-Prinzip "nur echte Laeufe zaehlen" in
    TME-Backlog.md) und sollte als erster Schritt nach diesem Fix geprueft
    werden.

    Gibt True zurueck, wenn mindestens ein Frame erfolgreich gerendert und
    out_png geschrieben wurde (sonst False, z.B. wenn lottie/cairosvg nicht
    installiert sind oder die Datei kein gueltiges gzip/Lottie-JSON ist).
    """
    import tempfile

    try:
        from lottie.parsers.tgs import parse_tgs
        from lottie.exporters.cairo import export_png
    except ImportError:
        return False

    try:
        animation = parse_tgs(str(raw_path))
    except Exception:
        return False

    if size:
        try:
            animation.scale(size, size)
        except Exception:
            pass

    ip = int(animation.in_point or 0)
    op = int(animation.out_point or 0)
    last_frame = max(op - 1, ip)
    indices = sample_indices(ip, last_frame, frame_samples)

    with tempfile.TemporaryDirectory(prefix="tgs_render_") as tmpdir:
        tmpdir_p = Path(tmpdir)
        frame_files: list[Path] = []
        for idx in indices:
            frame_png = tmpdir_p / f"frame_{idx}.png"
            try:
                export_png(animation, str(frame_png), frame=idx)
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

    ffmpeg/ffprobe werden ueber _find_bundled_tool() gesucht (PATH zuerst,
    dann das PyInstaller-Bundle-Verzeichnis, siehe --add-binary in
    scripts/build_win.ps1/build_linux.sh/TME_mac.spec). Die Aufrufe selbst
    laufen ueber subprocess_utils.run_hidden(), das unter Windows das
    andernfalls kurz aufpoppende Konsolenfenster unterdrueckt (TME-Backlog.md
    Punkt 8).

    Gibt True zurueck, wenn mindestens ein Frame erfolgreich extrahiert und
    out_png geschrieben wurde (sonst False, z.B. wenn ffmpeg nicht installiert
    ist). Ist ffprobe nicht verfuegbar oder liefert keine Dauer, wird auf ein
    einzelnes Frame bei Zeitstempel 0 zurueckgefallen (bisheriges Verhalten).
    """
    import subprocess
    import tempfile

    from .subprocess_utils import run_hidden

    ff = _find_bundled_tool("ffmpeg")
    if not ff:
        return False

    duration = 0.0
    ffprobe = _find_bundled_tool("ffprobe")
    if ffprobe:
        try:
            proc = run_hidden(
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
                run_hidden(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                continue
            if frame_png.exists():
                frame_files.append(frame_png)

        return composite_pngs(frame_files, out_png)
