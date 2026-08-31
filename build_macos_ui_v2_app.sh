#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="Football Simulator UI v2"
DIST_DIR="dist-ui-v2"
APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
SHARED_CONFIG_NAME="足球模拟器总配置.json"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-ui-v2}"

rm -rf build-ui-v2 "$DIST_DIR"
export PYINSTALLER_CONFIG_DIR="$ROOT_DIR/.pyinstaller-config-ui-v2"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

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
  --workpath "build-ui-v2" \
  --add-data "${SHARED_CONFIG_NAME}:." \
  --add-data "team_badges_40_v2/PNG:team_badges_40_v2/PNG" \
  --paths "$ROOT_DIR" \
  --hidden-import football_simulator.ui_v2.pages.competition_page \
  --hidden-import football_simulator.ui_v2.pages.dashboard_page \
  --hidden-import football_simulator.ui_v2.pages.draft_page \
  --hidden-import football_simulator.ui_v2.pages.history_page \
  --hidden-import football_simulator.ui_v2.pages.match_detail_page \
  --hidden-import football_simulator.ui_v2.pages.matches_page \
  --hidden-import football_simulator.ui_v2.pages.player_profile_page \
  --hidden-import football_simulator.ui_v2.pages.players_page \
  --hidden-import football_simulator.ui_v2.pages.saves_page \
  --hidden-import football_simulator.ui_v2.pages.season_overview_page \
  --hidden-import football_simulator.ui_v2.pages.team_profile_page \
  --hidden-import football_simulator.ui_v2.pages.teams_page \
  --hidden-import football_simulator.ui_v2.pages.transfers_page \
  --hidden-import football_simulator.ui_v2.pages.weekly_report_page \
  --hidden-import football_simulator.ui_v2.components.team_crest \
  --hidden-import football_simulator.ui_v2.design_tokens \
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
