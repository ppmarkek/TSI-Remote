"""Shared PyInstaller data collection helpers for Windows and macOS builds."""

from __future__ import annotations

import shutil
from pathlib import Path

REQUIRED_TESSERACT_MODELS = ("eng.traineddata", "rus.traineddata")


def get_project_roots(spec_path: str) -> tuple[Path, Path, Path]:
    project_root = (
        Path(spec_path).parent.parent
        if Path(spec_path).parent.name == "packaging"
        else Path(spec_path).parent
    )
    source_root = project_root / "src"
    package_root = source_root / "konspekt"
    return project_root, source_root, package_root


def collect_shared_packaging_info(
    project_root: Path,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    datas: list[tuple[str, str]] = []
    binaries: list[tuple[str, str]] = []
    hiddenimports: list[str] = [
        "konspekt",
        "konspekt.platform_services",
        "keyring",
        "keyring.backends",
        "platformdirs",
        "requests",
        "tqdm",
    ]

    assets_dir = project_root / "assets"
    if (assets_dir / "konspekt.png").is_file():
        datas.append((str(assets_dir / "konspekt.png"), "assets"))

    tesseract_executable = shutil.which("tesseract")
    if not tesseract_executable:
        raise RuntimeError("Tesseract is required for a release package but was not found on PATH.")

    tesseract_root = Path(tesseract_executable).parent
    binaries.append((tesseract_executable, "tesseract"))
    tessdata_candidates = (
        tesseract_root / "tessdata",
        tesseract_root.parent / "share" / "tessdata",
    )
    tessdata = next((candidate for candidate in tessdata_candidates if candidate.is_dir()), None)
    if tessdata is None:
        raise RuntimeError("Tesseract tessdata directory was not found on the build host.")

    missing_models = [
        model
        for model in REQUIRED_TESSERACT_MODELS
        if not (tessdata / model).is_file() or (tessdata / model).stat().st_size <= 0
    ]
    if missing_models:
        raise RuntimeError(
            "Required Tesseract language models are missing: " + ", ".join(missing_models)
        )

    for language_file in sorted(tessdata.glob("*.traineddata")):
        if language_file.is_file() and language_file.stat().st_size > 0:
            datas.append((str(language_file), "tesseract/tessdata"))

    try:
        from PyInstaller.utils.hooks import collect_all, collect_data_files

        for package in (
            "imageio_ffmpeg",
            "faster_whisper",
            "ctranslate2",
            "av",
            "tokenizers",
            "huggingface_hub",
        ):
            try:
                package_datas, package_binaries, package_hidden = collect_all(package)
                datas.extend(package_datas)
                binaries.extend(package_binaries)
                hiddenimports.extend(package_hidden)
            except Exception:
                pass

        try:
            datas.extend(collect_data_files("webview", subdir="js"))
        except Exception:
            pass
    except ImportError:
        pass

    return datas, binaries, hiddenimports
