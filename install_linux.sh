#!/usr/bin/env bash
# Installiert das mit build_linux.sh gebaute Binary lokal (ohne root-Rechte,
# unter ~/.local/share/tme/) und richtet/aktualisiert den Desktop-Eintrag so
# ein, dass er auf das eigenstaendige Binary zeigt statt auf einen
# venv-Python-Aufruf von ui/app.py (siehe scripts/generate_build_files.py
# --binary-path).
#
# Nutzung:
#   ./install_linux.sh              # baut bei Bedarf (falls dist/ fehlt) und installiert
#   ./install_linux.sh --release    # erzwingt einen --release-Build (--onefile) davor
#   ./install_linux.sh --with-stt   # bei Neu-Build: STT-Abhaengigkeiten mit einschliessen
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

BUILD_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --release) BUILD_ARGS+=(--release) ;;
    --with-stt) BUILD_ARGS+=(--with-stt) ;;
    *) echo "Unbekannte Option: $arg (bekannt: --release, --with-stt)" >&2; exit 1 ;;
  esac
done

INSTALL_DIR="$HOME/.local/share/tme"

find_binary() {
  if [ -d "$REPO_ROOT/dist" ]; then
    find "$REPO_ROOT/dist" -maxdepth 2 -name "TME" -type f | head -1
  fi
}

BIN="$(find_binary)"
if [ -z "$BIN" ]; then
  echo "Kein vorhandenes Build unter dist/ gefunden - baue zuerst..."
  "$REPO_ROOT/build_linux.sh" "${BUILD_ARGS[@]}"
  BIN="$(find_binary)"
fi

if [ -z "$BIN" ]; then
  echo "FEHLER: Kein TME-Binary gefunden, Installation abgebrochen." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
BIN_SRC_DIR="$(dirname "$BIN")"

echo "Kopiere Build von $BIN_SRC_DIR nach $INSTALL_DIR ..."
if [ -d "$BIN_SRC_DIR/_internal" ]; then
  # --onedir-Build: kompletten Ordnerinhalt kopieren (Binary + _internal/ mit
  # allen gebuendelten Abhaengigkeiten/Datas).
  cp -r "$BIN_SRC_DIR/." "$INSTALL_DIR/"
else
  # --onefile-Build (--release): nur die eine Datei.
  cp "$BIN" "$INSTALL_DIR/TME"
fi
chmod +x "$INSTALL_DIR/TME"

# config.yaml (falls vorhanden) neben das Binary kopieren - die App laedt sie
# ueber einen arbeitsverzeichnis-relativen Pfad (Path("config.yaml") in
# ui/app.py), nicht aus dem PyInstaller-Bundle selbst. Der Desktop-Eintrag
# setzt Path= auf INSTALL_DIR (siehe generate_build_files.py --binary-path),
# damit dieser relative Pfad beim Start ueber den Desktop-Eintrag aufgeloest
# werden kann.
if [ -f "$REPO_ROOT/config.yaml" ]; then
  cp "$REPO_ROOT/config.yaml" "$INSTALL_DIR/config.yaml"
  echo "config.yaml nach $INSTALL_DIR kopiert."
fi

# Fenster-/Menu-Icon mit an den Zielort kopieren, damit der Desktop-Eintrag
# auch funktioniert, wenn das Repo-Checkout spaeter geloescht wird.
if [ -f "$REPO_ROOT/Telegram-Nachrichten Herunterladen.png" ]; then
  cp "$REPO_ROOT/Telegram-Nachrichten Herunterladen.png" "$INSTALL_DIR/Telegram-Nachrichten Herunterladen.png"
fi

echo "Installiert nach: $INSTALL_DIR/TME"

echo "Aktualisiere Desktop-Eintrag (zeigt jetzt auf das Binary)..."
python3 "$REPO_ROOT/scripts/generate_build_files.py" \
  --binary-path "$INSTALL_DIR/TME" \
  --with-desktop-entry --no-requirements --no-spec

echo "Fertig. Desktop-Eintrag zeigt jetzt auf $INSTALL_DIR/TME."
echo "Deinstallieren: python3 scripts/generate_build_files.py --uninstall-desktop && rm -rf '$INSTALL_DIR'"
