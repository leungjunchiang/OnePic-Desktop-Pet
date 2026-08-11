"""为用户提供的独立动作 PNG 生成带编号的本地联系表。"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def build_index(source: Path, output: Path) -> None:
    paths = sorted(source.glob("*.png"), key=lambda path: path.name.casefold())
    if not paths:
        raise SystemExit(f"没有找到 PNG：{source}")

    columns = 5
    cell_width, cell_height = 260, 300
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#e8ebf1")
    draw = ImageDraw.Draw(sheet)
    number_font = _font(24)
    detail_font = _font(13)

    for index, path in enumerate(paths, start=1):
        image = Image.open(path).convert("RGBA")
        alpha_bbox = image.getchannel("A").getbbox()
        crop = image.crop(alpha_bbox) if alpha_bbox else image
        crop.thumbnail((232, 232), Image.Resampling.LANCZOS)
        column = (index - 1) % columns
        row = (index - 1) // columns
        x0, y0 = column * cell_width, row * cell_height
        px = x0 + (cell_width - crop.width) // 2
        py = y0 + 8 + (232 - crop.height) // 2
        checker = Image.new("RGB", (232, 232), "white")
        checker_draw = ImageDraw.Draw(checker)
        for cy in range(0, 232, 16):
            for cx in range(0, 232, 16):
                if (cx // 16 + cy // 16) % 2:
                    checker_draw.rectangle((cx, cy, cx + 15, cy + 15), fill="#dfe3ea")
        sheet.paste(checker, (x0 + 14, y0 + 8))
        sheet.paste(crop, (px, py), crop)
        draw.text((x0 + 14, y0 + 246), f"{index:02d}", font=number_font, fill="#15202b")
        draw.text(
            (x0 + 58, y0 + 251),
            f"{image.width}x{image.height} {image.mode}",
            font=detail_font,
            fill="#34495e",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)
    manifest = output.with_suffix(".txt")
    manifest.write_text(
        "\n".join(f"{index:02d}\t{path.name}" for index, path in enumerate(paths, start=1)),
        encoding="utf-8",
    )
    print(output)
    print(manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build_index(args.source, args.output)


if __name__ == "__main__":
    main()
