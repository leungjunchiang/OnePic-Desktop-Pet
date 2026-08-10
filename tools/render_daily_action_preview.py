"""把 36 张每日动作素材渲染为联系表，供透明边缘与角色一致性验收。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def render(source_dir: Path, output: Path) -> None:
    files = sorted(source_dir.glob("*.png"))
    cell = 230
    canvas = Image.new("RGB", (cell * 6, cell * 6), "#e8edf2")
    draw = ImageDraw.Draw(canvas)
    for index, path in enumerate(files):
        row, column = divmod(index, 6)
        sprite = Image.open(path).convert("RGBA")
        sprite.thumbnail((198, 198), Image.Resampling.LANCZOS)
        x = column * cell + (cell - sprite.width) // 2
        y = row * cell + 22
        canvas.paste(sprite, (x, y), sprite)
        draw.text((column * cell + 8, row * cell + 6), path.stem, fill="#263442")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, optimize=True)


if __name__ == "__main__":
    render(Path("assets/pet/daily-actions"), Path("build/visual/lili-v10-daily-actions.png"))
