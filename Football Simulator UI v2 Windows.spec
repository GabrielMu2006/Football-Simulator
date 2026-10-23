# -*- mode: python ; coding: utf-8 -*-
"""Windows PyInstaller 配置（onedir + windowed，新 UI 栈）。

要点：
- 所有源路径从 ``SPECPATH``（本 spec 所在目录）推导，与调用时的工作目录无关；
- 资源清单集中在 ``DATA_FILES``，缺任何一项都让构建显式失败，而不是悄悄缺图；
- 入口是当前新 UI ``ui_v2_main.py``，页面是 ``football_simulator.ui.pages.*``；
- 版本资源由 ``scripts/write_windows_version_info.py`` 从产品版本生成后经
  ``FS_WINDOWS_VERSION_FILE`` 传入，避免 EXE 版本号再次落后于版本真源；
- PyInstaller 不支持跨平台交叉编译，本 spec 必须在 Windows 上运行。
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(SPECPATH)

APP_NAME = "Football Simulator"

#: (源路径, 包内目标路径)。源路径相对 ROOT，构建时转成绝对路径。
DATA_FILES = [
    ("football_simulator/rules", "football_simulator/rules"),
    ("team_badges_40_v3/PNG", "team_badges_40_v3/PNG"),
    ("team_badges_40_v3/manifest.json", "team_badges_40_v3"),
    ("team_badges_40_v2/PNG", "team_badges_40_v2/PNG"),
    ("assets/atmosphere", "assets/atmosphere"),
    ("assets/trophies/v1", "assets/trophies/v1"),
    ("assets/app_icon_256.png", "assets"),
    ("足球模拟器总配置.json", "."),
]

#: 动态/延迟引用与运行期必需模块；PyInstaller 无法静态分析全部路径。
HIDDEN_IMPORTS = [
    "football_simulator.ui.main_window",
    "football_simulator.ui.pages.core",
    "football_simulator.ui.pages.competitions",
    "football_simulator.ui.pages.entities",
    "football_simulator.ui.pages.tools",
    "football_simulator.ui.pages.honors",
    "football_simulator.ui.trophies",
    "football_simulator.app.service",
    "football_simulator.app.queries",
    "football_simulator.app.records",
    "football_simulator.app.presentation",
    "football_simulator.persistence_v2.store",
    "football_simulator.domain.formulas",
    "football_simulator.ui.team_crest",
]

ENTRY_POINT = "ui_v2_main.py"


def _resolve_data_sources():
    """把相对源路径转成绝对路径，并让缺失资源在构建前就失败。"""
    resolved = []
    missing = []
    for source, target in DATA_FILES:
        absolute = ROOT / source
        if not absolute.exists():
            missing.append(source)
            continue
        resolved.append((str(absolute), target))
    if missing:
        raise SystemExit("缺少打包资源：" + ", ".join(missing))
    return resolved


def _resolve_version_file():
    """版本资源：优先用构建脚本生成的产物，否则按产品版本即时生成。"""
    explicit = os.environ.get("FS_WINDOWS_VERSION_FILE")
    if explicit and Path(explicit).is_file():
        return explicit

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import write_windows_version_info as version_info
    finally:
        sys.path.pop(0)

    candidates = [ROOT.parent / ".tmp", Path(tempfile.gettempdir())]
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / "windows_version_info.generated.txt"
            version, _ = version_info.write_version_info(output, ROOT, APP_NAME)
            print(f"[spec] generated version resource for {version}: {output}")
            return str(output)
        except OSError:
            continue
    raise SystemExit("无法生成 Windows 版本资源文件")


VERSION_FILE = _resolve_version_file()
DATA_SOURCES = _resolve_data_sources()

a = Analysis(
    [str(ROOT / ENTRY_POINT)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATA_SOURCES,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 不把开发期依赖或另一次要 Qt 模块拖进用户包。
        "tkinter",
        "unittest",
        "pytest",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT / "assets" / "app.ico")],
    version=VERSION_FILE,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
