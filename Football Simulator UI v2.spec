# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['ui_v2_main.py'],
    pathex=['/Users/gabrielmu/Documents/Football Simulator'],
    binaries=[],
    datas=[('足球模拟器总配置.json', '.')],
    hiddenimports=['football_simulator.ui_v2.pages.competition_page', 'football_simulator.ui_v2.pages.dashboard_page', 'football_simulator.ui_v2.pages.draft_page', 'football_simulator.ui_v2.pages.history_page', 'football_simulator.ui_v2.pages.match_detail_page', 'football_simulator.ui_v2.pages.matches_page', 'football_simulator.ui_v2.pages.player_profile_page', 'football_simulator.ui_v2.pages.players_page', 'football_simulator.ui_v2.pages.saves_page', 'football_simulator.ui_v2.pages.season_overview_page', 'football_simulator.ui_v2.pages.team_profile_page', 'football_simulator.ui_v2.pages.teams_page', 'football_simulator.ui_v2.pages.transfers_page', 'football_simulator.ui_v2.pages.weekly_report_page', 'football_simulator.ui_v2.components.team_crest', 'football_simulator.ui_v2.design_tokens'],
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
    name='Football Simulator UI v2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['/Users/gabrielmu/Documents/Football Simulator/assets/app.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Football Simulator UI v2',
)
app = BUNDLE(
    coll,
    name='Football Simulator UI v2.app',
    icon='/Users/gabrielmu/Documents/Football Simulator/assets/app.icns',
    bundle_identifier='com.gabrielmu.footballsimulator.uiv2',
)
