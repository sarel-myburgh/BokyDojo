"""Generate deterministic raster PWA icons from the BokyDojo mark."""

from pathlib import Path

from PIL import Image, ImageDraw

OUTPUT = Path(__file__).resolve().parents[1] / "static" / "icons"

for size in (192, 512):
    image = Image.new("RGB", (size, size), "#111827")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 7, fill="#111827")
    draw.rectangle((size * 0.18, size * 0.22, size * 0.82, size * 0.31), fill="white")
    draw.rectangle((size * 0.25, size * 0.31, size * 0.34, size * 0.78), fill="white")
    draw.rectangle((size * 0.66, size * 0.31, size * 0.75, size * 0.78), fill="white")
    draw.polygon(
        [
            (size * 0.34, size * 0.38),
            (size * 0.50, size * 0.51),
            (size * 0.66, size * 0.38),
            (size * 0.66, size * 0.51),
            (size * 0.50, size * 0.64),
            (size * 0.34, size * 0.51),
        ],
        fill="#f59e0b",
    )
    image.save(OUTPUT / f"bokydojo-{size}.png", optimize=True)
