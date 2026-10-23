#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="Football Simulator UI v2"
DIST_DIR="${DIST_DIR:-dist-ui-v2}"
BUILD_DIR="${BUILD_DIR:-build-ui-v2}"
APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
SHARED_CONFIG_NAME="足球模拟器总配置.json"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-ui-v2}"

rm -rf "$BUILD_DIR" "$DIST_DIR"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT_DIR/.pyinstaller-config-ui-v2}"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

TROPHY_DATA_ARGS=()
if [ -d "$ROOT_DIR/assets/trophies/v1" ]; then
  TROPHY_DATA_ARGS=(--add-data "$ROOT_DIR/assets/trophies/v1:assets/trophies/v1")
fi

if [ ! -x "$VENV_DIR/bin/pyinstaller" ]; then
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    arch -arm64 python3 -m venv "$VENV_DIR"
  fi
  arch -arm64 "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements-ui-v2.txt"
fi

arch -arm64 "$VENV_DIR/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ROOT_DIR/assets/app.icns" \
  --osx-bundle-identifier "com.gabrielmu.footballsimulator.uiv2" \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  --add-data "${SHARED_CONFIG_NAME}:." \
  --add-data "team_badges_40_v3/PNG:team_badges_40_v3/PNG" \
  --add-data "team_badges_40_v3/manifest.json:team_badges_40_v3" \
  --add-data "team_badges_40_v2/PNG:team_badges_40_v2/PNG" \
  --add-data "football_simulator/rules:football_simulator/rules" \
  --add-data "assets/atmosphere:assets/atmosphere" \
  --add-data "assets/app_icon_256.png:assets" \
  "${TROPHY_DATA_ARGS[@]}" \
  --paths "$ROOT_DIR" \
  --hidden-import football_simulator.ui.main_window \
  --hidden-import football_simulator.ui.pages.core \
  --hidden-import football_simulator.ui.pages.competitions \
  --hidden-import football_simulator.ui.pages.entities \
  --hidden-import football_simulator.ui.pages.tools \
  --hidden-import football_simulator.ui.pages.honors \
  --hidden-import football_simulator.ui.trophies \
  --hidden-import football_simulator.persistence_v2.store \
  --hidden-import football_simulator.app.service \
  --hidden-import football_simulator.app.queries \
  --hidden-import football_simulator.ui.team_crest \
  ui_v2_main.py

cp "$SHARED_CONFIG_NAME" "${DIST_DIR}/${SHARED_CONFIG_NAME}"
cp "$SHARED_CONFIG_NAME" "${DIST_DIR}/football_simulator_config.json"
/usr/libexec/PlistBuddy -c "Delete :NSPrincipalClass" "$APP_BUNDLE/Contents/Info.plist" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Add :NSPrincipalClass string NSApplication" "$APP_BUNDLE/Contents/Info.plist"

# 写入应用版本（与 football_simulator/version.py 保持一致）。
APP_VERSION="$(grep -o 'APP_VERSION = "[^"]*"' "$ROOT_DIR/football_simulator/version.py" | sed 's/.*"\(.*\)"$/\1/')"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $APP_VERSION" "$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_VERSION" "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$APP_BUNDLE/Contents/Info.plist"

codesign --force --deep --sign - "$APP_BUNDLE"
