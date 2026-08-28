# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the standalone macOS study application bundle."""

from pathlib import Path
import os
import sys

packaging_dir = Path(SPECPATH)
if str(packaging_dir) not in sys.path:
    sys.path.insert(0, str(packaging_dir))

from common import get_project_roots, collect_shared_packaging_info

project_root, source_root, package_root = get_project_roots(SPECPATH)
block_cipher = None

datas, binaries, hiddenimports = collect_shared_packaging_info(project_root)

hiddenimports.extend([
    "webview.platforms.cocoa",
    "objc",
    "AppKit",
    "Foundation",
    "WebKit",
])

a = Analysis(
    [str(package_root / "__main__.py")],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Konspekt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Konspekt",
    upx=False,
)

icon_path = str(project_root / "assets" / "konspekt.icns")
if not Path(icon_path).is_file():
    icon_path = str(project_root / "assets" / "konspekt.png") if (project_root / "assets" / "konspekt.png").is_file() else None

app = BUNDLE(
    coll,
    name="Konspekt.app",
    icon=icon_path,
    bundle_identifier="lv.tsi.konspekt",
    info_plist={
        "CFBundleName": "Konspekt",
        "CFBundleDisplayName": "Konspekt",
        "CFBundleIdentifier": "lv.tsi.konspekt",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0.0",
        "NSHumanReadableCopyright": "Copyright © 2026 TSI",
    },
)
