#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv-ui-v2"

cd "$ROOT_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "首次启动：正在创建本地 Python 环境..."
  arch -arm64 python3 -m venv "$VENV_DIR"
fi

echo "正在检查 UI 依赖..."
if ! arch -arm64 "$VENV_DIR/bin/python" -c "import PySide6, PyInstaller" >/dev/null 2>&1; then
  arch -arm64 "$VENV_DIR/bin/python" -m pip install --upgrade pip
  arch -arm64 "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements-ui-v2.txt"
else
  echo "UI 依赖已就绪。"
fi

echo "正在启动 Football Simulator UI v2..."
arch -arm64 "$VENV_DIR/bin/python" "$ROOT_DIR/ui_v2_main.py"
