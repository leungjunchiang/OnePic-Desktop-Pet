"""Pure-Python tests for the safe full-program updater."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

from onepic_desktop_pet.program_updates import ProgramUpdateManager, version_key


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


def test_program_version_comparison_is_numeric() -> None:
    assert version_key("v0.22.10") > version_key("0.22.9")
    assert version_key("0.22.45") == (0, 22, 45)


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


def test_no_update_for_same_or_older_release(tmp_path, monkeypatch) -> None:
    import onepic_desktop_pet.program_updates as module

    monkeypatch.setattr(module, "_asset_name", lambda: "Lili-Windows-x64-Setup.exe")
    payload = {"tag_name": "v0.22.45", "draft": False, "prerelease": False, "assets": []}

    def opener(request, timeout=0):
        return Response(json.dumps(payload).encode("utf-8"))

    manager = ProgramUpdateManager(app_version="0.22.45", opener=opener, download_root=tmp_path)
    assert manager.fetch_latest() is None
