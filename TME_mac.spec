# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

project_root = Path(__file__).resolve().parent

# Collect data files
qss_dark = str(project_root / "ui" / "theme_dark.qss")
qss_light = str(project_root / "ui" / "theme_light.qss")
translations_glob = str(project_root / "ui" / "translations" / "app_*.qm")
icon_png = str(project_root / "temp_letter.png")

# Datas: (source, dest) where dest is relative in the bundle
_datas = []
if os.path.exists(qss_dark):
    _datas.append((qss_dark, "ui"))
if os.path.exists(qss_light):
    _datas.append((qss_light, "ui"))
# Translations (may be empty if not built)
for p in project_root.glob("ui/translations/app_*.qm"):
    _datas.append((str(p), "ui/translations"))

block_cipher = None


a = Analysis(
    ['ui/app.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='Telegram-ODT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    # Note: For platform-specific icons, set icon arg via CLI or adapt here.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Telegram-ODT'
)
