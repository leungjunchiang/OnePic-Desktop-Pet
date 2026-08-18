"""把透明的 2×2 六毛造型图拆成四张统一正方形桌宠素材。

输入必须是已经带 alpha 通道的四宫格 PNG。脚本只裁切、按可见像素收紧边界并居中缩放，
不生成或重绘人物；输出可重复用于本地素材整理和公开构建前的视觉检查。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


NAMES = (
    "outfit-dream-cleaner.png",
    "outfit-dream-plane.png",
    "outfit-sleep-heart.png",
    "outfit-dream-cape.png",
)


def _fit_visible(image: Image.Image, size: int = 1024, padding: int = 36) -> Image.Image:
    """按 alpha 可见范围裁切并居中放入透明正方形。"""

    alpha = image.getchannel("A")
    bbox = alpha.point(lambda value: 255 if value >= 8 else 0).getbbox()
    if bbox is None:
        raise ValueError("分区内没有可见人物。")
    cropped = image.crop(bbox)
    available = size - padding * 2
    scale = min(available / cropped.width, available / cropped.height)
    resized = cropped.resize(
        (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(
        resized,
        ((size - resized.width) // 2, (size - resized.height) // 2),
    )
    return canvas


def split_sheet(source: Path, output_dir: Path) -> tuple[Path, ...]:
    """拆分四宫格并返回四张输出路径。"""

    image = Image.open(source).convert("RGBA")
    middle_x, middle_y = image.width // 2, image.height // 2
    boxes = (
        (0, 0, middle_x, middle_y),
        (middle_x, 0, image.width, middle_y),
        (0, middle_y, middle_x, image.height),
        (middle_x, middle_y, image.width, image.height),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name, box in zip(NAMES, boxes, strict=True):
        target = output_dir / name
        _fit_visible(image.crop(box)).save(target, optimize=True)
        outputs.append(target)
    return tuple(outputs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    for path in split_sheet(args.source, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
