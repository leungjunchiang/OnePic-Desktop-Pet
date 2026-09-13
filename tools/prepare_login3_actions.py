"""规范化三日连登娃衣的完整动作素材。

输入是仓库所有者提供的白底 PNG 动作图。脚本只处理派生文件：用边缘
洪水填充去掉白色底图，按桌宠公共画布缩放到 560×500 RGBA，再以最高
PNG 压缩写入 ``assets/pet/login-rewards/actions``。原始图片不会被修改。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


CANVAS_SIZE = (560, 500)
SOURCE_TO_OUTPUT = {
    "你好.png": "hello.png",
    "打电话.png": "phone.png",
    "工作打电脑.png": "computer.png",
    "喝奶茶.png": "milk-tea.png",
    "加油.png": "encourage.png",
    "看书.png": "book.png",
    "来消息了.png": "message.png",
    "比心.png": "love.png",
    "跑起来.png": "run.png",
    "刷牙.png": "brush.png",
    "睡觉.png": "sleep.png",
    "展示报表.png": "report.png",
}


def remove_white_matte(image: Image.Image) -> Image.Image:
    """从四周连通区域移除白色背景，保留白色道具和轮廓内部。"""

    rgba = image.convert("RGBA")
    # The supplied artwork has a flat white matte.  Flood-fill only from the
    # canvas boundary, so white paper, pillow, and report-board interiors are
    # protected by their black outlines and remain opaque.
    seeds = (
        (0, 0),
        (rgba.width - 1, 0),
        (0, rgba.height - 1),
        (rgba.width - 1, rgba.height - 1),
    )
    for seed in seeds:
        ImageDraw.floodfill(rgba, seed, (0, 0, 0, 0), thresh=24)
    return rgba


def normalize_action(source: Path) -> Image.Image:
    """返回与现有完整动作相同画布比例的透明 PNG 图像。"""

    with Image.open(source) as image:
        cleaned = remove_white_matte(image)
    # The source set is square.  Keep its complete composition and use the
    # same 500 px content height that _full_sprite() produces at runtime for
    # the 560×500 application canvas; this avoids clipping props or shadows.
    resized = cleaned.resize((500, 500), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((CANVAS_SIZE[0] - resized.width) // 2, 0))
    return canvas


def prepare(source_dir: Path, output_dir: Path) -> list[Path]:
    """转换全部动作并返回生成文件。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for source_name, output_name in SOURCE_TO_OUTPUT.items():
        source = source_dir / source_name
        if not source.is_file():
            raise FileNotFoundError(f"找不到 login-3 动作素材：{source}")
        output = output_dir / output_name
        normalize_action(source).save(
            output,
            "PNG",
            optimize=True,
            compress_level=9,
        )
        outputs.append(output)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="规范化 login-3 娃衣动作素材")
    parser.add_argument("source_dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "assets"
        / "pet"
        / "login-rewards"
        / "actions",
    )
    args = parser.parse_args()
    outputs = prepare(args.source_dir.resolve(), args.output.resolve())
    total = sum(path.stat().st_size for path in outputs)
    print(f"已生成 {len(outputs)} 个 login-3 动作：{args.output.resolve()}")
    print(f"派生素材总大小：{total / 1024 / 1024:.2f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
