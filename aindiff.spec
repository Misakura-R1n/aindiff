# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['scripts/launcher.py'],
    pathex=['src'],
    binaries=[],
    datas=[('vendor/alice/alice.exe', 'vendor/alice'),
           ('src/aindiff/assets/aindiff.png', 'aindiff/assets')],
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
    name='aindiff',
    icon='src/aindiff/assets/aindiff.ico',
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
    name='aindiff',
)
