# -*- mode: python ; coding: utf-8 -*-
# Paths relative to this .spec (scripts/setup_exe/).

block_cipher = None

a = Analysis(
    ['bts_setup.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('bts_app_icon.ico', '.'),
        ('bts_payload.zip', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BTS-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='bts_app_icon.ico',
    version='file_version_info.txt',
)
