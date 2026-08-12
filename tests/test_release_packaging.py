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
    assert "-Version 0.19.0" in workflow
    assert "-Version 0.18.1" not in workflow
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
    assert 'default: "v0.19.0"' in publisher
    assert "BUNDLE(" in spec
    assert 'name="Lili"' in spec
    assert '"CFBundleDisplayName": "Lili"' in spec
    assert '"CFBundleShortVersionString": "0.19.0"' in spec
    assert '"NSAppleEventsUsageDescription"' in spec
    assert '"winrt.windows.media.control"' in spec
    assert '"LSUIElement": False' in spec
    assert 'version = "0.19.0"' in pyproject
    assert "winrt-Windows.Media.Control" in pyproject
    assert "pyobjc-framework-Quartz" in pyproject
    assert "{localappdata}\\Programs\\Lili" in installer
    assert '{group}\\Lili' in installer
    assert "ChineseSimplified.isl" not in installer
    assert installer_script.isascii()
    assert 'ln -s /Applications "$dmg_root/Applications"' in macos_build


def test_one_command_release_script_has_safety_checks() -> None:
    """ä¸€é”®å‘å¸ƒå¿…é¡»å…ˆéªŒç™»å½•ã€å¹²å‡€å·¥ä½œåŒºã€æµ‹è¯•ã€ä¸»åˆ†æ”¯å’Œæ ‡ç­¾å½’å±žã€‚"""

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


def test_ai_settings_has_one_source_guarded_open_path() -> None:
    """æºç ä¸­ä¸å¾—ä¿ç•™ Agent çŠ¶æ€æˆ–æ—§åˆ«åç›´æŽ¥æ‰“å¼€è¿žæŽ¥ä¸Žé™ªä¼´è®¾ç½®ã€‚"""

    source_files = list((PROJECT_ROOT / "src").rglob("*.py"))
    sources = {
        path.relative_to(PROJECT_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in source_files
    }
    combinYYH—ˆ‹š›Ú[ŠÛÝ\˜Ù\Ë˜[Y\Ê
JBˆÚ[™ÝÈHÛÝ\˜Ù\ÖÈœÜ˜ËÛÛ™\X×Ù\ÚÝÜÜ]ÝÚ[™ÝËœH—BˆÚ]ÛX[˜YÙ\ˆHÛÝ\˜Ù\ÖÈœÜ˜ËÛÛ™\X×Ù\ÚÝÜÜ]ØÚ]ÛX[˜YÙ\‹œH—B‚ˆ›Üˆ›Ü˜šY[ˆ[ˆ
ˆ›Ü[—ØZWÜÙ][™ÜÈ‹ˆ›Ü[RTÙ][™ÜÈ‹ˆœÚÝÐRTÙ][™ÜÈ‹ˆœÙ]Ù][™ÜÓÜ[ˆ‹ˆœÙ]ÚÝÔÙ][™ÜÈ‹ˆ
N‚ˆ\ÜÙ\›Ü˜šY[ˆ›Ý[ˆÛÛXš[™Yˆ\ÜÙ\ÛÛXš[™Y˜ÛÝ[
™X[ÙÈHRTÙ][™ÜÑX[ÙÊŠHOHBˆ\ÜÙ\™YˆÜ[—ÜÙ][™ÜÊÙ[‹ÛÝ\˜ÙNˆÝŠHOˆ›ÛÛˆˆ[ˆÚ[™ÝÂˆ\ÜÙ\šYˆÛÝ\˜ÙHOHÑUS‘Ô×ÔÓÕTÑWÕTÑT—ÐPÕSÓŽˆˆ[ˆÚ[™ÝÂˆ\ÜÙ\RTÙ][™ÜÑX[ÙÈˆ›Ý[ˆÚ]ÛX[˜YÙ\‚