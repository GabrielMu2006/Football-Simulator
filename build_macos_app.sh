#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="Football Simulator"
APP_BUNDLE="dist/${APP_NAME}.app"
CLI_DIST_DIR="dist/${APP_NAME} CLI"
CLI_BINARY_NAME="${APP_NAME} CLI"
SHARED_CONFIG_NAME="足球模拟器总配置.json"

rm -rf build dist
export PYINSTALLER_CONFIG_DIR="$ROOT_DIR/.pyinstaller-config"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

.venv/bin/pyinstaller \
  --noconfirm \
  --clean \
  --console \
  --onedir \
  --name "$CLI_BINARY_NAME" \
  --add-data "saves:saves" \
  --add-data "${SHARED_CONFIG_NAME}:." \
  --paths "$ROOT_DIR" \
  terminal_main.py

mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

cp -R "$CLI_DIST_DIR" "$APP_BUNDLE/Contents/Resources/cli"
cp "$SHARED_CONFIG_NAME" "dist/$SHARED_CONFIG_NAME"

cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>Football Simulator</string>
  <key>CFBundleExecutable</key>
  <string>Football Simulator</string>
  <key>CFBundleIdentifier</key>
  <string>com.gabrielmu.footballsimulator</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Football Simulator</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.0.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cat > "$APP_BUNDLE/Contents/MacOS/${APP_NAME}" <<'SCRIPT'
#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLI_DIR="$APP_DIR/Resources/cli"
CLI_BIN="$CLI_DIR/Football Simulator CLI"

osascript <<OSA
tell application "Terminal"
  activate
  do script quoted form of "$CLI_BIN"
  delay 0.2
  try
    set number of columns of front window to 168
    set number of rows of front window to 42
  end try
end tell
OSA
SCRIPT

chmod +x "$APP_BUNDLE/Contents/MacOS/${APP_NAME}"
codesign --force --deep --sign - "$APP_BUNDLE"
