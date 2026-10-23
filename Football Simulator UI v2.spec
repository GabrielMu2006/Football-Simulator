# -*- mode: python ; coding: utf-8 -*-
# 资源路径全部从 spec 所在项目根推导，不写开发机绝对路径。
from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    ['ui_v2_main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ('足球模拟器总配置.json', '.'),
        ('team_badges_40_v3/PNG', 'team_badges_40_v3/PNG'),
        ('team_badges_40_v3/manifest.json', 'team_badges_40_v3'),
        ('team_badges_40_v2/PNG', 'team_badges_40_v2/PNG'),
        ('football_simulator/rules', 'football_simulator/rules'),
        ('assets/atmosphere', 'assets/atmosphere'),
        ('assets/app_icon_256.png', 'assets'),
    ] + ([('assets/trophies/v1', 'assets/trophies/v1')] if (ROOT / 'assets/trophies/v1').is_dir() else []),
    hiddenimports=[
        'football_simulator.ui.main_window',
        'football_simulator.ui.pages.core',
        'football_simulator.ui.pages.competitions',
        'football_simulator.ui.pages.entities',
        'football_simulator.ui.pages.tools',
        'football_simulator.ui.pages.honors',
        'football_simulator.ui.trophies',
        'football_simulator.persistence_v2.store',
        'football_simulator.app.service',
        'football_simulator.app.queries',
        'football_simulator.ui.team_crest',
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
    icon=[str(ROOT / 'assets' / 'app.icns')],
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
    icon=str(ROOT / 'assets' / 'app.icns'),
    bundle_identifier='com.gabrielmu.footballsimulator.uiv2',
)
