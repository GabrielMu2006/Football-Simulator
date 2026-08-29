#!/bin/zsh
# 从 dist-ui-v2/Football Simulator UI v2.app 生成可安装 DMG。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="Football Simulator UI v2"
APP_BUNDLE="${ROOT_DIR}/dist-ui-v2/${APP_NAME}.app"
DMG_TARGET="${ROOT_DIR}/release/macos/${APP_NAME}.dmg"
STAGING_DIR="${ROOT_DIR}/build-dmg/${APP_NAME}"

if [ ! -d "$APP_BUNDLE" ]; then
  echo "错误：未找到 $APP_BUNDLE，请先运行 build_macos_ui_v2_app.sh" >&2
  exit 1
fi

rm -rf "${ROOT_DIR}/build-dmg"
mkdir -p "$STAGING_DIR"
cp -R "$APP_BUNDLE" "$STAGING_DIR/"
cp "${ROOT_DIR}/足球模拟器总配置.json" "$STAGING_DIR/"
if [ -f "${ROOT_DIR}/football_simulator_config.json" ]; then
  cp "${ROOT_DIR}/football_simulator_config.json" "$STAGING_DIR/"
elif [ -f "${ROOT_DIR}/dist-ui-v2/football_simulator_config.json" ]; then
  cp "${ROOT_DIR}/dist-ui-v2/football_simulator_config.json" "$STAGING_DIR/"
fi

# 拖放到 Applications 的直观入口。
ln -s /Applications "$STAGING_DIR/Applications"

mkdir -p "$(dirname "$DMG_TARGET")"
rm -f "$DMG_TARGET"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "${ROOT_DIR}/build-dmg/${APP_NAME}" \
  -ov \
  -format UDZO \
  "$DMG_TARGET"

echo "DMG 生成完成：$DMG_TARGET"

# 清理临时 staging，避免项目内残留 .app 导致 Launchpad 重复图标。
rm -rf "${ROOT_DIR}/build-dmg"
