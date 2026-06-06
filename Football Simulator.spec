# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['mac_app.py'],
    pathex=['/Users/gabrielmu/Documents/Football Simulator'],
    binaries=[],
    datas=[('saves', 'saves')],
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
    name='Football Simulator',
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
    name='Football Simulator',
)
app = BUNDLE(
    coll,
    name='Football Simulator.app',
    icon=None,
    bundle_identifier='com.gabrielmu.footballsimulator',
)
