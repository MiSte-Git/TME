#!/usr/bin/env bash
# Produktiv-Build unter Linux: baut ein eigenstaendiges Binary aus ui/app.py
# via PyInstaller, in einer eigenen Build-venv (.venv-build/, getrennt von der
# Dev-venv aus run_linux_dev.sh, damit keine Dev-Only-Pakete ins Bundle
# gelangen). Analog zu scripts/build_win.ps1 (--onedir Standard, --onefile
# bei --release, gleiche --add-data-/Hidden-Import-Liste, soweit unter Linux
# sinnvoll uebertragbar).
#
# Nutzung:
#   ./build_linux.sh                 # --onedir Testbuild, PyInstaller-Cache bleibt erhalten
#   ./build_linux.sh --release       # --onefile + --clean, fuer Weitergabe an Endnutzer
#   ./build_linux.sh --clean         # erzwingt --clean auch fuer --onedir
#   ./build_linux.sh --with-stt      # installiert zusaetzlich requirements-stt.txt vor dem Build
#                                     # (Standard: STT NICHT im Bundle, siehe docs/DEPLOY.md - ~4-5 GB)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

RELEASE=0
FORCE_CLEAN=0
WITH_STT=0
for arg in "$@"; do
  case "$arg" in
    --release) RELEASE=1 ;;
    --clean) FORCE_CLEAN=1 ;;
    --with-stt) WITH_STT=1 ;;
    *) echo "Unbekannte Option: $arg (bekannt: --release, --clean, --with-stt)" >&2; exit 1 ;;
  esac
done

BUILD_VENV_DIR="$REPO_ROOT/.venv-build"
BUILD_PY="$BUILD_VENV_DIR/bin/python3"

if [ ! -x "$BUILD_PY" ]; then
  echo "Lege separate Build-venv an: $BUILD_VENV_DIR"
  python3 -m venv "$BUILD_VENV_DIR"
fi

echo "Using build venv: $BUILD_PY"
"$BUILD_PY" -m pip install --upgrade pip --quiet

echo "Installiere Abhaengigkeiten aus requirements.txt..."
"$BUILD_PY" -m pip install -r "$REPO_ROOT/requirements.txt" --quiet

if [ "$WITH_STT" -eq 1 ]; then
  echo "Installiere requirements-stt.txt (--with-stt gesetzt, ~4-5 GB inkl. CUDA-Paketen)..."
  "$BUILD_PY" -m pip install -r "$REPO_ROOT/requirements-stt.txt" --quiet
fi

echo "Installiere PyInstaller..."
"$BUILD_PY" -m pip install --quiet pyinstaller

# Optional: Uebersetzungen bauen (analog build_win.ps1, dort ueber
# TME_BUILD_TRANSLATIONS gesteuert - unter Linux ist bash immer vorhanden,
# daher hier einfach direkt versucht, Fehler sind nicht fatal).
TRANS_BUILD="$REPO_ROOT/ui/translations/build_qm.sh"
if [ -x "$TRANS_BUILD" ]; then
  echo "Baue Uebersetzungen..."
  (cd "$REPO_ROOT/ui/translations" && ./build_qm.sh) || echo "Warnung: Uebersetzungs-Build fehlgeschlagen, fahre fort."
fi

# Syntax-Check - .venv/.venv-build/build/dist ausschliessen (siehe README.md/
# CONTRIBUTING.md-Hinweis zu diesem Filter: sonst werden Fremdpakete wie
# PySide6-Jinja2-Templates faelschlich als ungueltiges Python gewertet).
echo "Running syntax check (compileall)..."
"$BUILD_PY" -m compileall -q . -x "[\\/](\.venv|\.venv-build|build|dist)[\\/]"

ENTRY="$REPO_ROOT/ui/app.py"
if [ ! -f "$ENTRY" ]; then
  echo "FEHLER: Einstiegspunkt nicht gefunden: $ENTRY" >&2
  exit 1
fi

if [ "$RELEASE" -eq 1 ]; then
  MODE_ARGS=(--onefile)
  USE_CLEAN=1
  MODE_LABEL="onefile (Release-Distribution)"
else
  MODE_ARGS=(--onedir)
  USE_CLEAN=$FORCE_CLEAN
  MODE_LABEL="onedir (schneller lokaler Test-Build)"
fi
CLEAN_ARGS=()
if [ "$USE_CLEAN" -eq 1 ]; then
  CLEAN_ARGS=(--clean)
fi

echo "Build-Modus: $MODE_LABEL"
if [ "$USE_CLEAN" -eq 1 ]; then
  echo "PyInstaller-Cache: wird verworfen (--clean)"
else
  echo "PyInstaller-Cache: wird wiederverwendet"
fi

# keyring waehlt sein Backend zur Laufzeit dynamisch ueber importlib.metadata-
# Entry-Points statt normaler import-Statements - PyInstallers statische
# Analyse erkennt das nicht automatisch, daher explizite Hidden-Imports
# (identisch zu scripts/build_win.ps1/TME_mac.spec - alle vier Backend-Module
# fangen ihre plattformspezifischen Imports selbst per try/except ab, daher
# unbedenklich unabhaengig von der Build-Plattform).
KEYRING_HIDDEN=(
  --hidden-import keyring.backends.Windows
  --hidden-import keyring.backends.macOS
  --hidden-import keyring.backends.SecretService
  --hidden-import keyring.backends.kwallet
  --hidden-import keyring.backends.chainer
  --hidden-import keyring.backends.fail
)

# Laufzeit-Ressourcen, die ui/app.py per Path(__file__).parent bzw. relativem
# Pfad laedt und die PyInstaller nicht automatisch erkennt (keine Python-
# Imports): Theme-QSS (inkl. des darin per relativem url() referenzierten
# checkbox-check.svg), Qt-Uebersetzungen und das Fenster-Icon. Identische
# Liste wie scripts/build_win.ps1, nur mit POSIX-Pfadsyntax (":" statt ";"
# als Trenner zwischen Quelle/Ziel bei --add-data).
DATA_ARGS=()
[ -f "$REPO_ROOT/ui/theme_dark.qss" ] && DATA_ARGS+=(--add-data "$REPO_ROOT/ui/theme_dark.qss:ui")
[ -f "$REPO_ROOT/ui/theme_light.qss" ] && DATA_ARGS+=(--add-data "$REPO_ROOT/ui/theme_light.qss:ui")
[ -f "$REPO_ROOT/ui/checkbox-check.svg" ] && DATA_ARGS+=(--add-data "$REPO_ROOT/ui/checkbox-check.svg:ui")
[ -f "$REPO_ROOT/Telegram-LibreOffice.png" ] && DATA_ARGS+=(--add-data "$REPO_ROOT/Telegram-LibreOffice.png:.")
if [ -d "$REPO_ROOT/ui/translations" ]; then
  while IFS= read -r -d '' qm; do
    DATA_ARGS+=(--add-data "$qm:ui/translations")
  done < <(find "$REPO_ROOT/ui/translations" -maxdepth 1 -name "app_*.qm" -print0)
fi
if [ -d "$REPO_ROOT/ui/assets/flags" ]; then
  while IFS= read -r -d '' png; do
    DATA_ARGS+=(--add-data "$png:ui/assets/flags")
  done < <(find "$REPO_ROOT/ui/assets/flags" -maxdepth 1 -name "*.png" -print0)
fi

echo "Building binary via PyInstaller..."
"$BUILD_PY" -m PyInstaller --noconfirm "${CLEAN_ARGS[@]}" "${MODE_ARGS[@]}" \
  --windowed --name TME "${KEYRING_HIDDEN[@]}" "${DATA_ARGS[@]}" "$ENTRY"

DIST_DIR="$REPO_ROOT/dist"
if [ ! -d "$DIST_DIR" ]; then
  echo "FEHLER: dist/ nicht gefunden - Build vermutlich fehlgeschlagen." >&2
  exit 1
fi

BIN="$(find "$DIST_DIR" -maxdepth 2 -name "TME" -type f | head -1)"
if [ -z "$BIN" ]; then
  echo "FEHLER: Build abgeschlossen, aber kein TME-Binary unter dist/ gefunden." >&2
  exit 1
fi
echo "Built binary: $BIN"
