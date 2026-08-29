"""Generate the PNG and ICO runtime assets from the Konspekt icon design."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PNG = ROOT / "assets" / "konspekt.png"
ICO = ROOT / "assets" / "konspekt.ico"

CANVAS = 256
SUPERSAMPLE = 4
GREEN = "#176B45"
PAPER = "#F7FAF8"
INPUT = "#CDE4D6"
FOCUS = "#A4CEB4"


def _scaled(value: int) -> int:
    return value * SUPERSAMPLE


def _rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: str,
) -> None:
    draw.rounded_rectangle(
        tuple(_scaled(value) for value in box),
        radius=_scaled(radius),
        fill=fill,
    )


def _round_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    width: int,
    fill: str,
) -> None:
    scaled_start = tuple(_scaled(value) for value in start)
    scaled_end = tuple(_scaled(value) for value in end)
    scaled_width = _scaled(width)
    radius = scaled_width // 2
    draw.line((scaled_start, scaled_end), fill=fill, width=scaled_width)
    for x, y in (scaled_start, scaled_end):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _indexed(image: Image.Image) -> Image.Image:
    """Keep assets small while preserving clean transparent corners."""

    alpha = image.getchannel("A").point(lambda value: 255 if value >= 96 else 0)
    opaque = Image.new("RGB", image.size, GREEN)
    opaque.paste(image.convert("RGB"), mask=alpha)
    indexed = opaque.quantize(
        colors=16,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )

    palette = indexed.getpalette() or []
    palette.extend([0] * (768 - len(palette)))
    indexed.putpalette(palette[:768])

    pixels = indexed.load()
    alpha_pixels = alpha.load()
    for y in range(indexed.height):
        for x in range(indexed.width):
            if alpha_pixels[x, y] == 0:
                pixels[x, y] = 255

    indexed.info["transparency"] = 255
    return indexed


def build_icon() -> Image.Image:
    size = _scaled(CANVAS)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    _rounded(draw, (0, 0, CANVAS, CANVAS), 58, GREEN)
    _rounded(draw, (54, 43, 84, 213), 15, PAPER)
    _round_line(draw, (84, 128), (169, 48), 31, INPUT)
    _round_line(draw, (84, 128), (177, 207), 31, PAPER)

    center_x = _scaled(86)
    center_y = _scaled(128)
    center_radius = _scaled(10)
    draw.ellipse(
        (
            center_x - center_radius,
            center_y - center_radius,
            center_x + center_radius,
            center_y + center_radius,
        ),
        fill=FOCUS,
    )

    return image.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def main() -> None:
    icon = _indexed(build_icon())
    icon.save(PNG, format="PNG", optimize=True, transparency=255)
    icon.save(
        ICO,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"Wrote {PNG} and {ICO}")


if __name__ == "__main__":
    main()
