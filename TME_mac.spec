# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

project_root = Path(__file__).resolve().parent

# Laufzeit-Ressourcen, die ui/app.py per Path(__file__).parent bzw. relativem
# Pfad lädt und die PyInstaller nicht automatisch erkennt (keine Python-
# Imports): Theme-QSS (inkl. des darin per relativem url() referenzierten
# checkbox-check.svg), Qt-Übersetzungen und das Fenster-Icon.
qss_dark = str(project_root / "ui" / "theme_dark.qss")
qss_light = str(project_root / "ui" / "theme_light.qss")
checkbox_svg = str(project_root / "ui" / "checkbox-check.svg")
window_icon = str(project_root / "Telegram-LibreOffice.png")

_datas = []
if os.path.exists(qss_dark):
    _datas.append((qss_dark, "ui"))
if os.path.exists(qss_light):
    _datas.append((qss_light, "ui"))
if os.path.exists(checkbox_svg):
    _datas.append((checkbox_svg, "ui"))
if os.path.exists(window_icon):
    _datas.append((window_icon, "."))
# Übersetzungen (kann leer sein, falls noch nicht gebaut)
for p in project_root.glob("ui/translations/app_*.qm"):
    _datas.append((str(p), "ui/translations"))

block_cipher = None

# keyring waehlt sein Backend zur Laufzeit dynamisch ueber importlib.metadata-
# Entry-Points statt normaler import-Statements - PyInstallers statische
# Analyse erkennt das nicht automatisch, daher explizite Hidden-Imports
# (analog zu scripts/build_win.ps1). Alle vier Backend-Module faengen ihre
# plattformspezifischen Imports selbst per try/except ab, daher unbedenklich
# als Hidden-Import unabhaengig von der Build-Plattform.
_keyring_hidden_imports = [
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.chainer",
    "keyring.backends.fail",
]

a = Analysis(
    ['ui/app.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=_datas,
    hiddenimports=_keyring_hidden_imports,
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
    name='TME',
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
    name='TME'
)
