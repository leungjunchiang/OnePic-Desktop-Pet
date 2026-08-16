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


def verified_ssl_context() -> ssl.SSLContext:
    """Create a normal certificate-verifying context.

    The certifi bundle is preferred for the frozen macOS app because it is
    shipped with the application.  If it is unavailable, Python's verified
    system defaults remain the safe fallback.  This function never uses
    ``CERT_NONE`` and never disables hostname checking.
    """

    bundle = certifi_bundle_path()
    if bundle is not None:
        try:
            return ssl.create_default_context(cafile=str(bundle))
        except (OSError, ssl.SSLError):
            pass
    return ssl.create_default_context()


def tls_diagnostics() -> dict[str, Any]:
    """Return safe TLS diagnostics suitable for local logs.

    Tokens and request bodies are intentionally not included.  Environment
    values are limited to certificate *paths*, which are useful when a Finder
    launch has inherited a broken override.
    """

    bundle = certifi_bundle_path()
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
        "ssl_default_cafile": defaults.cafile or "",
        "ssl_default_capath": defaults.capath or "",
        "SSL_CERT_FILE": os.environ.get("SSL_CERT_FILE", ""),
        "REQUESTS_CA_BUNDLE": os.environ.get("REQUESTS_CA_BUNDLE", ""),
    }

