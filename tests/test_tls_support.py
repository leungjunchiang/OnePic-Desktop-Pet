"""Tests for the verified CA handling used by frozen macOS builds."""

import ssl
from types import SimpleNamespace
from pathlib import Path

import onepic_desktop_pet.tls_support as tls_support

from onepic_desktop_pet.tls_support import tls_diagnostics, verified_ssl_context


def test_verified_context_never_disables_certificate_validation():
    context = verified_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_tls_diagnostics_contains_safe_runtime_fields():
    diagnostics = tls_diagnostics()

    assert diagnostics["platform"]
    assert diagnostics["python"]
    assert diagnostics["openssl"]
    assert "certifi_bundle" in diagnostics
    assert "SSL_CERT_FILE" in diagnostics
    assert "REQUESTS_CA_BUNDLE" in diagnostics
    assert "verified_ca_source" in diagnostics


def test_certifi_bundle_is_preferred_when_available(monkeypatch, tmp_path: Path):
    system_bundle = tmp_path / "cert.pem"
    system_bundle.write_text("not used for parsing in this test", encoding="utf-8")
    certifi_bundle = tmp_path / "certifi.pem"
    certifi_bundle.write_text("certifi", encoding="utf-8")
    monkeypatch.setattr(tls_support, "SYSTEM_CA_BUNDLE_CANDIDATES", (system_bundle,))
    monkeypatch.setattr(tls_support, "certifi_bundle_path", lambda: certifi_bundle)

    assert tls_support.system_ca_bundle_path() == system_bundle
    assert tls_support.verified_ca_bundle_path() == certifi_bundle


def test_certifi_remains_fallback_when_system_bundle_is_missing(monkeypatch):
    monkeypatch.setattr(tls_support, "SYSTEM_CA_BUNDLE_CANDIDATES", (tls_support.Path("/path/that/does/not/exist"),))
    monkeypatch.setattr(tls_support.ssl, "get_default_verify_paths", lambda: SimpleNamespace(cafile=None))
    fallback = tls_support.certifi_bundle_path()

    assert tls_support.system_ca_bundle_path() is None
    assert tls_support.verified_ca_bundle_path() == fallback

