#!/usr/bin/env bash
# Installiert das mit build_linux.sh gebaute Binary lokal (ohne root-Rechte,
# unter ~/.local/share/tme/) und richtet/aktualisiert den Desktop-Eintrag so
# ein, dass er auf das eigenstaendige Binary zeigt statt auf einen
# venv-Python-Aufruf von ui/app.py (siehe scripts/generate_build_files.py
# --binary-path).
#
# Nutzung:
#   ./install_linux.sh              # baut bei Bedarf neu (dist/ fehlt oder ist
#                                     # nicht auf dem aktuellen Codestand, siehe
#                                     # Versionsstempel dist/BUILD_VERSION.txt)
#                                     # und installiert
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

# Aktueller Codestand des Arbeitsbaums, im selben Format wie der Stempel, den
# build_linux.sh in dist/BUILD_VERSION.txt schreibt (siehe dort).
current_version() {
  local hash dirty
  hash="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  dirty=""
  if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
    dirty="-dirty"
  fi
  echo "${hash}${dirty}"
}

# Im dist/-Build hinterlegten Codestand auslesen (leer, falls Datei fehlt -
# z.B. bei einem dist/-Verzeichnis aus einer aelteren Skript-Version ohne
# Versionsstempel).
dist_version() {
  local vfile="$REPO_ROOT/dist/BUILD_VERSION.txt"
  if [ -f "$vfile" ]; then
    grep -m1 '^commit=' "$vfile" | cut -d= -f2-
  fi
}

BIN="$(find_binary)"
CUR_VERSION="$(current_version)"
DIST_VERSION="$(dist_version)"

if [ -z "$BIN" ]; then
  echo "Kein vorhandenes Build unter dist/ gefunden - baue zuerst..."
  "$REPO_ROOT/build_linux.sh" "${BUILD_ARGS[@]}"
  BIN="$(find_binary)"
  DIST_VERSION="$(dist_version)"
elif [ -z "$DIST_VERSION" ] || [ "$DIST_VERSION" != "$CUR_VERSION" ]; then
  # dist/ existiert, spiegelt aber nicht den aktuellen Arbeitsbaum wider (z.B.
  # Build von einem frueheren Codestand liegengeblieben, oder dist/ stammt aus
  # einem Lauf vor Einfuehrung des Versionsstempels). Ohne diesen Check wuerde
  # hier stillschweigend ein veraltetes Binary installiert - genau der Fehler,
  # der urspruenglich dazu fuehrte, dass nach build+install ein alter
  # Codestand lief.
  echo "dist/-Build (Version: ${DIST_VERSION:-unbekannt}) entspricht nicht dem aktuellen Codestand (${CUR_VERSION}) - baue neu..."
  "$REPO_ROOT/build_linux.sh" "${BUILD_ARGS[@]}"
  BIN="$(find_binary)"
  DIST_VERSION="$(dist_version)"
fi

if [ -z "$BIN" ]; then
  echo "FEHLER: Kein TME-Binary gefunden, Installation abgebrochen." >&2
  exit 1
fi

if [ -n "$DIST_VERSION" ] && [ "$DIST_VERSION" != "$CUR_VERSION" ]; then
  echo "WARNUNG: Installierte Version (${DIST_VERSION}) weicht weiterhin vom aktuellen Codestand (${CUR_VERSION}) ab." >&2
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

if [ -f "$REPO_ROOT/dist/BUILD_VERSION.txt" ]; then
  cp "$REPO_ROOT/dist/BUILD_VERSION.txt" "$INSTALL_DIR/BUILD_VERSION.txt"
fi

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

echo "Installiert nach: $INSTALL_DIR/TME (Version: ${DIST_VERSION:-unbekannt})"

echo "Aktualisiere Desktop-Eintrag (zeigt jetzt auf das Binary)..."
python3 "$REPO_ROOT/scripts/generate_build_files.py" \
  --binary-path "$INSTALL_DIR/TME" \
  --with-desktop-entry --no-requirements --no-spec

echo "Fertig. Desktop-Eintrag zeigt jetzt auf $INSTALL_DIR/TME."
echo "Deinstallieren: python3 scripts/generate_build_files.py --uninstall-desktop && rm -rf '$INSTALL_DIR'"
