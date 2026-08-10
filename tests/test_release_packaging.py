"""Validate that public release automation stays cross-platform and private-data safe."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_public_release_never_enables_private_assets() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    macos_build = (PROJECT_ROOT / "scripts" / "build_macos.sh").read_text(
        encoding="utf-8"
    )

    assert "build.ps1 -IncludeUserAssets" not in workflow
    assert "ONEPIC_INCLUDE_USER_ASSETS=1" not in workflow
    assert "ONEPIC_INCLUDE_USER_ASSETS=0" in macos_build


def test_release_builds_windows_zip_and_macos_dmg() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    macos_build = (PROJECT_ROOT / "scripts" / "build_macos.sh").read_text(
        encoding="utf-8"
    )
    spec = (PROJECT_ROOT / "OnePicDesktopPet.spec").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "Lili-Windows-x64.zip" in workflow
    assert "Lili-macOS-${{ matrix.arch }}-unsigned.dmg" in workflow
    assert 'dmg_file="dist/Lili-macOS-${release_arch}-unsigned.dmg"' in macos_build
    assert 'arm64) release_arch="arm64"' in macos_build
    assert 'x64|x86_64) release_arch="x64"' in macos_build
    assert "macos-latest" in workflow
    assert "macos-15-intel" in workflow
    assert "BUNDLE(" in spec
    assert 'name="Lili"' in spec
    assert '"CFBundleDisplayName": "Lili"' in spec
    assert '"CFBundleShortVersionString": "0.7.0"' in spec
    assert 'version = "0.7.0"' in pyproject
