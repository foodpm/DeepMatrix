# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.hooks import collect_submodules

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
backend_dir = os.path.join(repo_root, "backend")

block_cipher = None

hiddenimports = []
hiddenimports += collect_submodules("webview")

a = Analysis(
    [os.path.join(backend_dir, "desktop.py")],
    pathex=[backend_dir],
    binaries=[],
    datas=[
        Tree(os.path.join(backend_dir, "frontend"), prefix="frontend"),
        Tree(os.path.join(backend_dir, "models"), prefix="models"),
        Tree(os.path.join(backend_dir, "data"), prefix="data"),
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
    icon=None,
    bundle_identifier="com.foodpm.deepmatrix",
)
