#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Find lrelease (Qt translation compiler)
CANDIDATES=(
  lrelease
  lrelease-qt6
  pyside6-lrelease
  /usr/lib/qt6/bin/lrelease
  /usr/lib/qt5/bin/lrelease
  /usr/bin/lrelease-qt6
  /usr/bin/lrelease
)

LRELEASE=""
for c in "${CANDIDATES[@]}"; do
  if command -v "$c" >/dev/null 2>&1; then LRELEASE="$(command -v "$c")"; break; fi
  if [ -x "$c" ]; then LRELEASE="$c"; break; fi
done

if [ -z "$LRELEASE" ]; then
  echo "ERROR: lrelease not found. Install qt6-tools-dev-tools (Qt6) or qttools5-dev-tools (Qt5)." >&2
  echo "On Debian/Ubuntu: sudo apt-get install qt6-tools-dev-tools" >&2
  exit 1
fi

echo "Using lrelease: $LRELEASE"
LANGS=(de en fr it ru pl es hr nl fi)
for lang in "${LANGS[@]}"; do
  TS="app_${lang}.ts"
  QM="app_${lang}.qm"
  if [ -f "$TS" ]; then
    echo "Building $QM from $TS"
    "$LRELEASE" "$TS" -qm "$QM"
  else
    echo "Skipping $TS (not found)"
  fi
done

echo "Done. Generated:"
ls -l app_*.qm
