"""验证更新包断点续传、备用线路和官方摘要完整性。"""

from __future__ import annotations

import hashlib
import json
import ssl
import urllib.error
from urllib.parse import urlparse

import pytest

from onepic_desktop_pet.program_updates import (
    ProgramRelease,
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


class RangeResponse(Response):
    def __init__(self, payload, status=200, headers=None, interrupt=False):
        super().__init__(payload)
        self.status = status
        self.headers = headers or {}
        self.interrupt = interrupt

    def read(self, size=-1):
        if self.interrupt and self.offset:
            raise OSError('simulated interrupted connection')
        if self.interrupt:
            size = min(3, size)
        return super().read(size)


def _release_for_resume(payload):
    return ProgramRelease('0.23.999', 'v0.23.999',
        'https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/tag/v0.23.999',
        'Lili-Windows-x64-Setup.exe',
        'https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/download/v0.23.999/Lili-Windows-x64-Setup.exe',
        len(payload), None, hashlib.sha256(payload).hexdigest())


def test_interrupted_update_resumes_next_attempt_and_verifies(tmp_path):
    payload = b'0123456789abcdef'
    requests = []
    def opener(request, timeout=0):
        header = request.get_header('Range')
        requests.append(header)
        if len(requests) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))}, interrupt=True)
        assert header == 'bytes=3-'
        return RangeResponse(payload[3:], status=206, headers={'Content-Length': str(len(payload)-3),
                             'Content-Range': f'bytes 3-{len(payload)-1}/{len(payload)}'})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=())
    release = _release_for_resume(payload)
    with pytest.raises(ProgramUpdateError):
        manager.download_and_verify(release)
    assert (tmp_path / 'v0.23.999-Lili-Windows-x64-Setup.exe.part').read_bytes() == payload[:3]
    result = manager.download_and_verify(release)
    assert result.installer_path.read_bytes() == payload
    assert requests == [None, 'bytes=3-']


def test_resume_ignored_range_restarts_without_corruption(tmp_path):
    payload = b'0123456789abcdef'
    requests = []
    def opener(request, timeout=0):
        header = request.get_header('Range')
        requests.append(header)
        if len(requests) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))}, interrupt=True)
        assert header == 'bytes=3-'
        return RangeResponse(payload, status=200, headers={'Content-Length': str(len(payload))})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=())
    release = _release_for_resume(payload)
    with pytest.raises(ProgramUpdateError):
        manager.download_and_verify(release)
    assert manager.download_and_verify(release).installer_path.read_bytes() == payload


def test_bad_content_range_never_appends_untrusted_bytes(tmp_path):
    payload = b'0123456789abcdef'
    calls = []
    def opener(request, timeout=0):
        calls.append(request.get_header('Range'))
        if len(calls) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))}, interrupt=True)
        return RangeResponse(payload[4:], status=206, headers={'Content-Range': f'bytes 4-{len(payload)-1}/{len(payload)}'})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=())
    release = _release_for_resume(payload)
    with pytest.raises(ProgramUpdateError): manager.download_and_verify(release)
    with pytest.raises(ProgramUpdateError): manager.download_and_verify(release)
    assert (tmp_path / 'v0.23.999-Lili-Windows-x64-Setup.exe.part').read_bytes() == payload[:3]


def test_failed_official_stream_continues_from_mirror_with_official_digest(tmp_path):
    """官方中断后备用线路从同一位置续传，最终以官方摘要检查。"""
    payload = b'0123456789abcdef'
    requests = []
    def opener(request, timeout=0):
        requests.append((request.full_url, request.get_header('Range')))
        if len(requests) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))}, interrupt=True)
        assert request.full_url.startswith('https://ghfast.top/https://github.com/')
        assert request.get_header('Range') == 'bytes=3-'
        return RangeResponse(payload[3:], status=206, headers={'Content-Length': str(len(payload)-3),
                             'Content-Range': f'bytes 3-{len(payload)-1}/{len(payload)}'})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=('https://ghfast.top',))
    result = manager.download_and_verify(_release_for_resume(payload))
    assert result.installer_path.read_bytes() == payload
    assert len(requests) == 2


def test_mirror_tampering_never_installs_unverified_bytes(tmp_path):
    payload = b'0123456789abcdef'
    def opener(request, timeout=0):
        if request.full_url.startswith('https://github.com/'):
            raise urllib.error.HTTPError(request.full_url, 503, 'Unavailable', None, None)
        return RangeResponse(b'x' * len(payload), headers={'Content-Length': str(len(payload))})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=('https://ghfast.top',))
    with pytest.raises(ProgramUpdateError, match='校验失败'):
        manager.download_and_verify(_release_for_resume(payload))
    assert not (tmp_path/'v0.23.999-Lili-Windows-x64-Setup.exe').exists()


def test_mirror_failure_falls_back_to_official_continuation(monkeypatch, tmp_path):
    """备用线路不可用时，仍能回到官方线路继续已有断点。"""
    import onepic_desktop_pet.program_updates as module
    monkeypatch.setattr(module, 'sleep', lambda _seconds: None)
    payload = b'0123456789abcdef'
    requests = []
    def opener(request, timeout=0):
        requests.append((request.full_url, request.get_header('Range')))
        if len(requests) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))}, interrupt=True)
        if request.full_url.startswith('https://ghfast.top/'):
            raise urllib.error.URLError('mirror offline')
        assert request.get_header('Range') == 'bytes=3-'
        return RangeResponse(payload[3:], status=206,
            headers={'Content-Length': str(len(payload)-3),
                     'Content-Range': f'bytes 3-{len(payload)-1}/{len(payload)}'})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=('https://ghfast.top',))
    assert manager.download_and_verify(_release_for_resume(payload)).installer_path.read_bytes() == payload
    assert requests[-1][0].startswith('https://github.com/')


def test_changed_official_digest_discards_old_partial(tmp_path):
    payload = b'0123456789abcdef'
    first = _release_for_resume(payload)
    requests = []
    def opener(request, timeout=0):
        requests.append(request.get_header('Range'))
        if len(requests) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))}, interrupt=True)
        return RangeResponse(payload[::-1], headers={'Content-Length': str(len(payload))})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=())
    with pytest.raises(ProgramUpdateError): manager.download_and_verify(first)
    second = _release_for_resume(payload[::-1])
    assert manager.download_and_verify(second).installer_path.read_bytes() == payload[::-1]
    assert requests == [None, None]


def test_slow_official_stream_switches_to_mirror(monkeypatch, tmp_path):
    """官方长期慢速时切换，备用线路续传已收到的数据。"""
    import onepic_desktop_pet.program_updates as module
    payload = bytes(range(256)) * 1024
    ticks = iter([0.0, 13.0] + [14.0] * 20)
    monkeypatch.setattr(module, 'monotonic', lambda: next(ticks))
    requests = []
    def opener(request, timeout=0):
        requests.append((request.full_url, request.get_header('Range')))
        if len(requests) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))})
        assert request.get_header('Range') == 'bytes=65536-'
        return RangeResponse(payload[65536:], status=206,
            headers={'Content-Length': str(len(payload)-65536),
                     'Content-Range': f'bytes 65536-{len(payload)-1}/{len(payload)}'})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=('https://ghfast.top',))
    assert manager.download_and_verify(_release_for_resume(payload)).installer_path.read_bytes() == payload
    assert len(requests) == 2


def test_incomplete_response_never_reports_success(tmp_path):
    payload = b'0123456789abcdef'
    def opener(request, timeout=0):
        return RangeResponse(payload[:4], headers={'Content-Length': str(len(payload))})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=())
    with pytest.raises(ProgramUpdateError, match='未完成'):
        manager.download_and_verify(_release_for_resume(payload))
    assert not (tmp_path/'v0.23.999-Lili-Windows-x64-Setup.exe').exists()


def test_http_416_discards_stale_partial_and_restarts(tmp_path):
    """未知总大小的旧断点超出文件时，安全清除并重下。"""
    from dataclasses import replace
    payload = b'0123456789abcdef'
    requests = []
    def opener(request, timeout=0):
        requests.append(request.get_header('Range'))
        if len(requests) == 1:
            return RangeResponse(payload, headers={'Content-Length': str(len(payload))}, interrupt=True)
        if len(requests) == 2:
            raise urllib.error.HTTPError(request.full_url, 416, 'Range Not Satisfiable', None, None)
        return RangeResponse(payload, headers={'Content-Length': str(len(payload))})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=())
    release = replace(_release_for_resume(payload), asset_size=0)
    with pytest.raises(ProgramUpdateError): manager.download_and_verify(release)
    assert manager.download_and_verify(release).installer_path.read_bytes() == payload
    assert requests == [None, 'bytes=3-', None]


def test_full_length_corrupt_partial_redownloads_instead_of_stalling(tmp_path):
    """满长度但散列不符的断点应重下，而不是反复请求文件末尾。"""
    payload = b'0123456789abcdef'
    release = _release_for_resume(payload)
    partial = tmp_path/'v0.23.999-Lili-Windows-x64-Setup.exe.part'
    partial.write_bytes(b'x'*len(payload))
    meta = partial.with_suffix('.part.json')
    meta.write_text(json.dumps({'url':release.asset_url, 'size':release.asset_size,
                                'sha256':release.checksum_value}), encoding='utf-8')
    requests = []
    def opener(request, timeout=0):
        requests.append(request.get_header('Range'))
        return RangeResponse(payload, headers={'Content-Length':str(len(payload))})
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=())
    assert manager.download_and_verify(release).installer_path.read_bytes() == payload
    assert requests == [None]


def test_missing_official_digest_never_calls_mirror(tmp_path):
    from dataclasses import replace
    def opener(request, timeout=0):
        raise AssertionError('no installer request without official digest')
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=('https://ghfast.top',))
    with pytest.raises(ProgramUpdateError, match='SHA-256'):
        manager.download_and_verify(replace(_release_for_resume(b'data'), checksum_value=None))


def test_official_checksum_redirect_cannot_trust_a_third_party(tmp_path):
    """镜像不能提供用来信任镜像自身的校验值。"""
    from dataclasses import replace
    release = replace(_release_for_resume(b'data'), checksum_value=None,
                      checksum_url='https://github.com/example/sha256')
    def opener(request, timeout=0):
        return RedirectResponse(b'0'*64, 'https://untrusted.example/sha256')
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path, mirror_bases=('https://ghfast.top',))
    with pytest.raises(ProgramUpdateError, match='非 GitHub'):
        manager.download_and_verify(release)


def test_release_metadata_redirect_cannot_trust_a_third_party(tmp_path):
    def opener(request, timeout=0):
        return RedirectResponse(b'{}', 'https://untrusted.example/releases/latest')
    manager = ProgramUpdateManager(opener=opener, download_root=tmp_path)
    with pytest.raises(ProgramUpdateError, match='非 GitHub'):
        manager._read_url('https://api.github.com/repos/example/releases/latest', max_bytes=100)


def test_only_exact_project_asset_can_use_mirror(tmp_path):
    from dataclasses import replace
    manager = ProgramUpdateManager(download_root=tmp_path, mirror_bases=('https://ghfast.top',))
    release = _release_for_resume(b'data')
    assert len(manager._mirror_urls(release)) == 1
    assert manager._mirror_urls(replace(release, asset_url='https://github.com/other/repo/releases/download/v0.23.999/Lili-Windows-x64-Setup.exe')) == []
    assert manager._mirror_urls(replace(release, asset_url=release.asset_url+'?unknown=1')) == []
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


def test_updater_retries_transient_tls_eof(monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    calls = 0

    def opener(request, timeout=0, context=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError(ssl.SSLEOFError("UNEXPECTED_EOF_WHILE_READING"))
        return Response(b"{}")

    monkeypatch.setattr(module, "sleep", lambda _seconds: None)
    manager = ProgramUpdateManager(app_version="0.22.82", opener=opener)
    assert manager._read_url(
        "https://api.github.com/repos/example/releases/latest",
        max_bytes=1024,
    ) == b"{}"
    assert calls == 2


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
    manager.download_root.mkdir()
    old_installer = manager.download_root / "v0.22.45-Lili-Windows-x64-Setup.exe"
    old_partial = manager.download_root / "v0.22.44-Lili-Windows-x64-Setup.exe.part"
    unrelated_file = manager.download_root / "keep-me.txt"
    old_installer.write_bytes(b"old installer")
    old_partial.write_bytes(b"stale partial download")
    unrelated_file.write_text("keep", encoding="utf-8")

    release = manager.fetch_latest()
    assert release is not None
    result = manager.download_and_verify(release)
    assert result.installer_path.read_bytes() == installer
    assert result.installer_path.name == "v0.22.46-Lili-Windows-x64-Setup.exe"
    assert not old_installer.exists()
    assert not old_partial.exists()
    assert unrelated_file.exists()


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


def test_api_network_failure_uses_official_release_page(monkeypatch) -> None:
    """API 连接失败时仍尝试官方发布页，不从第三方获取版本或摘要。"""
    import onepic_desktop_pet.program_updates as module
    monkeypatch.setattr(module, '_asset_name', lambda: 'Lili-Windows-x64-Setup.exe')
    monkeypatch.setattr(module, 'sleep', lambda _seconds: None)
    def opener(request, timeout=0):
        if 'api.github.com' in request.full_url:
            raise urllib.error.URLError('api unavailable')
        return RedirectResponse(b'release page',
            'https://github.com/leungjunchiang/OnePic-Desktop-Pet/releases/tag/v0.23.999')
    result = ProgramUpdateManager(app_version='0.23.352', opener=opener).check_latest()
    assert result.latest_version == '0.23.999'
    assert result.release.checksum_url.startswith('https://github.com/')


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


def test_desktop_pet_application_is_a_qt_object_for_worker_callbacks() -> None:
    from PySide6.QtCore import QObject

    from onepic_desktop_pet.app import DesktopPetApplication

    assert issubclass(DesktopPetApplication, QObject)
