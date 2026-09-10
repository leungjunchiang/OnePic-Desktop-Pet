"""将透明 PNG 叠到白、黑和棋盘底色上，供素材边缘与阴影复核。"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def write_previews(source: Path, destination: Path) -> None:
    """输出三种底色预览，不改变源 PNG。"""

    source_image = Image.open(source).convert("RGBA")
    destination.mkdir(parents=True, exist_ok=True)
    backgrounds = {
        "white": Image.new("RGBA", source_image.size, "white"),
        "black": Image.new("RGBA", source_image.size, "black"),
    }
    checker = Image.new("RGBA", source_image.size, "#e8e8e8")
    draw = ImageDraw.Draw(checker)
    tile = 32
    for y in range(0, checker.height, tile):
        for x in range(0, checker.width, tile):
            if (x // tile + y // tile) % 2:
                draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill="#ffffff")
    backgrounds["checker"] = checker
    for name, background in backgrounds.items():
        background.alpha_composite(source_image)
        background.convert("RGB").save(destination / f"{name}.png", "PNG")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    write_previews(args.source, args.destination)
