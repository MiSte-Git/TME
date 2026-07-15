#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional, Set, Dict, Tuple


IMPORT_TO_PIP: Dict[str, str] = {
    "PySide6": "PySide6",
    "shiboken6": "shiboken6",
    "telethon": "telethon",
    "odf": "odfpy",
    "PIL": "Pillow",
    "pytesseract": "pytesseract",
    "easyocr": "easyocr",
    "cv2": "opencv-python",
    "numpy": "numpy",
    "torch": "torch",
    "torchvision": "torchvision",
    "yaml": "PyYAML",
    "requests": "requests",
    "dateutil": "python-dateutil",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "whisper": "openai-whisper",
    "lottie": "lottie",
}

IGNORE_MODULES: Set[str] = {
    "__future__",
    "typing",
    "typing_extensions",
    "dataclasses",
}


def is_stdlib_module(name: str) -> bool:
    stdlib = getattr(sys, "stdlib_module_names", None)
    return name in IGNORE_MODULES or (stdlib and name in stdlib)


def iter_py_files(repo_root: Path, exclude_dirs: Iterable[str]) -> Iterable[Path]:
    exclude = {d.lower() for d in exclude_dirs}
    for p in repo_root.rglob("*.py"):
        if any(ed in map(str.lower, p.parts) for ed in exclude):
            continue
        yield p


def parse_imports_from_file(py_file: Path) -> Set[str]:
    imports: Set[str] = set()
    try:
        src = py_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        src = py_file.read_text(encoding="latin-1", errors="ignore")

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.add(n.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                imports.add(node.module.split(".", 1)[0])

    return imports


def discover_local_packages(repo_root: Path, exclude_dirs: Iterable[str] = ()) -> Set[str]:
    """
    Top-level Verzeichnisse mit mindestens einer .py-Datei gelten als lokale
    Pakete (auch ohne __init__.py, siehe implizite Namespace-Packages), damit
    z. B. `from ui.foo import Bar` nicht als PyPI-Paket "ui" fehlinterpretiert wird.
    """
    exclude = {d.lower() for d in exclude_dirs}
    locals_: Set[str] = set()
    for p in repo_root.iterdir():
        if p.name.lower() in exclude or p.name.startswith("."):
            continue
        if p.is_dir():
            if next(p.rglob("*.py"), None) is not None:
                locals_.add(p.name)
        elif p.is_file() and p.suffix == ".py":
            locals_.add(p.stem)
    return locals_


def _installed_version(pip_name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(pip_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def build_requirements(repo_root: Path, exclude_dirs: Iterable[str]) -> Dict[str, Optional[str]]:
    """Gibt {pip_name: installierte_version_oder_None} zurück (kein direkter Datei-Write)."""
    local_pkgs = discover_local_packages(repo_root, exclude_dirs)
    found: Set[str] = set()

    for py in iter_py_files(repo_root, exclude_dirs):
        found |= parse_imports_from_file(py)

    third_party = {
        IMPORT_TO_PIP.get(m, m)
        for m in found
        if m not in local_pkgs and not is_stdlib_module(m)
    }

    return {p: _installed_version(p) for p in third_party if p}


def generate_spec(repo_root: Path, entry: Path, app_name: str) -> str:
    datas = []

    trans = repo_root / "ui" / "translations"
    if trans.exists():
        datas.append((str(trans / "*.qm"), "ui/translations"))

    assets = repo_root / "ui" / "assets"
    if assets.exists():
        datas.append((str(assets), "ui/assets"))

    datas_literal = "[" + ", ".join(f"({a!r}, {b!r})" for a, b in datas) + "]"

    entry_rel = entry.relative_to(repo_root).as_posix()
    repo_root_str = str(repo_root)

    return f"""# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    [{entry_rel!r}],
    pathex=[{repo_root_str!r}],
    binaries=[],
    datas={datas_literal},
    hiddenimports=collect_submodules("PySide6"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name={app_name!r},
    console=False,
)
"""

DESKTOP_ENTRY_NAME = "telegram-odt.desktop"


def _desktop_applications_dir() -> Path:
    return Path.home() / ".local" / "share" / "applications"


def _find_python_exec(repo_root: Path) -> str:
    venv_py = repo_root / ".venv" / "bin" / "python3"
    if venv_py.exists():
        return str(venv_py)
    found = shutil.which("python3")
    if found:
        return found
    return sys.executable


def _run_update_desktop_database() -> None:
    update_db = shutil.which("update-desktop-database")
    if not update_db:
        return
    subprocess.run([update_db, str(_desktop_applications_dir())], check=False)


def generate_desktop_entry(repo_root: Path, entry: Path) -> str:
    python_exec = _find_python_exec(repo_root)
    icon_path = repo_root / "Telegram-Nachrichten Herunterladen.png"

    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Telegram → ODT mit Emoji & Übersetzung
Comment=Erzeuge ODT aus Telegram-Schedules, inkl. Emoji-Handling und Übersetzung
Exec={python_exec} "{entry}"
Path={repo_root}
Icon={icon_path}
Terminal=false
Categories=Office;Utility;
TryExec={python_exec}
"""


def install_desktop_entry(repo_root: Path, entry: Path) -> Path:
    target_dir = _desktop_applications_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / DESKTOP_ENTRY_NAME

    content = generate_desktop_entry(repo_root, entry)
    target_path.write_text(content, encoding="utf-8")

    _run_update_desktop_database()
    return target_path


def uninstall_desktop_entry() -> bool:
    target_path = _desktop_applications_dir() / DESKTOP_ENTRY_NAME
    if not target_path.is_file():
        return False

    target_path.unlink()
    _run_update_desktop_database()
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--entry", default="ui/app.py")
    ap.add_argument("--name", default="TME")
    ap.add_argument("--exclude", default=".venv,venv,dist,build,__pycache__,.git,.pip-cache")
    ap.add_argument(
        "--uninstall-desktop",
        action="store_true",
        help="Entfernt eine zuvor installierte .desktop-Datei und beendet, ohne Spec/Requirements zu generieren.",
    )
    desktop_group = ap.add_mutually_exclusive_group()
    desktop_group.add_argument(
        "--with-desktop-entry",
        dest="desktop_entry",
        action="store_true",
        default=None,
        help="Erzeugt/installiert die .desktop-Datei (~/.local/share/applications), auch außerhalb von Linux.",
    )
    desktop_group.add_argument(
        "--no-desktop-entry",
        dest="desktop_entry",
        action="store_false",
        help="Überspringt die .desktop-Generierung, auch unter Linux.",
    )
    req_group = ap.add_mutually_exclusive_group()
    req_group.add_argument(
        "--with-requirements",
        dest="requirements",
        action="store_true",
        default=False,
        help="Generiert requirements.txt neu per Import-Scan (überschreibt manuelle Pins/Fixes!).",
    )
    req_group.add_argument(
        "--no-requirements",
        dest="requirements",
        action="store_false",
        help="Überspringt die requirements.txt-Generierung (Standard).",
    )
    args = ap.parse_args()

    repo_root = Path(args.repo).resolve()

    if args.uninstall_desktop:
        removed = uninstall_desktop_entry()
        target_path = _desktop_applications_dir() / DESKTOP_ENTRY_NAME
        if removed:
            print(f"Removed: {target_path}")
        else:
            print(f"Not found: {target_path}")
        return 0

    entry = (repo_root / args.entry).resolve()
    exclude_dirs = [e.strip() for e in args.exclude.split(",") if e.strip()]

    print("Generated:")

    if args.requirements:
        reqs = build_requirements(repo_root, exclude_dirs)
        lines = []
        for name in sorted(reqs):
            version = reqs[name]
            lines.append(f"{name}>={version}" if version else name)
            if not version:
                print(f"   WARNUNG: {name} nicht installiert, kein Versions-Pin ermittelbar", file=sys.stderr)

        (repo_root / "requirements.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        print(" - requirements.txt")
    else:
        print(" - requirements.txt (übersprungen, siehe --with-requirements)")

    spec = generate_spec(repo_root, entry, args.name)
    (repo_root / f"{args.name}.spec").write_text(spec, encoding="utf-8")
    print(f" - {args.name}.spec")

    want_desktop = args.desktop_entry
    if want_desktop is None:
        want_desktop = sys.platform.startswith("linux")

    if want_desktop:
        desktop_path = install_desktop_entry(repo_root, entry)
        print(f" - {desktop_path} (installiert)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
