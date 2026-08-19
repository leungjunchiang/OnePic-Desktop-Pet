"""
本模块统一定位桌面宠物在源码环境和 PyInstaller 打包环境中的资源文件。

职责范围：
- 判断当前是否运行于 PyInstaller 临时解包目录；
- 将相对资源路径解析为绝对路径；
- 对缺失资源给出明确异常，不负责加载或修改图片内容。

Agent 快速定位：
- 项目或打包资源根目录由 resource_root() 返回；
- 业务模块统一通过 resource_path() 获取素材和默认配置路径。

输入为项目相对路径，输出为已验证存在的 pathlib.Path。
本模块仅访问文件元数据，不修改文件、不访问网络。
"""

from __future__ import annotations

import sys
import json
import os
import re
from pathlib import Path

from . import __version__


def resource_root() -> Path:
    """返回源码项目根目录或 PyInstaller 解包资源根目录。"""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parents[2]


_content_root_override: Path | None = None
_active_content_cache: tuple[Path, int, Path | None] | None = None


def _release_version(value: object) -> tuple[int, ...] | None:
    """Return a comparable release tuple for normal vX.Y.Z content versions."""

    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))(?:\.(\d+))?", str(value or "").strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def _overlay_is_stale(payload: object) -> bool:
    """Never let an older release overlay hide newer bundled resources."""

    if not isinstance(payload, dict):
        return False
    active_version = _release_version(payload.get("content_version"))
    bundled_version = _release_version(__version__)
    return bool(active_version and bundled_version and active_version < bundled_version)


def content_update_root() -> Path:
    """Return the user-writable root used by optional content patches."""

    if _content_root_override is not None:
        return _content_root_override
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    return root / "Lili" / "content_updates"


def set_content_update_root(path: Path | None) -> None:
    """Override the content root for tests or an explicitly managed install."""

    global _content_root_override, _active_content_cache
    _content_root_override = Path(path) if path is not None else None
    _active_content_cache = None


def _active_content_root() -> Path | None:
    """Resolve the active versioned overlay without trusting arbitrary paths."""

    global _active_content_cache
    root = content_update_root()
    pointer = root / "active.json"
    try:
        stamp = pointer.stat().st_mtime_ns
    except OSError:
        _active_content_cache = (root, -1, None)
        return None
    if _active_content_cache is not None and _active_content_cache[:2] == (root, stamp):
        return _active_content_cache[2]
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        if _overlay_is_stale(payload):
            _active_content_cache = (root, stamp, None)
            return None
        version_dir_name = str(payload.get("directory", ""))
        if not version_dir_name or Path(version_dir_name).name != version_dir_name:
            raise ValueError("invalid content overlay directory")
        versions = (root / "versions").resolve()
        candidate = (versions / version_dir_name).resolve()
        candidate.relative_to(versions)
        active = candidate if candidate.is_dir() else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        active = None
    _active_content_cache = (root, stamp, active)
    return active


def clear_content_overlay_cache() -> None:
    """Make a newly committed content overlay visible immediately."""

    global _active_content_cache
    _active_content_cache = None


def resource_path(relative_path: str | Path) -> Path:
    """解析并验证资源路径，缺失时抛出 FileNotFoundError。"""

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"资源路径必须是项目内相对路径：{relative_path}")
    overlay = _active_content_root()
    if overlay is not None:
        overlay_path = overlay / relative
        if overlay_path.exists():
            return overlay_path
    path = resource_root() / relative
    if not path.exists():
        raise FileNotFoundError(f"缺少应用资源：{path}")
    return path


