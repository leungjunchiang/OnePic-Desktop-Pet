"""Verified TLS support for packaged desktop builds.

macOS applications launched from Finder do not inherit the same Python and
certificate environment as a terminal.  Keep certificate discovery in one
small module so the social transport can use an explicit, verified CA bundle
without ever disabling hostname or certificate verification.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import ssl
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


SYSTEM_CA_BUNDLE_CANDIDATES = (
    Path("/etc/ssl/cert.pem"),
    Path("/etc/ssl/certs/ca-certificates.crt"),
)


@lru_cache(maxsize=1)
def certifi_bundle_path() -> Path | None:
    """Return the bundled certifi CA file when it is installed and readable."""

    try:
        import certifi

        candidate = Path(certifi.where()).expanduser()
    except (ImportError, OSError, AttributeError):
        return None
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def system_ca_bundle_path() -> Path | None:
    """Return a readable OS CA bundle when the platform exposes one.

    Finder-launched macOS applications do not reliably inherit the shell's
    OpenSSL environment.  macOS commonly exposes its trusted bundle at
    ``/etc/ssl/cert.pem``; Linux distributions commonly use the second
    candidate.  The paths are intentionally explicit and never come from a
    user-controlled URL or request header.
    """

    for candidate in SYSTEM_CA_BUNDLE_CANDIDATES:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def verified_ca_bundle_path() -> Path | None:
    """Choose a verified CA bundle, preferring the OS trust store."""

    return system_ca_bundle_path() or certifi_bundle_path()


def verified_ssl_context() -> ssl.SSLContext:
    """Create a normal certificate-verifying context.

    The operating system trust store is preferred so corporate/device trust
    and the normal macOS certificate chain keep working.  The application-
    bundled certifi file is the safe fallback for frozen builds whose Python
    runtime cannot see the system bundle.  This function never uses
    ``CERT_NONE`` and never disables hostname checking.
    """

    bundle = verified_ca_bundle_path()
    if bundle is not None:
        try:
            return ssl.create_default_context(cafile=str(bundle))
        except (OSError, ssl.SSLError):
            # A stale or incompatible system bundle must not prevent the
            # bundled certifi fallback from being tried.
            fallback = certifi_bundle_path()
            if fallback is not None and fallback != bundle:
                try:
                    return ssl.create_default_context(cafile=str(fallback))
                except (OSError, ssl.SSLError):
                    pass
    return ssl.create_default_context()


def tls_diagnostics() -> dict[str, Any]:
    """Return safe TLS diagnostics suitable for local logs.

    Tokens and request bodies are intentionally not included.  Environment
    values are limited to certificate *paths*, which are useful when a Finder
    launch has inherited a broken override.
    """

    system_bundle = system_ca_bundle_path()
    bundle = verified_ca_bundle_path()
    try:
        certifi_version = importlib.metadata.version("certifi")
    except importlib.metadata.PackageNotFoundError:
        certifi_version = "未安装"
    defaults = ssl.get_default_verify_paths()
    return {
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "openssl": ssl.OPENSSL_VERSION,
        "certifi": certifi_version,
        "certifi_bundle": str(bundle) if bundle else "未找到",
        "certifi_bundle_exists": bool(bundle and bundle.is_file()),
        "system_ca_bundle": str(system_bundle) if system_bundle else "未找到",
        "verified_ca_bundle": str(bundle) if bundle else "未找到",
        "verified_ca_source": "system" if system_bundle and bundle == system_bundle else ("certifi" if bundle else "python-default"),
        "ssl_default_cafile": defaults.cafile or "",
        "ssl_default_capath": defaults.capath or "",
        "SSL_CERT_FILE": os.environ.get("SSL_CERT_FILE", ""),
        "REQUESTS_CA_BUNDLE": os.environ.get("REQUESTS_CA_BUNDLE", ""),
    }

