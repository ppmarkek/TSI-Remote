"""Build platform icons from the approved raster artwork."""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "konspekt-source.png"
PNG = ROOT / "assets" / "konspekt.png"
ICO = ROOT / "assets" / "konspekt.ico"
ICNS = ROOT / "assets" / "konspekt.icns"

CANVAS = 256
MAC_CANVAS = 1024
BACKGROUND = (255, 255, 255, 255)


def _content_bounds(image: Image.Image) -> tuple[int, int, int, int]:
    """Find the black/green mark while ignoring the near-white source canvas."""
    luminance = image.convert("L")
    foreground = luminance.point(lambda value: 255 if value < 240 else 0)
    bounds = foreground.getbbox()
    if bounds is None:
        raise ValueError(f"No visible artwork found in {SOURCE}")
    return bounds


def _prepare_master() -> Image.Image:
    source = Image.open(SOURCE).convert("RGBA")
    left, top, right, bottom = _content_bounds(source)

    # Retain the supplied geometry, but remove the oversized export canvas.
    artwork = source.crop((left, top, right, bottom))
    longest_edge = max(artwork.size)
    padding = round(longest_edge * 0.16)
    square_edge = longest_edge + 2 * padding
    master = Image.new("RGBA", (square_edge, square_edge), BACKGROUND)
    position = (
        (square_edge - artwork.width) // 2,
        (square_edge - artwork.height) // 2,
    )
    master.alpha_composite(artwork, position)
    return master


def render_icon(output_size: int) -> Image.Image:
    return _prepare_master().resize((output_size, output_size), Image.Resampling.LANCZOS)


def main() -> None:
    runtime_icon = render_icon(CANVAS)
    mac_icon = render_icon(MAC_CANVAS)
    runtime_icon.save(PNG, format="PNG")
    runtime_icon.save(
        ICO,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    mac_icon.save(ICNS, format="ICNS")
    print(f"Wrote {PNG}, {ICO}, and {ICNS} from {SOURCE}")


if __name__ == "__main__":
    main()
