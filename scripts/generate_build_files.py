#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Iterable, Set, Dict, Tuple


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


def discover_local_packages(repo_root: Path) -> Set[str]:
    locals_: Set[str] = set()
    for p in repo_root.iterdir():
        if p.is_dir() and (p / "__init__.py").exists():
            locals_.add(p.name)
        elif p.is_file() and p.suffix == ".py":
            locals_.add(p.stem)
    return locals_


def build_requirements(repo_root: Path, exclude_dirs: Iterable[str]) -> Set[str]:
    local_pkgs = discover_local_packages(repo_root)
    found: Set[str] = set()

    for py in iter_py_files(repo_root, exclude_dirs):
        found |= parse_imports_from_file(py)

    third_party = {
        IMPORT_TO_PIP.get(m, m)
        for m in found
        if m not in local_pkgs and not is_stdlib_module(m)
    }

    return {p for p in third_party if p}


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

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--entry", default="ui/app.py")
    ap.add_argument("--name", default="TME")
    ap.add_argument("--exclude", default=".venv,venv,dist,build,__pycache__,.git,.pip-cache")
    args = ap.parse_args()

    repo_root = Path(args.repo).resolve()
    entry = (repo_root / args.entry).resolve()
    exclude_dirs = [e.strip() for e in args.exclude.split(",") if e.strip()]

    reqs = build_requirements(repo_root, exclude_dirs)

    (repo_root / "requirements.txt").write_text(
        "\n".join(sorted(reqs)) + "\n", encoding="utf-8"
    )

    spec = generate_spec(repo_root, entry, args.name)
    (repo_root / f"{args.name}.spec").write_text(spec, encoding="utf-8")

    print("Generated:")
    print(" - requirements.txt")
    print(f" - {args.name}.spec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
