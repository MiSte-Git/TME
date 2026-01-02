# -*- mode: python ; coding: utf-8 -*-

import glob
import os
from PyInstaller.utils.hooks import collect_submodules

qm_files = glob.glob(r"ui\translations\*.qm")
datas = [(f, r"ui\translations") for f in qm_files]

qss_files = glob.glob(r"ui\theme*.qss")
datas += [(f, r"ui") for f in qss_files]

# optional (nur wenn du das Icon/PNG zur Laufzeit lädst)
if os.path.exists("Telegram-LibreOffice.png"):
    datas += [("Telegram-LibreOffice.png", ".")]

a = Analysis(
    ['ui\\app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules('PySide6'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TME',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ui/assets/app.ico",
)
