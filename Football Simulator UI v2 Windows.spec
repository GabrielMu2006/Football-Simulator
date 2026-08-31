# -*- mode: python ; coding: utf-8 -*-
# Windows PyInstaller 配置。
# 注意：PyInstaller 不支持跨平台交叉编译，必须在 Windows 上运行本 spec。
# 推荐从仓库根目录执行 scripts\build_windows.ps1（会自动准备 conda 环境并打包）。

from pathlib import Path


ROOT_DIR = Path.cwd()


a = Analysis(
    ["ui_v2_main.py"],
    pathex=[str(ROOT_DIR)],
    binaries=[],
    datas=[
        ("足球模拟器总配置.json", "."),
        ("team_badges_40_v2/PNG", "team_badges_40_v2/PNG"),
    ],
    hiddenimports=[
        # 动态路由加载的页面（main_window._PARALLEL_PAGE_SPECS 经 importlib 加载，
        # PyInstaller 无法静态分析，必须显式声明）。
        "football_simulator.ui_v2.pages.competition_page",
        "football_simulator.ui_v2.pages.dashboard_page",
        "football_simulator.ui_v2.pages.draft_page",
        "football_simulator.ui_v2.pages.history_page",
        "football_simulator.ui_v2.pages.match_detail_page",
        "football_simulator.ui_v2.pages.matches_page",
        "football_simulator.ui_v2.pages.player_profile_page",
        "football_simulator.ui_v2.pages.players_page",
        "football_simulator.ui_v2.pages.saves_page",
        "football_simulator.ui_v2.pages.season_overview_page",
        "football_simulator.ui_v2.pages.team_profile_page",
        "football_simulator.ui_v2.pages.teams_page",
        "football_simulator.ui_v2.pages.transfers_page",
        "football_simulator.ui_v2.pages.weekly_report_page",
        # 页面/组件里被延迟引用（如表格委托、队徽绘制）的可选导入。
        "football_simulator.ui_v2.components.crest_delegate",
        "football_simulator.ui_v2.components.empty_state",
        "football_simulator.ui_v2.components.entity_link",
        "football_simulator.ui_v2.components.entity_table",
        "football_simulator.ui_v2.components.filter_bar",
        "football_simulator.ui_v2.components.global_search",
        "football_simulator.ui_v2.components.page_header",
        "football_simulator.ui_v2.components.team_crest",
        "football_simulator.ui_v2.design_tokens",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Football Simulator UI v2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=[str(ROOT_DIR / "assets" / "app.ico")],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Football Simulator UI v2",
)
