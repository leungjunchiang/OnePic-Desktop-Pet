"""验证 Mac 正式签名缺少凭据时在构建前失败，防止误产未公证安装包。"""

import base64
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS build entry point")
def test_signed_entry_does_not_fall_back_when_credentials_are_missing():
    env = dict(os.environ)
    env.pop("LILI_MAC_SIGNING_IDENTITY", None)
    env.pop("LILI_MAC_NOTARY_PROFILE", None)
    env.pop("LILI_MAC_SELF_SIGN_IDENTITY", None)
    env.pop("LILI_MAC_SELF_SIGN_KEYCHAIN", None)
    result = subprocess.run(["bash", "scripts/build_macos_signed.sh"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "LILI_MAC_SIGNING_IDENTITY" in result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS build entry point")
@pytest.mark.parametrize(
    "identity,profile,expected",
    [
        ("Developer ID Application: Example (TEAMID)", "", "正式签名需要"),
        ("", "lili-notary", "没有设置 LILI_MAC_SIGNING_IDENTITY"),
        ("-", "lili-notary", "正式签名需要"),
        ("Developer ID Application: nonexistent-test-identity (0000000000)",
         "lili-notary", "钥匙串中没有可用的 Developer ID Application"),
    ],
)
def test_incomplete_signing_configuration_stops_before_build(identity, profile, expected):
    env = dict(os.environ, LILI_MAC_SIGNING_IDENTITY=identity,
               LILI_MAC_NOTARY_PROFILE=profile, LILI_MAC_SELF_SIGN_IDENTITY="",
               LILI_MAC_SELF_SIGN_KEYCHAIN="", ONEPIC_MAC_ARCH="arm64")
    result = subprocess.run(["bash", "scripts/build_macos.sh"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert expected in result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS build entry point")
@pytest.mark.parametrize("identity,keychain,expected", [
    ("BAD", "/tmp/missing-lili.keychain", "免费签名需要"),
    ("", "/tmp/missing-lili.keychain", "没有设置签名身份"),
    ("A" * 40, "/tmp/missing-lili.keychain", "没有可用的免费签名身份"),
])
def test_selfsigned_configuration_cannot_fall_back_to_adhoc(identity, keychain, expected):
    env = dict(os.environ, LILI_MAC_SIGNING_IDENTITY="", LILI_MAC_NOTARY_PROFILE="",
               LILI_MAC_SELF_SIGN_IDENTITY=identity, LILI_MAC_SELF_SIGN_KEYCHAIN=keychain,
               ONEPIC_MAC_ARCH="arm64")
    result = subprocess.run(["bash", "scripts/build_macos.sh"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert expected in result.stderr


def test_hardened_runtime_entitlements_have_no_debugging_privilege():
    data = plistlib.loads((ROOT / "packaging/macos/Lili.entitlements").read_bytes())
    assert data.get("com.apple.security.automation.apple-events") is True
    assert not data.get("com.apple.security.get-task-allow", False)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS signing and base64 tools")
def test_free_identity_is_reused_and_bad_fingerprint_stops_ci_before_keychain(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text('#!/bin/bash\nprintf "%s %s\\n" "$1" "$2" >> "$LILI_FAKE_GH_LOG"\nif [[ "$1" == secret ]]; then cat >/dev/null; fi\n')
    fake_gh.chmod(0o700)
    identity_dir = tmp_path / "identity"
    pin = tmp_path / "public-pin.sha256"
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
               LILI_SELF_SIGN_DIR=str(identity_dir), LILI_SELF_SIGN_PIN_PATH=str(pin),
               LILI_FAKE_GH_LOG=str(tmp_path / "gh.log"))
    args = ["bash", "scripts/configure_macos_selfsign_secrets.sh"]
    first = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    p12 = identity_dir / "lili-selfsign.p12"
    password = identity_dir / "lili-selfsign-password"
    bundle = identity_dir / "lili-selfsign-github-secret.json"
    original = p12.read_bytes()
    assert identity_dir.stat().st_mode & 0o777 == 0o700
    assert p12.stat().st_mode & 0o777 == 0o600
    assert password.stat().st_mode & 0o777 == 0o600
    assert bundle.stat().st_mode & 0o777 == 0o600
    assert base64.b64decode(json.loads(bundle.read_text())["p12"]) == original
    assert password.read_text() not in first.stdout

    second = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert p12.read_bytes() == original
    previous_calls = (tmp_path / "gh.log").read_text()
    prepared = subprocess.run(args + ["--prepare-only"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=30)
    assert prepared.returncode == 0, prepared.stderr
    assert p12.read_bytes() == original
    assert (tmp_path / "gh.log").read_text() == previous_calls
    assert (identity_dir / "lili-selfsign-fingerprint").read_text().isalnum()
    assert pin.read_text().strip() == (identity_dir / "lili-selfsign-fingerprint").read_text()

    ci_dir = tmp_path / "runner"
    ci_dir.mkdir()
    ci_env = dict(os.environ, GITHUB_ACTIONS="true", RUNNER_OS="macOS",
                  RUNNER_TEMP=str(ci_dir), GITHUB_ENV=str(tmp_path / "github-env"),
                  LILI_MAC_SELF_SIGN_BUNDLE=bundle.read_text(),
                  LILI_MAC_SELF_SIGN_CERT_SHA256="0" * 64)
    ci = subprocess.run(["bash", "scripts/setup_macos_selfsign_ci.sh"], cwd=ROOT,
                        env=ci_env, capture_output=True, text=True, timeout=20)
    assert ci.returncode != 0
    assert "证书指纹与固定值不一致" in ci.stderr
    assert not (tmp_path / "github-env").exists()
    assert not (ci_dir / "lili-selfsign" / "identity.p12").exists()
    assert not (ci_dir / "lili-selfsign" / "password").exists()

    pin.write_text("0" * 64)
    mismatch = subprocess.run(args + ["--prepare-only"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=30)
    assert mismatch.returncode != 0
    assert "项目固定指纹不同" in mismatch.stderr
    assert p12.read_bytes() == original
