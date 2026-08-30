"""Build platform icons from the approved raster artwork."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "konspekt-source.png"
PNG = ROOT / "assets" / "konspekt.png"
MAC_PNG = ROOT / "assets" / "konspekt-macos.png"
SIDEBAR = ROOT / "assets" / "konspekt-sidebar.png"
ICO = ROOT / "assets" / "konspekt.ico"
ICNS = ROOT / "assets" / "konspekt.icns"

CANVAS = 256
SIDEBAR_CANVAS = 32
MAC_CANVAS = 1024
MAC_SAFE_AREA_RATIO = 0.80


def _prepare_master() -> Image.Image:
    source = Image.open(SOURCE).convert("RGBA")
    plate_mask = Image.new("L", source.size, 0)
    plate_mask_draw = ImageDraw.Draw(plate_mask)
    plate_mask_draw.rounded_rectangle(
        (0, 0, source.width - 1, source.height - 1),
        radius=round(min(source.size) * 0.25),
        fill=255,
    )
    source.putalpha(plate_mask)
    return source


def render_icon(output_size: int) -> Image.Image:
    return _prepare_master().resize((output_size, output_size), Image.Resampling.LANCZOS)


def render_macos_icon(output_size: int) -> Image.Image:
    """Place the icon inside Apple's visual safe area for a balanced Dock size."""

    plate_size = round(output_size * MAC_SAFE_AREA_RATIO)
    plate = render_icon(plate_size)
    canvas = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
    offset = ((output_size - plate_size) // 2, (output_size - plate_size) // 2)
    canvas.alpha_composite(plate, offset)
    return canvas


def main() -> None:
    runtime_icon = render_icon(CANVAS)
    sidebar_icon = render_icon(SIDEBAR_CANVAS)
    mac_runtime_icon = render_macos_icon(CANVAS)
    mac_icon = render_macos_icon(MAC_CANVAS)
    runtime_icon.save(PNG, format="PNG")
    mac_runtime_icon.save(MAC_PNG, format="PNG")
    sidebar_icon.save(SIDEBAR, format="PNG")
    runtime_icon.save(
        ICO,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    mac_icon.save(ICNS, format="ICNS")
    print(f"Wrote {PNG}, {MAC_PNG}, {SIDEBAR}, {ICO}, and {ICNS} from {SOURCE}")


if __name__ == "__main__":
    main()
