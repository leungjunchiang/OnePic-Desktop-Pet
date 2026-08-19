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

    candidates = list(SYSTEM_CA_BUNDLE_CANDIDATES)
    # Python distributions on macOS often ship their own OpenSSL CA file
    # outside /etc.  It is still a normal PEM trust store, so use it when the
    # explicit OS paths are unavailable.
    try:
        default_cafile = ssl.get_default_verify_paths().cafile
    except AttributeError:
        default_cafile = None
    if default_cafile:
        candidates.append(Path(default_cafile))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def verified_ca_bundle_path() -> Path | None:
    """Choose a CA bundle that is reliable in Finder-launched frozen apps.

    The Python runtime inside a macOS ``.app`` can see ``/etc/ssl/cert.pem``
    while still receiving an incomplete or stale trust store.  The packaged
    certifi bundle is deterministic and contains GitHub's public chain, so it
    is preferred for HTTPS update checks; the OS bundle remains the fallback
    for environments that intentionally install their own trust roots.
    """

    return certifi_bundle_path() or system_ca_bundle_path()


def verified_ssl_context() -> ssl.SSLContext:
    """Create a normal certificate-verifying context.

    The application-bundled certifi file is loaded first for a deterministic
    public CA set in frozen builds; the operating system trust store is then
    added so corporate/device roots and normal macOS certificate chains keep
    working.  This function never uses ``CERT_NONE`` and never disables
    hostname checking.
    """

    # Start with the normal verifying context, then load trusted bundles in
    # priority order.  This keeps CERT_REQUIRED/check_hostname enabled while
    # allowing an enterprise/macOS root from the OS bundle to coexist with
    # the deterministic certifi bundle shipped inside the application.
    context = ssl.create_default_context()
    candidates = (certifi_bundle_path(), system_ca_bundle_path())
    for bundle in candidates:
        if bundle is None:
            continue
        try:
            context.load_verify_locations(cafile=str(bundle))
        except (OSError, ssl.SSLError):
            continue
    return context


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

