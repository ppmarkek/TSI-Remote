"""Generate the PNG and ICO runtime assets from the Konspekt icon design."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PNG = ROOT / "assets" / "konspekt.png"
ICO = ROOT / "assets" / "konspekt.ico"

CANVAS = 256
GREEN = "#176B45"
PAPER = "#F7FAF8"
FOLD = "#CDE4D6"
FOLD_LINE = "#A4CEB4"


def _rounded(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def build_icon() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), GREEN)
    draw = ImageDraw.Draw(image)
    _rounded(draw, (0, 0, CANVAS - 1, CANVAS - 1), 56, GREEN)
    _rounded(draw, (61, 47, 183, 209), 16, PAPER)
    draw.polygon([(153, 47), (183, 77), (153, 77)], fill=FOLD)
    draw.line([(153, 47), (153, 77), (183, 77)], fill=FOLD_LINE, width=9, joint="curve")
    for y, width in ((112, 74), (139, 74), (166, 43)):
        draw.ellipse((63, y - 7, 77, y + 7), fill=GREEN)
        draw.line([(91, y), (91 + width, y)], fill=GREEN, width=13)
    return image


def main() -> None:
    icon = build_icon()
    icon.save(PNG, format="PNG")
    icon.save(
        ICO,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"Wrote {PNG} and {ICO}")


if __name__ == "__main__":
    main()
