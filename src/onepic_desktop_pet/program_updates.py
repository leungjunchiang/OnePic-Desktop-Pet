"""安全下载程序更新，支持续传、第三方备用线路及官方 SHA-256 校验。

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
from time import monotonic, sleep
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from . import __version__
from .tls_support import verified_ssl_context


LOGGER = logging.getLogger(__name__)


DEFAULT_RELEASES_URL = (
    "https://api.github.com/repos/leungjunchiang/OnePic-Desktop-Pet/releases/latest"
)
DEFAULT_RELEASE_PAGE_URL = (
    "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/latest"
)
_VERSION_PATTERN = re.compile(r"\d+")
# GitHub release tags in this project use zero-padded components as well
# (for example, v0.23.00).  Compare numeric components with version_key()
# below, but accept both padded and ordinary SemVer-shaped release tags.
_SEMVER_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256_PATTERN = re.compile(r"\b([0-9a-fA-F]{64})\b")
_TRUSTED_API_HOSTS = {"api.github.com"}
_MAX_METADATA_BYTES = 5 * 1024 * 1024
_MAX_INSTALLER_BYTES = 512 * 1024 * 1024
_OPEN_ATTEMPTS = 3
_OPEN_RETRY_DELAY_SECONDS = 0.15
_DEFAULT_MIRRORS = ("https://ghfast.top",)
_SLOW_CHECK_SECONDS = 12.0
_SLOW_BYTES_PER_SECOND = 128 * 1024


def _is_github_host(host: str) -> bool:
    clean = str(host or "").casefold().rstrip(".")
    return clean == "github.com" or clean.endswith(".github.com") or clean.endswith(".githubusercontent.com")


class ProgramUpdateError(RuntimeError):
    """Raised when a program release cannot be safely checked or verified."""


class NoProgramRelease(ProgramUpdateError):
    """Raised when GitHub has no public stable Release to inspect."""


class GitHubApiRateLimited(ProgramUpdateError):
    """Raised when the unauthenticated GitHub API refuses another request."""


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
    release_notes: str = field(default="", repr=False)


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
        release_page_url: str | None = None,
        app_version: str = __version__,
        timeout: float = 8.0,
        cache_seconds: float = 300.0,
        opener: Callable[..., object] | None = None,
        download_root: Path | None = None,
        mirror_bases: tuple[str, ...] = _DEFAULT_MIRRORS,
    ) -> None:
        self.releases_url = str(releases_url or "").strip()
        self.release_page_url = str(release_page_url or "").strip() or self._derive_release_page_url(self.releases_url)
        self.app_version = str(app_version or "0.0.0")
        self.timeout = max(1.0, float(timeout))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._opener = opener or urllib.request.urlopen
        self._uses_verified_default_opener = opener is None
        self.download_root = Path(download_root) if download_root is not None else Path(tempfile.gettempdir()) / "Lili" / "updates"
        self.mirror_bases = tuple(mirror_bases)
        self._cached_check: tuple[float, ProgramUpdateCheckResult] | None = None

    def _open(self, request: urllib.request.Request):
        """Open updater HTTPS requests with the bundled/OS verified CA set."""

        # GitHub occasionally closes an otherwise valid TLS connection while
        # Python is reading the response (notably on Windows networks and
        # proxies).  Retrying the same HTTPS request is safe here because all
        # updater requests are GETs, and avoids turning a transient
        # ``UNEXPECTED_EOF_WHILE_READING`` into a blocking update error.
        for attempt in range(1, _OPEN_ATTEMPTS + 1):
            try:
                if self._uses_verified_default_opener:
                    return self._opener(
                        request,
                        timeout=self.timeout,
                        context=verified_ssl_context(),
                    )
                # Test and embedding openers keep their existing small
                # interface.
                return self._opener(request, timeout=self.timeout)
            except urllib.error.HTTPError:
                raise
            except (OSError, urllib.error.URLError) as exc:
                if attempt >= _OPEN_ATTEMPTS:
                    raise
                LOGGER.warning(
                    "[Update] HTTPS request attempt %s/%s failed; retrying: %s",
                    attempt,
                    _OPEN_ATTEMPTS,
                    exc,
                )
                sleep(_OPEN_RETRY_DELAY_SECONDS * attempt)
        raise RuntimeError("unreachable updater retry state")

    @staticmethod
    def _derive_release_page_url(api_url: str) -> str:
        """Convert the standard GitHub Releases API URL to its web URL."""

        parsed = urllib.parse.urlparse(api_url)
        parts = [part for part in parsed.path.split("/") if part]
        try:
            repos_index = parts.index("repos")
            owner, repo = parts[repos_index + 1 : repos_index + 3]
        except (ValueError, IndexError):
            return DEFAULT_RELEASE_PAGE_URL if not api_url else ""
        if parsed.netloc.casefold() != "api.github.com":
            return ""
        return f"https://github.com/{owner}/{repo}/releases/latest"

    def _read_url(self, url: str, *, max_bytes: int) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ProgramUpdateError("程序更新只允许 HTTPS")
        if parsed.netloc.casefold() not in _TRUSTED_API_HOSTS and not _is_github_host(parsed.netloc):
            raise ProgramUpdateError("程序更新地址不是受信任的 GitHub 地址")
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Lili-Desktop-Pet",
                    "Accept": "application/vnd.github+json",
                    "Connection": "close",
                },
            )
            response = self._open(request)
            with response:
                final = urllib.parse.urlsplit(str(getattr(response, "geturl", lambda: url)() or url))
                if (final.scheme != "https" or not final.hostname or final.username or final.password
                        or not _is_github_host(final.hostname)):
                    raise ProgramUpdateError("更新信息重定向到非 GitHub 地址")
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
            if exc.code in {403, 429}:
                raise GitHubApiRateLimited(
                    f"GitHub API 请求受限：HTTP {exc.code}"
                ) from exc
            raise ProgramUpdateError(f"程序更新网络请求失败：HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ProgramUpdateError(f"程序更新网络请求失败：{exc}") from exc

    def _read_release_page_redirect(self) -> str:
        """Read only the Release page redirect, avoiding the API rate limit."""

        if not self.release_page_url:
            raise ProgramUpdateError("没有可用的 GitHub Release 网页地址")
        parsed = urllib.parse.urlparse(self.release_page_url)
        if parsed.scheme != "https" or not _is_github_host(parsed.netloc):
            raise ProgramUpdateError("程序更新网页地址不是受信任的 GitHub 地址")
        request = urllib.request.Request(
            self.release_page_url,
            headers={
                "User-Agent": "Lili-Desktop-Pet",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            response = self._open(request)
            with response:
                final_url = getattr(response, "geturl", lambda: self.release_page_url)()
                # Consume a small amount so custom urllib adapters and HTTP
                # clients finish the response cleanly.  The body is not used.
                response.read(16 * 1024)
                return str(final_url or self.release_page_url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NoProgramRelease("GitHub 暂未找到可用的程序 Release") from exc
            raise ProgramUpdateError(
                f"GitHub Release 网页请求失败：HTTP {exc.code}"
            ) from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ProgramUpdateError(f"GitHub Release 网页请求失败：{exc}") from exc

    @staticmethod
    def _release_from_redirect(
        redirect_url: str,
        *,
        current_version: str,
        asset_name: str,
    ) -> ProgramUpdateCheckResult:
        """Build a minimal verified-asset plan from /releases/latest redirect."""

        parsed = urllib.parse.urlparse(redirect_url)
        if parsed.scheme != "https" or not _is_github_host(parsed.netloc):
            raise ProgramUpdateError("GitHub Release 重定向地址不受信任")
        match = re.search(r"/releases/tag/([^/?#]+)", parsed.path)
        if not match:
            raise NoProgramRelease("GitHub Release 页面没有定位到版本标签")
        tag_name = urllib.parse.unquote(match.group(1))
        version = tag_name.removeprefix("v")
        if not _SEMVER_PATTERN.fullmatch(version):
            raise ProgramUpdateError("程序更新信息格式无效：版本号格式无法识别")
        if version_key(version) <= version_key(current_version):
            return ProgramUpdateCheckResult(current_version, version, None)

        repository_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.split('/releases/tag/')[0]}"
        encoded_tag = urllib.parse.quote(tag_name, safe="")
        encoded_asset = urllib.parse.quote(asset_name, safe="")
        release_url = f"{repository_url}/releases/tag/{encoded_tag}"
        asset_url = f"{repository_url}/releases/download/{encoded_tag}/{encoded_asset}"
        checksum_url = f"{asset_url}.sha256"
        release = ProgramRelease(
            version=version,
            tag_name=tag_name,
            release_url=release_url,
            asset_name=asset_name,
            asset_url=asset_url,
            # The page redirect does not expose the byte size.  The actual
            # download reads Content-Length and still reports progress.
            asset_size=0,
            checksum_url=checksum_url,
            checksum_value=None,
            release_notes="",
        )
        LOGGER.info("[Update] GitHub API limited; release-page fallback=%s", tag_name)
        return ProgramUpdateCheckResult(current_version, version, release)

    def _check_via_release_page(
        self,
        *,
        current_version: str,
        asset_name: str,
    ) -> ProgramUpdateCheckResult:
        redirect_url = self._read_release_page_redirect()
        return self._release_from_redirect(
            redirect_url,
            current_version=current_version,
            asset_name=asset_name,
        )

    def check_latest(self, *, force: bool = False) -> ProgramUpdateCheckResult:
        """Return current/latest versions and an optional verified asset plan.

        Automatic checks may reuse the short-lived cache, but an explicit
        user request must always ask GitHub again.  Otherwise an automatic
        check made just before a new Release is published can hide that
        Release from the manual menu for the full cache window.
        """

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
        if not force and self._cached_check is not None:
            checked_at, cached = self._cached_check
            if self.cache_seconds > 0 and monotonic() - checked_at < self.cache_seconds:
                LOGGER.info("[Update] returning cached release check result")
                return cached
        try:
            raw = self._read_url(self.releases_url, max_bytes=_MAX_METADATA_BYTES)
        except GitHubApiRateLimited:
            try:
                result = self._check_via_release_page(
                    current_version=current_version,
                    asset_name=asset_name,
                )
            except ProgramUpdateError as fallback_exc:
                raise ProgramUpdateError(
                    "GitHub API 被限制，Release 网页线路也不可用："
                    f"{fallback_exc}"
                ) from fallback_exc
            self._cached_check = (monotonic(), result)
            return result
        except NoProgramRelease:
            result = ProgramUpdateCheckResult(
                current_version,
                current_version,
                None,
                status="no_release",
            )
            self._cached_check = (monotonic(), result)
            return result
        except ProgramUpdateError as api_exc:
            try:
                result = self._check_via_release_page(
                    current_version=current_version, asset_name=asset_name)
            except ProgramUpdateError as fallback_exc:
                raise ProgramUpdateError(
                    f"GitHub 更新接口及发布页面均不可用：{fallback_exc}") from api_exc
            self._cached_check = (monotonic(), result)
            return result
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgramUpdateError(f"程序更新信息格式无效：{exc}") from exc
        if not isinstance(payload, dict):
            raise ProgramUpdateError("程序更新信息格式无效：Release 不是对象")
        if bool(payload.get("draft")) or bool(payload.get("prerelease")):
            result = ProgramUpdateCheckResult(
                current_version,
                current_version,
                None,
                status="no_release",
            )
            self._cached_check = (monotonic(), result)
            return result
        tag_name = str(payload.get("tag_name") or "").strip()
        version = tag_name.removeprefix("v")
        if not tag_name or not version:
            raise ProgramUpdateError("程序更新信息格式无效：缺少 tag_name")
        if not _SEMVER_PATTERN.fullmatch(version):
            raise ProgramUpdateError("程序更新信息格式无效：版本号格式无法识别")
        if version_key(version) <= version_key(current_version):
            LOGGER.info("[Update] latest release=%s result=UP_TO_DATE", version)
            result = ProgramUpdateCheckResult(current_version, version, None)
            self._cached_check = (monotonic(), result)
            return result
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
        result = ProgramUpdateCheckResult(current_version, version, release)
        self._cached_check = (monotonic(), result)
        return result

    def fetch_latest(self, *, force: bool = False) -> ProgramRelease | None:
        """Backward-compatible helper returning only a newer release."""

        return self.check_latest(force=force).release

    @staticmethod
    def _response_content_length(response: object) -> int:
        """Read a response size without requiring a concrete urllib class."""

        headers = getattr(response, "headers", None)
        raw: object | None = None
        if headers is not None:
            try:
                raw = headers.get("Content-Length")
            except (AttributeError, TypeError):
                raw = None
        if raw is None:
            getheader = getattr(response, "getheader", None)
            if callable(getheader):
                try:
                    raw = getheader("Content-Length")
                except (OSError, TypeError, ValueError):
                    raw = None
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return 0

    def _download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        expected_size: int = 0,
        progress: Callable[[int, int], None] | None = None,
        resume: bool = False,
        allow_mirror: bool = False,
        switch_on_slow: bool = False,
    ) -> None:
        parsed = urllib.parse.urlparse(url)
        allowed_mirror = allow_mirror and any(
            url.startswith(base.rstrip('/') + '/https://github.com/')
            for base in self.mirror_bases
        )
        if parsed.scheme != "https" or not (_is_github_host(parsed.netloc) or allowed_mirror):
            raise ProgramUpdateError("安装包地址不是受信任的 GitHub 地址")
        offset = destination.stat().st_size if resume and destination.exists() else 0
        if offset > max_bytes or (expected_size and offset > expected_size):
            destination.unlink(missing_ok=True)
            offset = 0
        headers = {"User-Agent": "Lili-Desktop-Pet", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            response = self._open(request)
            with response:
                final_url = getattr(response, "geturl", lambda: url)()
                final = urllib.parse.urlsplit(str(final_url or url))
                if final.scheme != "https" or not final.hostname or final.username or final.password:
                    raise ProgramUpdateError("下载重定向不是安全的 HTTPS 地址")
                if not allow_mirror and not _is_github_host(final.hostname):
                    raise ProgramUpdateError("官方文件重定向到非 GitHub 地址")
                status = int(getattr(response, "status", 200) or 200)
                response_headers = getattr(response, "headers", None)
                content_range = response_headers.get("Content-Range", "") if response_headers is not None else ""
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", str(content_range).strip())
                    if not match:
                        raise ProgramUpdateError("续传响应缺少有效的文件范围")
                    first, last, full_size = map(int, match.groups())
                    if first != offset or last < first or last >= full_size or full_size > max_bytes:
                        raise ProgramUpdateError("续传响应的文件范围不匹配")
                    if expected_size and full_size != expected_size:
                        raise ProgramUpdateError("续传文件大小与官方记录不一致")
                    response_length = self._response_content_length(response)
                    if response_length and response_length != last - first + 1:
                        raise ProgramUpdateError("续传数据长度不匹配")
                    total = full_size
                elif status == 200:
                    # A server may ignore Range; replace the partial file
                    # rather than appending a second copy of the installer.
                    offset = 0
                    response_length = self._response_content_length(response)
                    if expected_size and response_length and response_length != expected_size:
                        raise ProgramUpdateError("安装包大小与官方记录不一致")
                    total = expected_size or response_length
                else:
                    raise ProgramUpdateError(f"安装包下载返回异常状态：{status}")
                if progress is not None:
                    progress(offset, total)
                size = offset
                started = monotonic()
                with destination.open("ab" if offset else "wb") as handle:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes or (total and size > total):
                            raise ProgramUpdateError("安装包超过预期大小")
                        handle.write(chunk)
                        if progress is not None:
                            progress(size, total)
                        elapsed = monotonic() - started
                        if (switch_on_slow and elapsed >= _SLOW_CHECK_SECONDS
                                and (size - offset) / max(elapsed, 0.001) < _SLOW_BYTES_PER_SECOND
                                and (not total or size < total)):
                            raise ProgramUpdateError("官方下载速度较慢，尝试备用线路")
                if total and size != total:
                    raise ProgramUpdateError("下载未完成，请重试以继续下载")
        except ProgramUpdateError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:
                destination.unlink(missing_ok=True)
                return self._download(url, destination, max_bytes=max_bytes,
                                      expected_size=expected_size, progress=progress,
                                      resume=False, allow_mirror=allow_mirror,
                                      switch_on_slow=switch_on_slow)
            raise ProgramUpdateError(f"安装包下载失败：HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ProgramUpdateError(f"安装包下载失败：{exc}") from exc

    @staticmethod
    def _parse_checksum(raw: bytes, asset_name: str) -> str:
        text = raw.decode("utf-8", errors="replace")
        match = _SHA256_PATTERN.search(text)
        if not match:
            raise ProgramUpdateError(f"找不到安装包校验值：{asset_name}")
        return match.group(1).casefold()

    def _cleanup_previous_installers(self, keep_path: Path, asset_name: str) -> None:
        """Remove stale installer files left by earlier update downloads.

        The updater owns only its application-specific temporary directory.
        Restrict deletion to files ending in the exact platform installer name
        (or its interrupted-download suffix), so unrelated user files in the
        directory are never touched.  Cleanup is best effort: a locked old
        installer must not turn an already verified update into a failure.
        """

        installer_suffixes = (
            f"-{asset_name}",
            f"-{asset_name}.part",
            f"-{asset_name}.part.json",
        )
        try:
            candidates = tuple(self.download_root.iterdir())
        except OSError as exc:
            LOGGER.warning("[Update] old installer cleanup skipped: %s", exc)
            return

        for path in candidates:
            if (
                path == keep_path
                or not path.is_file()
                or not path.name.endswith(installer_suffixes)
            ):
                continue
            try:
                path.unlink()
            except OSError as exc:
                LOGGER.warning("[Update] could not delete old installer %s: %s", path, exc)
            else:
                LOGGER.info("[Update] removed old installer %s", path)

    def download_and_verify(
        self,
        release: ProgramRelease,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> ProgramUpdateResult:
        self.download_root.mkdir(parents=True, exist_ok=True)
        safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", release.tag_name).strip("._") or "release"
        final_path = self.download_root / f"{safe_tag}-{release.asset_name}"
        partial_path = final_path.with_suffix(final_path.suffix + ".part")
        metadata_path = partial_path.with_suffix(partial_path.suffix + ".json")
        expected = release.checksum_value
        if not expected and release.checksum_url:
            checksum_path = partial_path.with_suffix(partial_path.suffix + ".sha256")
            self._download(release.checksum_url, checksum_path, max_bytes=4096)
            expected = self._parse_checksum(checksum_path.read_bytes(), release.asset_name)
            checksum_path.unlink(missing_ok=True)
        if not expected or not _SHA256_PATTERN.fullmatch(expected):
            raise ProgramUpdateError("Release 没有提供安装包 SHA-256，已拒绝安装")
        expected = expected.casefold()
        identity = {"url": release.asset_url, "size": release.asset_size, "sha256": expected}
        try:
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            previous = None
        if previous != identity:
            partial_path.unlink(missing_ok=True)
        metadata_tmp = metadata_path.with_suffix(metadata_path.suffix + '.tmp')
        metadata_tmp.write_text(json.dumps(identity, ensure_ascii=True), encoding="utf-8")
        metadata_tmp.replace(metadata_path)
        complete = partial_path.exists() and self._file_sha256(partial_path) == expected
        if partial_path.exists() and release.asset_size and partial_path.stat().st_size >= release.asset_size and not complete:
            partial_path.unlink(missing_ok=True)
        if not complete:
            mirrors = self._mirror_urls(release)
            try:
                self._download(release.asset_url, partial_path, max_bytes=_MAX_INSTALLER_BYTES,
                               expected_size=release.asset_size, progress=progress, resume=True,
                               switch_on_slow=bool(mirrors))
            except ProgramUpdateError as official_error:
                if not mirrors:
                    raise
                LOGGER.warning("[Update] official download unavailable or slow; trying fallback")
                last_error = official_error
                for mirror in mirrors:
                    try:
                        self._download(mirror, partial_path, max_bytes=_MAX_INSTALLER_BYTES,
                                       expected_size=release.asset_size, progress=progress,
                                       resume=True, allow_mirror=True)
                        break
                    except ProgramUpdateError as mirror_error:
                        last_error = mirror_error
                else:
                    # A failed accelerator must not prevent the user from
                    # continuing on a slow but functional official route.
                    try:
                        self._download(release.asset_url, partial_path, max_bytes=_MAX_INSTALLER_BYTES,
                                       expected_size=release.asset_size, progress=progress, resume=True)
                    except ProgramUpdateError as exc:
                        raise ProgramUpdateError(f"官方与备用线路均不可用：{last_error}") from exc
        digest = self._file_sha256(partial_path)
        if digest != expected:
            partial_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            raise ProgramUpdateError("安装包校验失败，已拒绝启动")
        partial_path.replace(final_path)
        metadata_path.unlink(missing_ok=True)
        self._cleanup_previous_installers(final_path, release.asset_name)
        return ProgramUpdateResult(release=release, installer_path=final_path)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _mirror_urls(self, release: ProgramRelease) -> list[str]:
        """Only mirror this project's exact immutable GitHub release asset."""
        parsed = urllib.parse.urlsplit(release.asset_url)
        expected_path = ('/leungjunchiang/OnePic-Desktop-Pet/releases/download/'
                         + urllib.parse.quote(release.tag_name, safe='') + '/'
                         + urllib.parse.quote(release.asset_name, safe=''))
        if (parsed.scheme != 'https' or parsed.netloc.casefold() != 'github.com'
                or parsed.path != expected_path or parsed.query or parsed.fragment):
            return []
        urls = []
        for base in self.mirror_bases:
            mirror = urllib.parse.urlsplit(base)
            if (mirror.scheme == 'https' and mirror.hostname and not mirror.username
                    and not mirror.password and not mirror.query and not mirror.fragment
                    and mirror.path in ('', '/')):
                urls.append(base.rstrip('/') + '/' + release.asset_url)
        return urls
