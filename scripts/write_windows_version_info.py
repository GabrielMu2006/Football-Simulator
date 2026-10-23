# -*- coding: utf-8 -*-
"""从 ``football_simulator/version.py`` 生成 PyInstaller Windows 版本资源。

单一版本真源是 ``APP_VERSION``：EXE 的文件版本、产品版本和 ZIP 文件名都由它
推导，避免再出现 EXE 仍显示 1.0.1 的手工维护漂移。

Windows 构建脚本调用：
    python scripts/write_windows_version_info.py --output build/windows_version_info.txt
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

APP_NAME = "Football Simulator"
COMPANY_NAME = "GabrielMu2006"
VERSION_PATTERN = re.compile(r'^APP_VERSION\s*=\s*["\']([0-9]+(?:\.[0-9]+){0,3})["\']', re.MULTILINE)


class VersionInfoError(RuntimeError):
    pass


def read_app_version(root: Path) -> str:
    """读取产品版本号（不导入包，避免构建期触发 Qt 依赖）。"""
    version_file = Path(root) / "football_simulator" / "version.py"
    try:
        text = version_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise VersionInfoError(f"无法读取版本文件：{version_file}") from exc
    match = VERSION_PATTERN.search(text)
    if not match:
        raise VersionInfoError(f"{version_file} 中未找到 APP_VERSION")
    return match.group(1)


def version_tuple(version: str) -> tuple:
    parts = [int(part) for part in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def render_version_info(version: str, app_name: str = APP_NAME, company: str = COMPANY_NAME) -> str:
    major, minor, patch, build = version_tuple(version)
    numbers = f"{major}, {minor}, {patch}, {build}"
    return f"""# UTF-8
# 由 scripts/write_windows_version_info.py 从 football_simulator/version.py 生成。
# 不要手工编辑：改版本请改 version.py 后重新构建。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numbers}),
    prodvers=({numbers}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'{company}'),
          StringStruct(u'FileDescription', u'{app_name}'),
          StringStruct(u'FileVersion', u'{version}'),
          StringStruct(u'InternalName', u'{app_name}'),
          StringStruct(u'LegalCopyright', u'Copyright (C) 2026 {company}'),
          StringStruct(u'OriginalFilename', u'{app_name}.exe'),
          StringStruct(u'ProductName', u'{app_name}'),
          StringStruct(u'ProductVersion', u'{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def write_version_info(output: Path, root: Path, app_name: str = APP_NAME) -> tuple:
    version = read_app_version(root)
    text = render_version_info(version, app_name=app_name)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return version, output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 Windows EXE 版本资源")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--output", required=True)
    parser.add_argument("--app-name", default=APP_NAME)
    args = parser.parse_args(argv)
    version, output = write_version_info(Path(args.output), Path(args.root), args.app_name)
    print(f"version={version} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
