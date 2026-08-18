"""把用户提供的六毛动作 PNG 去背景、统一画布并导入公开动作目录。

输入文件不会被改写。脚本先把当前动作复制到本地构建备份，再用旧动作的不透明边界作为
尺寸和落脚基准；新增工作动作使用办公室动作的基准，确保切换时人物大小接近且道具完整。
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


CANVAS_SIZE = (1024, 1024)
SAFE_MARGIN = 34

# 以更新后的本地索引编号映射到程序语义名称。44–46 是用户重新修正的电影、日光浴与睡觉。
SOURCE_TO_TARGET = {
    1: "11-pointing.png",
    2: "12-climbing.png",
    3: "13-bunny-carrot.png",
    4: "14-babuda.png",
    5: "15-overhead-heart.png",
    6: "16-ufo.png",
    7: "18-aquarium.png",
    8: "01-stand.png",
    9: "02-office.png",
    10: "03-headphones.png",
    11: "04-guitar.png",
    12: "05-drums.png",
    13: "06-coconut.png",
    14: "07-piano.png",
    15: "08-love.png",
    16: "09-night-reading.png",
    17: "19-tea.png",
    18: "20-motorcycle.png",
    19: "21-singing.png",
    20: "22-thermos.png",
    21: "24-tennis.png",
    22: "25-dolphin.png",
    23: "26-fishing.png",
    24: "27-wild-king.png",
    25: "28-seagull.png",
    26: "29-flowers.png",
    27: "30-milk-tea.png",
    28: "31-feast.png",
    29: "32-shells.png",
    30: "33-football.png",
    31: "34-group-photo.png",
    32: "35-whale.png",
    33: "36-demon.png",
    34: "37-flower-dance.png",
    35: "38-work-cheer.png",
    36: "39-work-study.png",
    37: "40-work-flow.png",
    38: "41-desk-nap.png",
    39: "42-daydream.png",
    40: "43-deep-focus.png",
    41: "44-work-complete.png",
    42: "45-overtime.png",
    43: "46-overwhelmed.png",
    44: "23-movie.png",
    45: "10-sunbath.png",
    46: "17-sleep.png",
}


def _remove_connected_neutral_background(image: Image.Image) -> Image.Image:
    """只移除与画布边缘相连的中性灰背景，保留白色贴纸边和内部浅色道具。"""

    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb = rgba[..., :3].astype(np.int16)
    channel_range = rgb.max(axis=2) - rgb.min(axis=2)
    luminance = rgb.mean(axis=2)

    border = np.concatenate(
        (luminance[:8, :].ravel(), luminance[-8:, :].ravel(), luminance[:, :8].ravel(), luminance[:, -8:].ravel())
    )
    low = max(135.0, float(np.percentile(border, 2)) - 38.0)
    high = min(251.0, float(np.percentile(border, 98)) + 34.0)
    candidate = (channel_range <= 30) & (luminance >= low) & (luminance <= high)

    # 生成图最外圈可能带少量彩色压缩噪点，但真实动作均远离边缘；把最外圈并入背景种子，
    # 可让四周的同一块灰色渐变稳定连通，又不会触及白色贴纸边。
    edge_seed = 6
    candidate[:edge_seed, :] = True
    candidate[-edge_seed:, :] = True
    candidate[:, :edge_seed] = True
    candidate[:, -edge_seed:] = True

    # Image.fromarray 可能返回只读共享视图；copy() 后 Pillow floodfill 才会真正写入。
    flood_mask = Image.fromarray(
        np.where(candidate, 255, 0).astype(np.uint8), "L"
    ).copy()
    ImageDraw.floodfill(flood_mask, (0, 0), 128, thresh=0)
    background = np.asarray(flood_mask) == 128
    rgba[background, 3] = 0

    # 清掉完全透明像素的 RGB，避免缩放时出现灰色晕边。
    rgba[background, :3] = 0
    result = Image.fromarray(rgba, "RGBA")
    bbox = result.getchannel("A").getbbox()
    transparent_ratio = float(background.mean())
    if (
        bbox is None
        or bbox == (0, 0, image.width, image.height)
        or transparent_ratio < 0.25
    ):
        raise ValueError(
            "背景抠除失败："
            f"transparent={transparent_ratio:.3f}, candidate={candidate.mean():.3f}, "
            f"range={low:.1f}-{high:.1f}, bbox={bbox}"
        )
    return result


def _reference_boxes(target: Path) -> dict[str, tuple[int, int, int, int]]:
    boxes: dict[str, tuple[int, int, int, int]] = {}
    for path in target.glob("*.png"):
        with Image.open(path) as image:
            bbox = image.convert("RGBA").getchannel("A").getbbox()
        if bbox is not None:
            boxes[path.name] = bbox
    return boxes


def _normalized(
    image: Image.Image,
    reference: tuple[int, int, int, int],
) -> Image.Image:
    """按旧动作边界等比缩放，并以中心和底部为锚点放回统一画布。"""

    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("动作图没有可见内容")
    content = image.crop(bbox)
    ref_left, ref_top, ref_right, ref_bottom = reference
    max_width = min(ref_right - ref_left, CANVAS_SIZE[0] - SAFE_MARGIN * 2)
    max_height = min(ref_bottom - ref_top, CANVAS_SIZE[1] - SAFE_MARGIN * 2)
    scale = min(max_width / content.width, max_height / content.height)
    size = (max(1, round(content.width * scale)), max(1, round(content.height * scale)))
    content = content.resize(size, Image.Resampling.LANCZOS)

    center_x = (ref_left + ref_right) / 2
    x = round(center_x - content.width / 2)
    y = round(ref_bottom - content.height)
    x = max(SAFE_MARGIN, min(x, CANVAS_SIZE[0] - SAFE_MARGIN - content.width))
    y = max(SAFE_MARGIN, min(y, CANVAS_SIZE[1] - SAFE_MARGIN - content.height))
    canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    canvas.alpha_composite(content, (x, y))
    return canvas


def import_folder(source: Path, target: Path, backup: Path) -> None:
    sources = sorted(source.glob("*.png"), key=lambda path: path.name.casefold())
    if len(sources) != len(SOURCE_TO_TARGET):
        raise SystemExit(f"应有 {len(SOURCE_TO_TARGET)} 张 PNG，实际找到 {len(sources)} 张：{source}")

    references = _reference_boxes(target)
    office_reference = references.get("02-office.png", (64, 64, 960, 980))
    backup.mkdir(parents=True, exist_ok=True)
    if not any(backup.glob("*.png")):
        for current in target.glob("*.png"):
            shutil.copy2(current, backup / current.name)

    for index, source_path in enumerate(sources, start=1):
        target_name = SOURCE_TO_TARGET[index]
        reference = references.get(target_name, office_reference)
        with Image.open(source_path) as raw:
            transparent = _remove_connected_neutral_background(raw)
        output = _normalized(transparent, reference)
        output.save(target / target_name, optimize=True)
        bbox = output.getchannel("A").getbbox()
        print(f"{index:02d} -> {target_name}: {bbox}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--target", type=Path, default=Path("assets/pet/daily-actions"))
    parser.add_argument("--backup", type=Path, default=Path("build/action-backup-v0.11.0"))
    args = parser.parse_args()
    import_folder(args.source, args.target, args.backup)


if __name__ == "__main__":
    main()
