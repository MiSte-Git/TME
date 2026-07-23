#!/usr/bin/env bash
# Entwicklungs-Start unter Linux: startet die App direkt aus dem aktuellen
# Arbeitsstand (python3 ui/app.py über die lokale .venv) - kein Build, kein
# PyInstaller. Fuer schnelle Iteration waehrend der Entwicklung, analog zu
# scripts/run_ui.ps1 unter Windows (dort wird die venv aber vorausgesetzt;
# hier wird sie bei Bedarf automatisch angelegt).
#
# Nutzung:
#   ./run_linux_dev.sh          # normaler Start
#   ./run_linux_dev.sh --stt    # stellt zusaetzlich requirements-stt.txt sicher
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WITH_STT=0
for arg in "$@"; do
  case "$arg" in
    --stt) WITH_STT=1 ;;
    *) echo "Unbekannte Option: $arg (bekannt: --stt)" >&2; exit 1 ;;
  esac
done

VENV_DIR="$REPO_ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python3"

if [ ! -x "$VENV_PY" ]; then
  echo "Kein .venv gefunden - lege neue virtuelle Umgebung an: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  "$VENV_PY" -m pip install --upgrade pip --quiet
  echo "Installiere Abhaengigkeiten aus requirements.txt (einmalig - je nach Internetverbindung kann das 1-2 Minuten dauern)..."
  "$VENV_PY" -m pip install -r "$REPO_ROOT/requirements.txt"
fi

if [ "$WITH_STT" -eq 1 ]; then
  if "$VENV_PY" -c "import torch" >/dev/null 2>&1; then
    echo "STT-Abhaengigkeiten (torch/whisper) bereits installiert."
  else
    echo "Installiere optionale STT-Abhaengigkeiten aus requirements-stt.txt (~4-5 GB inkl. CUDA-Paketen, kann laenger dauern)..."
    "$VENV_PY" -m pip install -r "$REPO_ROOT/requirements-stt.txt"
  fi
fi

ENTRY="$REPO_ROOT/ui/app.py"
if [ ! -f "$ENTRY" ]; then
  echo "FEHLER: Einstiegspunkt nicht gefunden: $ENTRY" >&2
  exit 1
fi

echo "Starte App aus aktuellem Arbeitsstand ($VENV_PY)..."
exec "$VENV_PY" "$ENTRY"
