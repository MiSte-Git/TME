"""Erzeugt die plattformspezifischen App-Icons aus Telegram-LibreOffice.png.

Ausgabe (alle aus derselben Quelle, damit Fenster, Menüeintrag, Installer
und Executable dasselbe Icon tragen):
- assets/icon.png   512x512 PNG  - Linux-.desktop-Eintrag (bootstrap/desktop_integration.py)
- assets/icon.ico   16-256 px    - Windows: Installer-Executable (build-bootstrap.yml), Startmenü-Verknüpfung
- assets/icon.icns  16-1024 px   - macOS: Installer-Executable (build-bootstrap.yml), .app-Bundle
- ui/assets/app.ico 16-256 px    - Windows-Vollbuild (scripts/build_win.ps1 --icon)

Hinweis: Telegram-LibreOffice.png ist trotz Endung ein WebP-Bild; Pillow
liest es unabhängig von der Endung korrekt. Benötigt nur Pillow.

    python tools/build_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Telegram-LibreOffice.png"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    src = Image.open(SOURCE).convert("RGBA")
    if src.width != src.height:
        side = min(src.size)
        left = (src.width - side) // 2
        top = (src.height - side) // 2
        src = src.crop((left, top, left + side, top + side))
    big = src.resize((1024, 1024), Image.Resampling.LANCZOS)

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    big.resize((512, 512), Image.Resampling.LANCZOS).save(assets / "icon.png", optimize=True)
    big.save(assets / "icon.ico", sizes=ICO_SIZES)
    big.save(assets / "icon.icns")
    big.save(ROOT / "ui" / "assets" / "app.ico", sizes=ICO_SIZES)
    for p in (assets / "icon.png", assets / "icon.ico", assets / "icon.icns", ROOT / "ui" / "assets" / "app.ico"):
        print(f"{p.relative_to(ROOT)}: {p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
