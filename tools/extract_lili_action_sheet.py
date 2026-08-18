"""从六列六行动作表中提取透明六毛动作，不重绘人物。

输入是带浅灰背景和行下注释的固定 6×6 贴纸表。脚本先按动作区域裁切，再拟合每格的
平滑灰色背景，依据原像素与背景的颜色距离生成柔和 alpha，最后把可见贴纸居中放到统一
1024×1024 透明画布。外部编号和标题不进入输出，气泡等贴纸内部元素会被保留。
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


NAMES = (
    "stand", "office", "headphones", "guitar", "drums", "coconut",
    "piano", "love", "night-reading", "sunbath", "pointing", "climbing",
    "bunny-carrot", "babuda", "overhead-heart", "ufo", "sleep", "aquarium",
    "tea", "motorcycle", "singing", "thermos", "movie", "tennis",
    "dolphin", "fishing", "wild-king", "seagull", "flowers", "milk-tea",
    "feast", "shells", "football", "group-photo", "whale", "demon",
)

# 修正版 1121×1403 图中，去掉每行下方编号标题后的六个动作带。
ROW_BANDS = ((0, 195), (235, 416), (466, 647), (691, 875), (915, 1090), (1147, 1319))

# 每行插画宽度不同，边界放在贴纸之间的真实空隙，避免相邻道具串入当前动作。
ROW_X_EDGES = (
    (0, 165, 365, 548, 735, 920, 1121),
    (0, 210, 370, 550, 735, 915, 1121),
    (0, 180, 370, 550, 735, 915, 1121),
    (0, 210, 415, 600, 745, 930, 1121),
    (0, 210, 420, 570, 750, 940, 1121),
    (0, 210, 420, 535, 770, 940, 1121),
)

# 少数宽动作横跨等分中心，允许与相邻格轻微重叠后再按主体组件去背景。
X_OVERRIDES = {
    3: (555, 720),   # 弹吉他
    8: (370, 545),   # 夜读
    9: (560, 725),   # 海边日光浴
    15: (525, 705),  # UFO 悬挂
    16: (745, 900),  # 睡觉
    25: (200, 412),  # 钓螃蟹
    26: (435, 575),  # 荒野国王
    27: (580, 750),  # 与海鸥
    31: (210, 385),  # 捡贝壳
    32: (400, 520),  # 踢足球
    33: (510, 775),  # 合影
    34: (790, 920),  # 乘鲸云游
}


def _remove_small_islands(alpha: np.ndarray) -> np.ndarray:
    """清掉压缩噪点，保留人物、道具、音符和气泡等真实贴纸组件。"""

    binary = alpha >= 18
    height, width = binary.shape
    visited = np.zeros_like(binary, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    for start_y, start_x in zip(*np.nonzero(binary & ~visited), strict=False):
        if visited[start_y, start_x]:
            continue
        queue = deque([(int(start_y), int(start_x))])
        visited[start_y, start_x] = True
        component: list[tuple[int, int]] = []
        while queue:
            y, x = queue.popleft()
            component.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and binary[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    queue.append((ny, nx))
        if len(component) >= 10:
            components.append(component)
    if not components:
        return np.zeros_like(alpha)
    main = max(components, key=len)
    keep = np.zeros_like(binary, dtype=bool)
    for component in components:
        ys, xs = zip(*component, strict=True)
        min_y, max_y = min(ys), max(ys)
        min_x, max_x = min(xs), max(xs)
        edge_fragment = (
            (max_y < height * 0.11)
            or (min_y > height * 0.89)
            or (max_x < width * 0.025)
            or (min_x > width * 0.975)
            or min_x <= 1
            or max_x >= width - 2
            or min_y <= 1
            or max_y >= height - 2
        )
        if component is not main and edge_fragment:
            continue
        keep[np.asarray(ys), np.asarray(xs)] = True
    # 扩张两像素，把抗锯齿边缘与主体重新连接。
    expanded = keep.copy()
    for _ in range(2):
        expanded |= np.roll(expanded, 1, 0) | np.roll(expanded, -1, 0)
        expanded |= np.roll(expanded, 1, 1) | np.roll(expanded, -1, 1)
    return np.where(expanded, alpha, 0).astype(np.uint8)


def _transparent_crop(image: Image.Image) -> Image.Image:
    """按灰底的低饱和中亮度特征计算透明通道并收紧可见边界。"""

    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = maximum - minimum
    brightness = rgb.mean(axis=2)
    # 灰底处于低饱和、中等亮度；彩色人物、深色物件和纯白贴纸边均分别保留。
    color_alpha = np.clip((saturation - 7.0) / 22.0 * 255.0, 0, 255)
    dark_alpha = np.clip((184.0 - brightness) / 30.0 * 255.0, 0, 255)
    light_alpha = np.clip((brightness - 241.0) / 11.0 * 255.0, 0, 255)
    alpha = np.maximum.reduce((color_alpha, dark_alpha, light_alpha)).astype(np.uint8)
    alpha = _remove_small_islands(alpha)
    rgba = np.dstack((rgb.astype(np.uint8), alpha))
    result = Image.fromarray(rgba, "RGBA")
    bbox = result.getchannel("A").point(lambda value: 255 if value >= 10 else 0).getbbox()
    if bbox is None:
        raise ValueError("动作格没有提取到可见贴纸")
    return result.crop(bbox)


def _fit_square(image: Image.Image, size: int = 1024, padding: int = 38) -> Image.Image:
    """把动作等比放进统一透明正方形，避免桌宠切换时忽大忽小。"""

    available = size - padding * 2
    scale = min(available / image.width, available / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    return canvas


def extract_sheet(source: Path, output_dir: Path) -> tuple[Path, ...]:
    """拆出 36 张动作图并返回输出路径。"""

    sheet = Image.open(source).convert("RGB")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for index, name in enumerate(NAMES):
        row, column = divmod(index, 6)
        x_edges = ROW_X_EDGES[row]
        left, right = X_OVERRIDES.get(index, (x_edges[column], x_edges[column + 1]))
        left = max(0, left)
        right = min(sheet.width, right)
        top, bottom = ROW_BANDS[row]
        sticker = _transparent_crop(sheet.crop((left, top, right, bottom)))
        target = output_dir / f"{index + 1:02d}-{name}.png"
        _fit_square(sticker).save(target, optimize=True)
        outputs.append(target)
    return tuple(outputs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    for output in extract_sheet(args.source, args.output_dir):
        print(output)


if __name__ == "__main__":
    main()
