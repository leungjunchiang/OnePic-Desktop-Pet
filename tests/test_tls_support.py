"""Tests for the verified CA handling used by frozen macOS builds."""

import ssl

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

