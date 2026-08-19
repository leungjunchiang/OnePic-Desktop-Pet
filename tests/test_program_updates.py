"""Pure-Python tests for the safe full-program updater."""

from __future__ import annotations

import hashlib
import json
import urllib.error
from urllib.parse import urlparse

import pytest

from onepic_desktop_pet.program_updates import (
    ProgramUpdateError,
    ProgramUpdateManager,
    version_key,
)


class Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload)
        value = self.payload[self.offset : self.offset + size]
        self.offset += len(value)
        return value


class RedirectResponse(Response):
    def __init__(self, payload: bytes, final_url: str) -> None:
        super().__init__(payload)
        self.final_url = final_url

    def geturl(self) -> str:
        return self.final_url


def test_program_version_comparison_is_numeric() -> None:
    assert version_key("v0.22.10") > version_key("0.22.9")
    assert version_key("0.22.45") == (0, 22, 45)
def test_zero_padded_release_tag_is_accepted(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    payload = {
        "tag_name": "v0.23.00",
        "html_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/tag/v0.23.00",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "Lili-Windows-x64-Setup.exe",
                "browser_download_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.23.00/Lili-Windows-x64-Setup.exe",
                "size": 12,
            }
        ],
    }

    def opener(request, timeout=0):
        return Response(json.dumps(payload).encode("utf-8"))

    result = ProgramUpdateManager(app_version="0.22.98", opener=opener).check_latest()
    assert result.latest_version == "0.23.00"
    assert result.release is not None
    assert result.release.version == "0.23.00"



def test_default_updater_opener_uses_verified_ssl_context(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    sentinel = object()
    captured: dict[str, object] = {}

    def fake_context():
        return sentinel

    def fake_urlopen(request, timeout=0, context=None):
        captured["timeout"] = timeout
        captured["context"] = context
        return Response(b"{}")

    monkeypatch.setattr(module, "verified_ssl_context", fake_context)
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    manager = ProgramUpdateManager(app_version="0.22.82")

    manager._read_url("https://api.github.com/repos/example/releases/latest", max_bytes=1024)

    assert captured["context"] is sentinel
    assert captured["timeout"] == manager.timeout


def test_fetch_and_verify_platform_installer(tmp_path, monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    installer = b"fake installer bytes"
    digest = hashlib.sha256(installer).hexdigest()
    payload = {
        "tag_name": "v0.22.46",
        "html_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/tag/v0.22.46",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "Lili-Windows-x64-Setup.exe",
                "browser_download_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.22.46/Lili-Windows-x64-Setup.exe",
                "size": len(installer),
            },
            {
                "name": "Lili-Windows-x64-Setup.exe.sha256",
                "browser_download_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.22.46/Lili-Windows-x64-Setup.exe.sha256",
                "size": 80,
            },
        ],
    }

    def opener(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        path = urlparse(url).path
        if path.endswith("/releases/latest"):
            return Response(json.dumps(payload).encode("utf-8"))
        if path.endswith(".sha256"):
            return Response(f"{digest}  Lili-Windows-x64-Setup.exe\n".encode("ascii"))
        return Response(installer)

    manager = ProgramUpdateManager(
        app_version="0.22.45",
        opener=opener,
        download_root=tmp_path / "updates",
    )
    release = manager.fetch_latest()
    assert release is not None
    result = manager.download_and_verify(release)
    assert result.installer_path.read_bytes() == installer
    assert result.installer_path.name == "v0.22.46-Lili-Windows-x64-Setup.exe"


def test_download_reports_byte_progress(tmp_path, monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    installer = b"installer payload for progress"
    digest = hashlib.sha256(installer).hexdigest()
    payload = {
        "tag_name": "v0.22.46",
        "html_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/tag/v0.22.46",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "Lili-Windows-x64-Setup.exe",
                "browser_download_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.22.46/Lili-Windows-x64-Setup.exe",
                "size": len(installer),
            },
            {
                "name": "Lili-Windows-x64-Setup.exe.sha256",
                "browser_download_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.22.46/Lili-Windows-x64-Setup.exe.sha256",
                "size": 80,
            },
        ],
    }

    def opener(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        path = urlparse(url).path
        if path.endswith("/releases/latest"):
            return Response(json.dumps(payload).encode("utf-8"))
        if path.endswith(".sha256"):
            return Response(f"{digest}  Lili-Windows-x64-Setup.exe\n".encode("ascii"))
        return Response(installer)

    manager = ProgramUpdateManager(
        app_version="0.22.45",
        opener=opener,
        download_root=tmp_path / "updates",
    )
    release = manager.fetch_latest()
    assert release is not None
    updates: list[tuple[int, int]] = []
    result = manager.download_and_verify(
        release,
        progress=lambda done, total: updates.append((done, total)),
    )

    assert result.installer_path.exists()
    assert updates[0] == (0, len(installer))
    assert updates[-1] == (len(installer), len(installer))
    assert all(done <= total for done, total in updates if total > 0)


def test_no_update_for_same_or_older_release(tmp_path, monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    payload = {"tag_name": "v0.22.45", "draft": False, "prerelease": False, "assets": []}

    def opener(request, timeout=0):
        return Response(json.dumps(payload).encode("utf-8"))

    manager = ProgramUpdateManager(app_version="0.22.45", opener=opener, download_root=tmp_path)
    result = manager.check_latest()
    assert result.current_version == "0.22.45"
    assert result.latest_version == "0.22.45"
    assert result.release is None
    assert result.status == "ok"
    assert manager.fetch_latest() is None


def test_missing_github_release_is_distinguished_from_up_to_date(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")

    def opener(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )

    result = ProgramUpdateManager(app_version="0.22.48", opener=opener).check_latest()
    assert result.release is None
    assert result.status == "no_release"
    assert result.current_version == "0.22.48"


def test_release_version_must_be_semver(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")

    def opener(request, timeout=0):
        return Response(
            json.dumps({"tag_name": "latest", "draft": False, "prerelease": False}).encode("utf-8")
        )

    manager = ProgramUpdateManager(app_version="0.22.48", opener=opener)
    with pytest.raises(ProgramUpdateError, match="版本号格式无法识别"):
        manager.check_latest()


def test_check_latest_returns_structured_new_release_and_notes(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    payload = {
        "tag_name": "v0.22.47",
        "html_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/tag/v0.22.47",
        "body": "修复待办操作列裁切\n完善程序更新反馈\n第三行不需要全部展示",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "Lili-Windows-x64-Setup.exe",
                "browser_download_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.22.47/Lili-Windows-x64-Setup.exe",
                "size": 12 * 1024 * 1024,
            }
        ],
    }

    def opener(request, timeout=0):
        return Response(json.dumps(payload).encode("utf-8"))

    result = ProgramUpdateManager(app_version="0.22.46", opener=opener).check_latest()
    assert result.update_available is True
    assert result.release is not None
    assert result.release.version == "0.22.47"
    assert "修复待办操作列裁切" in result.release.release_notes


def test_check_latest_rejects_malformed_release(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")

    def opener(request, timeout=0):
        return Response(json.dumps({"draft": False, "prerelease": False}).encode("utf-8"))

    manager = ProgramUpdateManager(app_version="0.22.46", opener=opener)
    with pytest.raises(ProgramUpdateError):
        manager.check_latest()


def test_api_rate_limit_falls_back_to_release_page_redirect(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")

    def opener(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "api.github.com" in url:
            raise urllib.error.HTTPError(
                url,
                403,
                "Forbidden",
                hdrs=None,
                fp=None,
            )
        return RedirectResponse(
            b"release page",
            "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/tag/v0.22.57",
        )

    manager = ProgramUpdateManager(app_version="0.22.56", opener=opener)
    result = manager.check_latest()

    assert result.latest_version == "0.22.57"
    assert result.release is not None
    assert result.release.asset_url.endswith(
        "/releases/download/v0.22.57/Lili-Windows-x64-Setup.exe"
    )
    assert result.release.checksum_url.endswith(
        "/releases/download/v0.22.57/Lili-Windows-x64-Setup.exe.sha256"
    )
    assert result.release.asset_size == 0


def test_release_check_is_cached_to_avoid_repeated_api_requests(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    calls = 0
    payload = {"tag_name": "v0.22.57", "draft": False, "prerelease": False, "assets": []}

    def opener(request, timeout=0):
        nonlocal calls
        calls += 1
        return Response(json.dumps(payload).encode("utf-8"))

    manager = ProgramUpdateManager(app_version="0.22.57", opener=opener, cache_seconds=300)
    first = manager.check_latest()
    second = manager.check_latest()

    assert first == second
    assert calls == 1


def test_forced_release_check_bypasses_cached_result(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    calls = 0
    payloads = [
        {
            "tag_name": "v0.23.7",
            "draft": False,
            "prerelease": False,
            "assets": [],
        },
        {
            "tag_name": "v0.23.8",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "Lili-Windows-x64-Setup.exe",
                "browser_download_url": "https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.23.8/Lili-Windows-x64-Setup.exe",
                "size": 1,
            }],
        },
    ]

    def opener(request, timeout=0):
        nonlocal calls
        payload = payloads[min(calls, len(payloads) - 1)]
        calls += 1
        return Response(json.dumps(payload).encode("utf-8"))

    manager = ProgramUpdateManager(app_version="0.23.7", opener=opener, cache_seconds=300)
    cached = manager.check_latest()
    refreshed = manager.check_latest(force=True)

    assert cached.latest_version == "0.23.7"
    assert refreshed.latest_version == "0.23.8"
    assert calls == 2



def test_program_update_worker_keeps_force_flag_and_passes_it_to_manager() -> None:
    from onepic_desktop_pet.update_worker import ProgramUpdateCheckWorker

    calls = []

    class Manager:
        def check_app_update(self, *, force: bool = False):
            calls.append(force)
            return object()

    worker = ProgramUpdateCheckWorker(Manager(), force=True)
    assert worker.force is True
    worker.run()
    assert calls == [True]

def test_desktop_pet_application_is_a_qt_object_for_worker_callbacks() -> None:
    from PySide6.QtCore import QObject

    from onepic_desktop_pet.app import DesktopPetApplication

    assert issubclass(DesktopPetApplication, QObject)
