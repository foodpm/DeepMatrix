# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

repo_root = os.path.abspath(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
backend_dir = os.path.join(repo_root, "backend")
icon_icns = os.path.join(repo_root, "packaging", "assets", "logo.icns")

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

app = BUNDLE(
    exe,
    name="DeepMatrix.app",
    icon=icon_icns if os.path.isfile(icon_icns) else None,
    bundle_identifier="com.foodpm.deepmatrix",
)
