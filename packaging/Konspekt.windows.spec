# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the standalone Windows study application."""

from pathlib import Path
import os
import shutil
import sys

# Add packaging directory to sys.path so common.py is importable
packaging_dir = Path(SPECPATH)
if str(packaging_dir) not in sys.path:
    sys.path.insert(0, str(packaging_dir))

from common import get_project_roots, collect_shared_packaging_info
from PyInstaller.utils.hooks import collect_dynamic_libs, get_package_paths

project_root, source_root, package_root = get_project_roots(SPECPATH)
block_cipher = None

datas, binaries, hiddenimports = collect_shared_packaging_info(project_root)

# Windows-specific webview and pythonnet collection
try:
    for source, destination in collect_dynamic_libs("webview"):
        source_path = Path(source)
        if source_path.name in {
            "Microsoft.Web.WebView2.Core.dll",
            "Microsoft.Web.WebView2.WinForms.dll",
            "WebView2Loader.dll",
        }:
            binaries.append((source, destination))

    _, pythonnet_root = get_package_paths("pythonnet")
    python_runtime = Path(pythonnet_root) / "runtime" / "Python.Runtime.dll"
    if python_runtime.is_file():
        binaries.append((str(python_runtime), "pythonnet/runtime"))
except Exception:
    pass

hiddenimports.extend([
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
    "pythonnet",
    "clr_loader",
])

# Optional Tesseract bundling on Windows if present
tesseract_executable = shutil.which("tesseract")
if not tesseract_executable:
    candidate = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Tesseract-OCR"
        / "tesseract.exe"
    )
    if candidate.is_file():
        tesseract_executable = str(candidate)

if tesseract_executable:
    tesseract_root = Path(tesseract_executable).parent
    for file_path in tesseract_root.rglob("*"):
        if file_path.is_file():
            destination = Path("tesseract") / file_path.relative_to(tesseract_root).parent
            datas.append((str(file_path), str(destination)))

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

icon_path = str(project_root / "assets" / "konspekt.ico")
if not Path(icon_path).is_file():
    icon_path = None

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
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Konspekt",
    upx=False,
)
