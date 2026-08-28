"""Shared PyInstaller data collection helpers for Windows and macOS builds."""

from __future__ import annotations

from pathlib import Path


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
    if (assets_dir / "konspekt.svg").is_file():
        datas.append((str(assets_dir / "konspekt.svg"), "assets"))

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
                p_datas, p_binaries, p_hidden = collect_all(package)
                datas.extend(p_datas)
                binaries.extend(p_binaries)
                hiddenimports.extend(p_hidden)
            except Exception:
                pass

        try:
            datas.extend(collect_data_files("webview", subdir="js"))
        except Exception:
            pass
    except ImportError:
        pass

    return datas, binaries, hiddenimports
