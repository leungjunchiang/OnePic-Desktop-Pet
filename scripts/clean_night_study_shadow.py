"""基于主体核心与外扩轮廓清理夜间加班 PNG 的外部软阴影。

这个脚本不会覆盖原始素材。它保留核心主体及其抗锯齿边缘，只清理距离主体
核心过远的低 alpha 外围像素，并把结果写成新的 RGBA PNG，便于在白底、黑底
和桌面背景上复核。
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter


def remove_baked_checkerboard(
    image: Image.Image,
    *,
    bounds: tuple[int, int, int, int] = (64, 79, 960, 975),
) -> None:
    """只清除与画布边缘连通的白色棋盘格，不误删主体内部白色细节。

    这张素材的“透明背景”实际是透明格与白色不透明格交错的生成图棋盘。
    棋盘所在区域的透明像素和近白像素组成候选背景，再从边缘做 8 邻域
    flood fill；被主体轮廓包住的白纸、灯光等不会因为颜色接近白色而被清除。
    """

    rgba = image.load()
    width, height = image.size
    left, top, right, bottom = bounds
    candidate = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            r, g, b, alpha = rgba[x, y]
            is_transparent = alpha == 0
            in_grid = left <= x < right and top <= y < bottom
            # 不只匹配整块白格，也纳入它的抗锯齿边缘；这样棋盘格不会
            # 在黑底预览里留下零散白色短线。主体内部的白色区域若被黑色
            # 轮廓包住，不会从画布边缘 flood fill 到达。
            is_background_white = (
                in_grid and alpha > 0 and r >= 245 and g >= 245 and b >= 245
            )
            if is_transparent or is_background_white:
                candidate[y * width + x] = 1

    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if candidate[x]:
            queue.append((x, 0))
        index = (height - 1) * width + x
        if candidate[index]:
            queue.append((x, height - 1))
    for y in range(height):
        if candidate[y * width]:
            queue.append((0, y))
        index = y * width + width - 1
        if candidate[index]:
            queue.append((width - 1, y))

    visited = bytearray(width * height)
    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if visited[index] or not candidate[index]:
            continue
        visited[index] = 1
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = ny * width + nx
                    if candidate[neighbor] and not visited[neighbor]:
                        queue.append((nx, ny))

    for y in range(top, min(bottom, height)):
        for x in range(left, min(right, width)):
            index = y * width + x
            r, g, b, alpha = rgba[x, y]
            if (
                visited[index]
                and candidate[index]
                and alpha > 0
                and r >= 245
                and g >= 245
                and b >= 245
            ):
                rgba[x, y] = (r, g, b, 0)


def clean_external_shadow(
    source: Path,
    destination: Path,
    *,
    core_alpha: int = 80,
    contour_radius: int = 8,
) -> None:
    """清理主体外部的软阴影，同时保留主体边缘的原始 alpha。"""

    image = Image.open(source).convert("RGBA")
    remove_baked_checkerboard(image)
    alpha = image.getchannel("A")
    # 高 alpha 区域代表主体核心；MaxFilter 只用于形成主体外扩轮廓，
    # 不会重写主体颜色或把所有半透明像素一刀切掉。
    core = alpha.point(lambda value: 255 if value >= core_alpha else 0)
    size = contour_radius * 2 + 1
    expanded_core = core.filter(ImageFilter.MaxFilter(size))

    outside = expanded_core.point(lambda value: 0 if value else 255)
    # 外扩轮廓之外的像素属于外围投影/雾边；轮廓内的低 alpha 像素继续
    # 保留，从而不破坏主体边缘抗锯齿和内部柔和明暗。
    cleaned_alpha = Image.composite(Image.new("L", alpha.size, 0), alpha, outside)
    image.putalpha(cleaned_alpha)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG", optimize=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--core-alpha", type=int, default=80)
    parser.add_argument("--contour-radius", type=int, default=8)
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    clean_external_shadow(
        args.source,
        args.destination,
        core_alpha=max(1, min(255, args.core_alpha)),
        contour_radius=max(1, min(32, args.contour_radius)),
    )
