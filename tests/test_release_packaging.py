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


def test_release_builds_installable_windows_app_and_macos_dmg() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    publisher = (
        PROJECT_ROOT / ".github" / "workflows" / "publish-release-assets.yml"
    ).read_text(encoding="utf-8")
    macos_build = (PROJECT_ROOT / "scripts" / "build_macos.sh").read_text(
        encoding="utf-8"
    )
    spec = (PROJECT_ROOT / "OnePicDesktopPet.spec").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    installer = (PROJECT_ROOT / "packaging" / "windows" / "Lili.iss").read_text(
        encoding="utf-8"
    )
    installer_script = (PROJECT_ROOT / "scripts" / "build_installer.ps1").read_text(
        encoding="utf-8"
    )

    assert "Lili-Windows-x64.zip" in workflow
    assert "Lili-Windows-x64-Setup.exe" in workflow
    assert "Lili-macOS-${{ matrix.arch }}-unsigned.dmg" in workflow
    assert 'dmg_file="dist/Lili-macOS-${release_arch}-unsigned.dmg"' in macos_build
    assert 'arm64) release_arch="arm64"' in macos_build
    assert 'x64|x86_64) release_arch="x64"' in macos_build
    assert "macos-latest" in workflow
    assert "macos-15-intel" in workflow
    assert 'gh release view "$GITHUB_REF_NAME"' in workflow
    assert 'gh release upload "$GITHUB_REF_NAME"' in workflow
    assert "--clobber" in workflow
    assert "artifact_run_id" in publisher
    assert "run-id: ${{ inputs.artifact_run_id }}" in publisher
    assert 'gh release upload "${{ inputs.release_tag }}"' in publisher
    assert "BUNDLE(" in spec
    assert 'name="Lili"' in spec
    assert '"CFBundleDisplayName": "Lili"' in spec
    assert '"CFBundleShortVersionString": "0.16.0"' in spec
    assert '"LSUIElement": False' in spec
    assert 'version = "0.16.0"' in pyproject
    assert "{localappdata}\\Programs\\Lili" in installer
    assert '{group}\\Lili' in installer
    assert "ChineseSimplified.isl" not in installer
    assert installer_script.isascii()
    assert 'ln -s /Applications "$dmg_root/Applications"' in macos_build


def test_one_command_release_script_has_safety_checks() -> None:
    """一键发布必须先验登录、干净工作区、测试、主分支和标签归属。"""

    script = (PROJECT_ROOT / "scripts" / "publish_release.ps1").read_text(
        encoding="utf-8"
    )

    assert "gh auth status" in script
    assert "git status --porcelain" in script
    assert 'branch -ne "main"' in script
    assert "test.ps1" in script
    assert "git push origin main" in script
    assert "gh release create" in script
    assert "--verify-tag" in script
    assert "--web" not in script
