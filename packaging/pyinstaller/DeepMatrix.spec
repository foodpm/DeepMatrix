# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

repo_root = os.path.abspath(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
backend_dir = os.path.join(repo_root, "backend")
icon_ico = os.path.join(repo_root, "packaging", "assets", "logo.ico")

block_cipher = None

hiddenimports = []
hiddenimports += collect_submodules("webview")

a = Analysis(
    [os.path.join(backend_dir, "desktop.py")],
    pathex=[backend_dir],
    binaries=[],
    datas=[
        (os.path.join(backend_dir, "frontend"), "frontend"),
        (os.path.join(repo_root, "logo.png"), "frontend"),
        (os.path.join(backend_dir, "models"), "models"),
        (os.path.join(backend_dir, "data"), "data"),
        (os.path.join(backend_dir, "yolov8n.pt"), "."),
    ],
    hiddenimports=hiddenimports,
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
    a.datas,
    [],
    name="DeepMatrix",
    icon=icon_ico if os.path.isfile(icon_ico) else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DeepMatrix",
)
