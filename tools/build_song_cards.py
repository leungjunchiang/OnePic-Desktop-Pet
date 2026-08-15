"""从用户本地歌词 TXT 生成不含歌词正文的公开作品卡。

用法：
    python tools/build_song_cards.py --source "C:\\Users\\...\\陈楚生歌词.txt"

输出只包含歌名、标签和六毛使用建议，不会把任何歌词行写入输出文件。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from onepic_desktop_pet.song_knowledge import make_public_card, parse_local_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("resources/chen_chusheng_song_cards.json"),
    )
    args = parser.parse_args()
    entries = parse_local_catalog(args.source)
    if not entries:
        raise SystemExit("没有读到可用的本地歌曲目录，未写入输出文件。")
    cards = [make_public_card(title, artist, body) for title, artist, body in entries]
    payload = {
        "schema_version": 1,
        "artist": "陈楚生",
        "source_policy": "由用户本地歌词目录抽取；公开文件不包含歌词正文或逐句索引。",
        "songs": [card.to_mapping() for card in cards],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(cards)} public song cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

