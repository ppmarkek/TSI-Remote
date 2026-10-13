# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the standalone Windows study application."""

from pathlib import Path
import os
import shutil

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    get_package_paths,
)


project_root = Path(SPECPATH).parent
source_root = project_root / "src"
package_root = source_root / "konspekt"
block_cipher = None
datas: list[tuple[str, str]] = [
    (str(project_root / "assets" / "konspekt.png"), "assets"),
]
# The HTML interface: index.html, styles.css and app.js.
for static_file in sorted((package_root / "web" / "static").iterdir()):
    if static_file.is_file():
        datas.append((str(static_file), "konspekt/web/static"))
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

for package in (
    "imageio_ffmpeg",
    "faster_whisper",
    "ctranslate2",
    "av",
    "tokenizers",
    "huggingface_hub",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)

# Keep the personal ChatGPT sign-in surface Windows-only and WebView2-only.
# pywebview loads its WinForms backend dynamically, while pythonnet's `clr`
# hook is responsible for the managed runtime bridge.
datas.extend(collect_data_files("webview", subdir="js"))

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
if not python_runtime.is_file():
    raise SystemExit("Python.Runtime.dll is required to package the WebView2 sign-in window.")
binaries.append((str(python_runtime), "pythonnet/runtime"))

hiddenimports.extend(
    [
        "faster_whisper",
        "ctranslate2",
        "av",
        "tokenizers",
        "huggingface_hub",
        "imageio_ffmpeg",
        "konspekt.web.api",
        "konspekt.web.main",
        "konspekt.web.server",
        "konspekt.web.tasks",
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr",
        "pythonnet",
        "clr_loader",
    ]
)

tesseract_executable = shutil.which("tesseract")
if not tesseract_executable:
    candidate = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Tesseract-OCR"
        / "tesseract.exe"
    )
    tesseract_executable = str(candidate) if candidate.is_file() else None
if not tesseract_executable:
    raise SystemExit("Tesseract is required to build Konspekt. Run setup_local_ai.ps1 first.")

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

# pywebview ships a generic hook for every supported renderer/platform. Trim its
# Windows payload to EdgeChromium. Version 6.2.1 probes all three native-loader
# directories at import time, so each loader is required even in an x64 build.
allowed_webview_libs = {
    "webview/lib/Microsoft.Web.WebView2.Core.dll",
    "webview/lib/Microsoft.Web.WebView2.WinForms.dll",
    "webview/lib/runtimes/win-arm64/native/WebView2Loader.dll",
    "webview/lib/runtimes/win-x64/native/WebView2Loader.dll",
    "webview/lib/runtimes/win-x86/native/WebView2Loader.dll",
}


def keep_webview_runtime(entry):
    destination = entry[0].replace("\\", "/")
    return not destination.startswith("webview/lib/") or destination in allowed_webview_libs


a.datas = [entry for entry in a.datas if keep_webview_runtime(entry)]
a.binaries = [entry for entry in a.binaries if keep_webview_runtime(entry)]
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
    icon=str(project_root / "assets" / "konspekt.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Konspekt",
    upx=False,
)
