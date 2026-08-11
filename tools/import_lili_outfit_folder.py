"""Import the user's numbered Lili outfit PNGs as transparent hourly sprites.

The source folder stays untouched. Every filename must contain an hour number
from 1 through 12. The neutral connected background is removed locally, then
the complete character and props are fitted to the confirmed standing sprite.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image

from import_lili_action_folder import _normalized, _remove_connected_neutral_background


EXPECTED_HOURS = tuple(range(1, 13))


def _hour_from_name(path: Path) -> int:
    match = re.search(r"\d+", path.stem)
    if match is None:
        raise ValueError(f"文件名没有小时数字：{path.name}")
    return int(match.group())


def import_folder(source: Path, target: Path, reference_path: Path) -> None:
    numbered: dict[int, Path] = {}
    for path in source.glob("*.png"):
        hour = _hour_from_name(path)
        if hour in numbered:
            raise SystemExit(f"小时 {hour} 有两张图：{numbered[hour].name}、{path.name}")
        numbered[hour] = path
    if tuple(sorted(numbered)) != EXPECTED_HOURS:
        raise SystemExit(f"娃衣应覆盖 1–12 小时，实际为：{sorted(numbered)}")

    with Image.open(reference_path) as reference_image:
        reference = reference_image.convert("RGBA").getchannel("A").getbbox()
    if reference is None:
        raise SystemExit(f"基准图没有可见内容：{reference_path}")

    target.mkdir(parents=True, exist_ok=True)
    for hour in EXPECTED_HOURS:
        source_path = numbered[hour]
        with Image.open(source_path) as raw:
            transparent = _remove_connected_neutral_background(raw)
        output = _normalized(transparent, reference)
        output_path = target / f"{hour:02d}-hour.png"
        output.save(output_path, optimize=True)
        print(f"{hour:02d}h {source_path.name} -> {output_path.name}: {output.getchannel('A').getbbox()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--target", type=Path, default=Path("assets/pet/hourly-outfits"))
    parser.add_argument("--reference", type=Path, default=Path("assets/pet/daily-actions/01-stand.png"))
    args = parser.parse_args()
    import_folder(args.source, args.target, args.reference)


if __name__ == "__main__":
    main()
