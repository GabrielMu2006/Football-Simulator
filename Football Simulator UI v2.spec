# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['ui_v2_main.py'],
    pathex=['/Users/gabrielmu/Documents/Football Simulator'],
    binaries=[],
    datas=[('足球模拟器总配置.json', '.')],
    hiddenimports=[],
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
    icon=None,
    bundle_identifier=None,
)
