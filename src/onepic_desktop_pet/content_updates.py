"""小体积内容补丁更新。

内容补丁只覆盖 ``assets/``、``config/`` 和 ``resources/``，不覆盖正在运行的
Python/EXE，也不触碰用户设置、任务、聊天记录或登录凭据。清单通过 HTTPS
取得，文件下载后先验 SHA-256，再写入版本目录，最后用一个原子替换的指针
切换整套内容，因此中途断网不会留下半套正在使用的资源。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import __version__
from .resources import (
    clear_content_overlay_cache,
    content_update_root,
    resource_path,
)


DEFAULT_MANIFEST_URL = (
    "https://github.com/leungjunchiang/OnePic-Desktop-Pet/"
    "releases/latest/download/content-manifest.json"
)
_ALLOWED_PREFIXES = ("assets/", "config/", "resources/")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_FILE_BYTES = 128 * 1024 * 1024


class ContentUpdateError(RuntimeError):
    """Raised when a content manifest or file fails validation."""


def _version_key(value: str) -> tuple[int, ...]:
    numbers = tuple(int(item) for item in re.findall(r"\d+", str(value or "")))
    return numbers or (0,)


def _safe_relative_path(value: object) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or raw.startswith("/"):
        raise ContentUpdateError(f"不安全的内容路径：{raw!r}")
    normalized = str(path)
    if not normalized.startswith(_ALLOWED_PREFIXES):
        raise ContentUpdateError(f"不允许在线覆盖的内容路径：{normalized!r}")
    return normalized


@dataclass(frozen=True)
class ContentFile:
    path: str
    url: str
    sha256: str
    size: int = 0

    @classmethod
    def from_mapping(cls, value: object) -> "ContentFile":
        if not isinstance(value, dict):
            raise ContentUpdateError("内容清单文件项必须是对象")
        path = _safe_relative_path(value.get("path"))
        url = str(value.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"https", "http", "file"} or not parsed.netloc and parsed.scheme != "file":
            raise ContentUpdateError(f"内容下载地址无效：{url!r}")
        digest = str(value.get("sha256") or "").strip().casefold()
        if not _SHA256.fullmatch(digest):
            raise ContentUpdateError(f"SHA-256 无效：{path}")
        try:
            size = max(0, int(value.get("size", 0)))
        except (TypeError, ValueError):
            size = 0
        return cls(path=path, url=url, sha256=digest, size=size)


@dataclass(frozen=True)
class ContentManifest:
    content_version: str
    files: tuple[ContentFile, ...]
    min_app_version: str = "0.0.0"

    @classmethod
    def from_mapping(cls, value: object) -> "ContentManifest":
        if not isinstance(value, dict):
            raise ContentUpdateError("不支持的内容清单版本")
        try:
            schema_version = int(value.get("schema_version", 1))
        except (TypeError, ValueError) as exc:
            raise ContentUpdateError("不支持的内容清单版本") from exc
        if schema_version != 1:
            raise ContentUpdateError("不支持的内容清单版本")
        version = str(value.get("content_version") or "").strip()
        if not version:
            raise ContentUpdateError("内容清单缺少版本号")
        raw_files = value.get("files")
        if not isinstance(raw_files, list):
            raise ContentUpdateError("内容清单缺少 files 数组")
        files = tuple(ContentFile.from_mapping(item) for item in raw_files)
        paths = [item.path for item in files]
        if len(paths) != len(set(paths)):
            raise ContentUpdateError("内容清单包含重复路径")
        return cls(
            content_version=version,
            files=files,
            min_app_version=str(value.get("min_app_version") or "0.0.0"),
        )


@dataclass(frozen=True)
class ContentUpdateResult:
    content_version: str
    updated_files: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ContentUpdateManager:
    """Fetch, compare and atomically apply content-only updates."""

    def __init__(
        self,
        *,
        manifest_url: str = DEFAULT_MANIFEST_URL,
        app_version: str = __version__,
        update_root: Path | None = None,
        timeout: float = 5.0,
        opener: Callable[..., object] | None = None,
        allow_local_files: bool = False,
    ) -> None:
        self.manifest_url = str(manifest_url or "").strip()
        self.app_version = str(app_version or "0.0.0")
        self.update_root = Path(update_root) if update_root is not None else content_update_root()
        self.timeout = max(1.0, float(timeout))
        self._opener = opener or urllib.request.urlopen
        self.allow_local_files = allow_local_files

    def _read_url(self, url: str) -> bytes:
        scheme = urllib.parse.urlparse(url).scheme
        if scheme == "http" and not self.allow_local_files:
            raise ContentUpdateError("内容更新只允许 HTTPS")
        if scheme == "file" and not self.allow_local_files:
            raise ContentUpdateError("本地文件地址只允许测试使用")
        try:
            response = self._opener(url, timeout=self.timeout)
            with response:
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _MAX_FILE_BYTES:
                        raise ContentUpdateError("内容补丁文件过大")
                    chunks.append(chunk)
                return b"".join(chunks)
        except ContentUpdateError:
            raise
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ContentUpdateError(f"内容更新网络请求失败：{exc}") from exc

    def fetch_manifest(self) -> ContentManifest | None:
        if not self.manifest_url:
            return None
        raw = self._read_url(self.manifest_url)
        try:
            return ContentManifest.from_mapping(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ContentUpdateError(f"内容清单格式无效：{exc}") from exc

    def _current_version(self) -> str:
        pointer = self.update_root / "active.json"
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            return str(payload.get("content_version") or self.app_version)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return self.app_version

    def changed_files(self, manifest: ContentManifest) -> tuple[ContentFile, ...]:
        changed: list[ContentFile] = []
        for item in manifest.files:
            try:
                local = resource_path(item.path)
                if local.is_file() and sha256_file(local) == item.sha256:
                    continue
            except (OSError, ValueError, FileNotFoundError):
                pass
            changed.append(item)
        return tuple(changed)

    def _prepare_stage(self, manifest: ContentManifest, changed: tuple[ContentFile, ...]) -> Path:
        self.update_root.mkdir(parents=True, exist_ok=True)
        staging_root = self.update_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="content-", dir=staging_root))
        changed_by_path = {item.path: item for item in changed}
        try:
            for item in manifest.files:
                target = stage / item.path
                target.parent.mkdir(parents=True, exist_ok=True)
                if item.path in changed_by_path:
                    data = self._read_url(item.url)
                    if item.size and len(data) != item.size:
                        raise ContentUpdateError(f"内容大小不匹配：{item.path}")
                    if hashlib.sha256(data).hexdigest() != item.sha256:
                        raise ContentUpdateError(f"内容校验失败：{item.path}")
                    target.write_bytes(data)
                    continue
                try:
                    source = resource_path(item.path)
                    shutil.copy2(source, target)
                except (OSError, FileNotFoundError, ValueError) as exc:
                    raise ContentUpdateError(f"无法准备未变化内容：{item.path}") from exc
            return stage
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def apply(self, manifest: ContentManifest) -> ContentUpdateResult:
        if _version_key(manifest.min_app_version) > _version_key(self.app_version):
            raise ContentUpdateError("当前程序版本过旧，不能直接加载这批内容")
        if _version_key(manifest.content_version) < _version_key(self._current_version()):
            raise ContentUpdateError("拒绝回退到更旧的内容版本")
        changed = self.changed_files(manifest)
        stage = self._prepare_stage(manifest, changed)
        versions = self.update_root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        directory = f"{manifest.content_version}-{os.getpid()}"
        final_dir = versions / directory
        if final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        os.replace(stage, final_dir)
        pointer = self.update_root / "active.json"
        temporary = self.update_root / "active.json.tmp"
        temporary.write_text(
            json.dumps(
                {"content_version": manifest.content_version, "directory": directory},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, pointer)
        if self.update_root == content_update_root():
            clear_content_overlay_cache()
        return ContentUpdateResult(manifest.content_version, tuple(item.path for item in changed))

    def check_and_apply(self) -> ContentUpdateResult | None:
        manifest = self.fetch_manifest()
        if manifest is None:
            return None
        changed = self.changed_files(manifest)
        if not changed and _version_key(manifest.content_version) <= _version_key(self._current_version()):
            return None
        return self.apply(manifest)


def reload_runtime_content() -> None:
    """Clear local knowledge caches so a successful patch can be used now."""

    from . import ai
    from . import knowledge_manager, song_knowledge, story_trigger_engine

    ai.LIUMAO_PERSONA = ai._load_short_persona()
    knowledge_manager.get_knowledge_manager.cache_clear()
    song_knowledge._load_public_cards.cache_clear()
    story_trigger_engine._DEFAULT_ENGINE = None
