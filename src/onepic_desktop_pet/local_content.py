"""读取用户只保存在本机的歌词文本与“巴布达”音频变体。

本模块不访问网络，也不会复制或上传用户文件。歌词只读取有限大小的逐行文本；音频变体只在
所选文件的同一目录内查找，供桌宠按顺序轮换播放。
"""

from __future__ import annotations

from pathlib import Path


_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".ogg"}


def load_local_lines(path_text: str, *, limit: int = 500) -> tuple[str, ...]:
    """安全读取用户自选 TXT 的非空短行；文件无效时返回空元组。"""

    if not path_text.strip():
        return ()
    path = Path(path_text).expanduser()
    try:
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            return ()
        raw = path.read_bytes()
    except OSError:
        return ()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ()
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.replace("\x00", "").strip().split())[:120]
        if line and line not in lines:
            lines.append(line)
        if len(lines) >= max(1, limit):
            break
    return tuple(lines)


def find_audio_variants(path_text: str) -> tuple[Path, ...]:
    """返回所选音频及同目录的编号变体，例如 babuda-1.wav 到 babuda-4.wav。"""

    if not path_text.strip():
        return ()
    selected = Path(path_text).expanduser()
    try:
        if not selected.is_file() or selected.suffix.casefold() not in _AUDIO_SUFFIXES:
            return ()
        stem = selected.stem
        prefix = stem.rsplit("-", 1)[0] if stem.rsplit("-", 1)[-1].isdigit() else stem
        siblings = sorted(
            path
            for path in selected.parent.iterdir()
            if path.is_file()
            and path.suffix.casefold() in _AUDIO_SUFFIXES
            and (path.stem == prefix or path.stem.startswith(f"{prefix}-"))
        )
    except OSError:
        return (selected,)
    return tuple([selected, *(path for path in siblings if path != selected)])
