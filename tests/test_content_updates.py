from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

import pytest

from onepic_desktop_pet.content_updates import (
    ContentManifest,
    ContentUpdateError,
    ContentUpdateManager,
)
from onepic_desktop_pet.resources import (
    clear_content_overlay_cache,
    resource_path,
    resource_root,
    set_content_update_root,
)


def _manifest_for(path: Path, *, version: str = "v-test-1") -> dict:
    data = path.read_bytes()
    return {
        "schema_version": 1,
        "content_version": version,
        "min_app_version": "0.0.0",
        "files": [
            {
                "path": "resources/liumao_persona.txt",
                "url": path.as_uri(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        ],
    }


def test_content_patch_downloads_verifies_and_activates_atomically(tmp_path: Path) -> None:
    update_root = tmp_path / "content_updates"
    source = tmp_path / "liumao_persona.txt"
    manifest_path = tmp_path / "content-manifest.json"
    source.write_text("六毛补充人格测试内容\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(_manifest_for(source)), encoding="utf-8")
    set_content_update_root(update_root)
    try:
        manager = ContentUpdateManager(
            manifest_url=manifest_path.as_uri(),
            update_root=update_root,
            opener=urllib.request.urlopen,
            app_version="0.22.44",
            allow_local_files=True,
        )
        result = manager.check_and_apply()

        assert result is not None
        assert result.updated_files == ("resources/liumao_persona.txt",)
        assert (update_root / "active.json").is_file()
        assert resource_path("resources/liumao_persona.txt").read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
        assert not any((update_root / ".staging").glob("*"))
    finally:
        set_content_update_root(None)
        clear_content_overlay_cache()


def test_content_patch_rejects_hash_mismatch_without_switching_version(tmp_path: Path) -> None:
    update_root = tmp_path / "content_updates"
    source = tmp_path / "liumao_persona.txt"
    source.write_text("未通过校验\n", encoding="utf-8")
    mapping = _manifest_for(source)
    mapping["files"][0]["sha256"] = "0" * 64
    manager = ContentUpdateManager(update_root=update_root, app_version="0.22.44", allow_local_files=True)

    with pytest.raises(ContentUpdateError, match="内容校验失败"):
        manager.apply(ContentManifest.from_mapping(mapping))

    assert not (update_root / "active.json").exists()


def test_content_manifest_rejects_paths_outside_content_roots() -> None:
    mapping = {
        "schema_version": 1,
        "content_version": "v-test-1",
        "files": [
            {
                "path": "src/secret.py",
                "url": "https://example.com/secret.py",
                "sha256": "0" * 64,
            }
        ],
    }

    with pytest.raises(ContentUpdateError, match="不允许在线覆盖"):
        ContentManifest.from_mapping(mapping)


def test_older_release_overlay_cannot_hide_newer_bundled_resources(tmp_path: Path) -> None:
    update_root = tmp_path / "content_updates"
    overlay = update_root / "versions" / "v0.22.79-1" / "assets" / "pet" / "daily-actions"
    overlay.mkdir(parents=True)
    (overlay / "22-thermos.png").write_bytes(b"stale-overlay")
    (update_root / "active.json").write_text(
        json.dumps({"content_version": "v0.22.79", "directory": "v0.22.79-1"}),
        encoding="utf-8",
    )
    set_content_update_root(update_root)
    try:
        resolved = resource_path("assets/pet/daily-actions/22-thermos.png")
        assert resolved == resource_root() / "assets/pet/daily-actions/22-thermos.png"
    finally:
        set_content_update_root(None)
        clear_content_overlay_cache()

