"""Safe full-program update checks and downloads.

Content patches can be applied while Lili is running.  A program update is
different: the running executable must be closed before the installer can
replace it.  This module only discovers a release, downloads the matching
official GitHub asset, and verifies its SHA-256 sidecar.  Qt owns the user
confirmation and installer launch in ``app.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from . import __version__


LOGGER = logging.getLogger(__name__)


DEFAULT_RELEASES_URL = (
    "https://api.github.com/repos/leungjunchiang/OnePic-Desktop-Pet/releases/latest"
)
_VERSION_PATTERN = re.compile(r"\d+")
_SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256_PATTERN = re.compile(r"\b([0-9a-fA-F]{64})\b")
_TRUSTED_API_HOSTS = {"api.github.com"}
_MAX_METADATA_BYTES = 5 * 1024 * 1024
_MAX_INSTALLER_BYTES = 512 * 1024 * 1024


def _is_github_host(host: str) -> bool:
    clean = str(host or "").casefold().rstrip(".")
    return clean == "github.com" or clean.endswith(".github.com") or clean.endswith(".githubusercontent.com")


class ProgramUpdateError(RuntimeError):
    """Raised when a program release cannot be safely checked or verified."""


class NoProgramRelease(ProgramUpdateError):
    """Raised when GitHub has no public stable Release to inspect."""


class UpdateState(str, Enum):
    """Visible lifecycle states shared by the program update UI."""

    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    DOWNLOADING = "downloading"
    READY_TO_INSTALL = "ready_to_install"
    INSTALLING = "installing"
    ERROR = "error"


def version_key(value: str) -> tuple[int, ...]:
    numbers = tuple(int(item) for item in _VERSION_PATTERN.findall(str(value or "")))
    return numbers or (0,)


def _asset_name() -> str | None:
    if sys.platform == "win32":
        return "Lili-Windows-x64-Setup.exe"
    if sys.platform == "darwin":
        machine = platform.machine().casefold()
        return "Lili-macOS-arm64-unsigned.dmg" if machine in {"arm64", "aarch64"} else "Lili-macOS-x64-unsigned.dmg"
    return None


@dataclass(frozen=True)
class ProgramRelease:
    version: str
    tag_name: str
    release_url: str
    asset_name: str
    asset_url: str
    asset_size: int
    checksum_url: str | None
    checksum_value: str | None
    release_notes: str = ""


@dataclass(frozen=True)
class ProgramUpdateCheckResult:
    """The result of a metadata-only check, including the no-update case."""

    current_version: str
    latest_version: str
    release: ProgramRelease | None
    # ``no_release`` is distinct from ``up_to_date`` so the UI never turns a
    # missing GitHub Release into a misleading "already latest" message.
    status: str = "ok"

    @property
    def update_available(self) -> bool:
        return self.release is not None


@dataclass(frozen=True)
class ProgramUpdateResult:
    release: ProgramRelease
    installer_path: Path


class ProgramUpdateManager:
    """Check GitHub Releases and download one platform-specific installer."""

    def __init__(
        self,
        *,
        releases_url: str = DEFAULT_RELEASES_URL,
        app_version: str = __version__,
        timeout: float = 8.0,
        opener: Callable[..., object] | None = None,
        download_root: Path | None = None,
    ) -> None:
        self.releases_url = str(releases_url or "").strip()
        self.app_version = str(app_version or "0.0.0")
        self.timeout = max(1.0, float(timeout))
        self._opener = opener or urllib.request.urlopen
        self.download_root = Path(download_root) if download_root is not None else Path(tempfile.gettempdir()) / "Lili" / "updates"

    def _read_url(self, url: str, *, max_bytes: int) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ProgramUpdateError("程序更新只允许 HTTPS")
        if parsed.netloc.casefold() not in _TRUSTED_API_HOSTS and not _is_github_host(parsed.netloc):
            raise ProgramUpdateError("程序更新地址不是受信任的 GitHub 地址")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Lili-Desktop-Pet"})
            response = self._opener(request, timeout=self.timeout)
            with response:
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ProgramUpdateError("更新文件超过安全大小限制")
                    chunks.append(chunk)
                return b"".join(chunks)
        except ProgramUpdateError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NoProgramRelease("GitHub 暂未找到可用的程序 Release") from exc
            raise ProgramUpdateError(f"程序更新网络请求失败：HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ProgramUpdateError(f"程序更新网络请求失败：{exc}") from exc

    def check_latest(self) -> ProgramUpdateCheckResult:
        """Return current/latest versions and an optional verified asset plan."""

        asset_name = _asset_name()
        LOGGER.info(
            "[Update] querying GitHub Releases url=%s current=%s asset=%s",
            self.releases_url,
            self.app_version,
            asset_name,
        )
        if not asset_name or not self.releases_url:
            current = str(self.app_version).removeprefix("v")
            return ProgramUpdateCheckResult(current, current, None, status="no_release")
        current_version = str(self.app_version).removeprefix("v")
        try:
            raw = self._read_url(self.releases_url, max_bytes=_MAX_METADATA_BYTES)
        except NoProgramRelease:
            return ProgramUpdateCheckResult(
                current_version,
                current_version,
                None,
                status="no_release",
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgramUpdateError(f"程序更新信息格式无效：{exc}") from exc
        if not isinstance(payload, dict):
            raise ProgramUpdateError("程序更新信息格式无效：Release 不是对象")
        if bool(payload.get("draft")) or bool(payload.get("prerelease")):
            return ProgramUpdateCheckResult(
                current_version,
                current_version,
                None,
                status="no_release",
            )
        tag_name = str(payload.get("tag_name") or "").strip()
        version = tag_name.removeprefix("v")
        if not tag_name or not version:
            raise ProgramUpdateError("程序更新信息格式无效：缺少 tag_name")
        if not _SEMVER_PATTERN.fullmatch(version):
            raise ProgramUpdateError("程序更新信息格式无效：版本号格式无法识别")
        if version_key(version) <= version_key(current_version):
            LOGGER.info("[Update] latest release=%s result=UP_TO_DATE", version)
            return ProgramUpdateCheckResult(current_version, version, None)
        release_url = str(payload.get("html_url") or "").strip()
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise ProgramUpdateError("程序更新缺少安装包列表")
        asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == asset_name), None)
        if not isinstance(asset, dict):
            raise ProgramUpdateError(f"当前平台没有可用安装包：{asset_name}")
        asset_url = str(asset.get("browser_download_url") or "").strip()
        if not asset_url:
            raise ProgramUpdateError("安装包下载地址为空")
        checksum_name = f"{asset_name}.sha256"
        checksum_asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == checksum_name), None)
        checksum_url = str(checksum_asset.get("browser_download_url") or "").strip() if isinstance(checksum_asset, dict) else None
        checksum_value = str(asset.get("digest") or "").removeprefix("sha256:").casefold() or None
        release = ProgramRelease(
            version=version,
            tag_name=tag_name,
            release_url=release_url,
            asset_name=asset_name,
            asset_url=asset_url,
            asset_size=max(0, int(asset.get("size") or 0)),
            checksum_url=checksum_url,
            checksum_value=checksum_value if _SHA256_PATTERN.fullmatch(checksum_value or "") else None,
            release_notes=str(payload.get("body") or "").strip(),
        )
        LOGGER.info("[Update] latest release=%s result=UPDATE_AVAILABLE", version)
        return ProgramUpdateCheckResult(current_version, version, release)

    def fetch_latest(self) -> ProgramRelease | None:
        """Backward-compatible helper returning only a newer release."""

        return self.check_latest().release

    def _download(self, url: str, destination: Path, *, max_bytes: int) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not _is_github_host(parsed.netloc):
            raise ProgramUpdateError("安装包地址不是受信任的 GitHub 地址")
        request = urllib.request.Request(url, headers={"User-Agent": "Lili-Desktop-Pet"})
        try:
            response = self._opener(request, timeout=self.timeout)
            with response, destination.open("wb") as handle:
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ProgramUpdateError("安装包超过安全大小限制")
                    handle.write(chunk)
        except ProgramUpdateError:
            raise
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ProgramUpdateError(f"安装包下载失败：{exc}") from exc

    @staticmethod
    def _parse_checksum(raw: bytes, asset_name: str) -> str:
        text = raw.decode("utf-8", errors="replace")
        match = _SHA256_PATTERN.search(text)
        if not match:
            raise ProgramUpdateError(f"找不到安装包校验值：{asset_name}")
        return match.group(1).casefold()

    def download_and_verify(self, release: ProgramRelease) -> ProgramUpdateResult:
        self.download_root.mkdir(parents=True, exist_ok=True)
        safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", release.tag_name).strip("._") or "release"
        final_path = self.download_root / f"{safe_tag}-{release.asset_name}"
        partial_path = final_path.with_suffix(final_path.suffix + ".part")
        partial_path.unlink(missing_ok=True)
        self._download(release.asset_url, partial_path, max_bytes=_MAX_INSTALLER_BYTES)
        expected = release.checksum_value
        if release.checksum_url:
            checksum_path = partial_path.with_suffix(partial_path.suffix + ".sha256")
            self._download(release.checksum_url, checksum_path, max_bytes=4096)
            expected = self._parse_checksum(checksum_path.read_bytes(), release.asset_name)
            checksum_path.unlink(missing_ok=True)
        if not expected:
            partial_path.unlink(missing_ok=True)
            raise ProgramUpdateError("Release 没有提供安装包 SHA-256，已拒绝安装")
        digest = hashlib.sha256(partial_path.read_bytes()).hexdigest()
        if digest != expected:
            partial_path.unlink(missing_ok=True)
            raise ProgramUpdateError("安装包校验失败，已拒绝启动")
        partial_path.replace(final_path)
        return ProgramUpdateResult(release=release, installer_path=final_path)
