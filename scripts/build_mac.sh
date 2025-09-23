#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Optional: build Qt translations
if [ -x "ui/translations/build_qm.sh" ]; then
  ( cd ui/translations && ./build_qm.sh ) || true
fi

# Ensure pyinstaller is installed
if ! command -v pyinstaller >/dev/null 2>&1; then
  python3 -m pip install --user pyinstaller || true
fi

# Build via spec
pyinstaller --noconfirm telegram_odt.spec

APP="dist/Telegram-ODT.app"
if [ -d "$APP" ]; then
  echo "Built: $APP"
else
  echo "Build finished, see dist/" && ls -la dist || true
fi
