#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="Football Simulator UI v2"
DIST_DIR="dist-ui-v2"
APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
SHARED_CONFIG_NAME="足球模拟器总配置.json"

rm -rf build-ui-v2 "$DIST_DIR"
export PYINSTALLER_CONFIG_DIR="$ROOT_DIR/.pyinstaller-config-ui-v2"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

.venv/bin/pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --distpath "$DIST_DIR" \
  --workpath "build-ui-v2" \
  --add-data "${SHARED_CONFIG_NAME}:." \
  --paths "$ROOT_DIR" \
  ui_v2_main.py

cp "$SHARED_CONFIG_NAME" "${DIST_DIR}/${SHARED_CONFIG_NAME}"
cp "$SHARED_CONFIG_NAME" "${DIST_DIR}/football_simulator_config.json"
codesign --force --deep --sign - "$APP_BUNDLE"
