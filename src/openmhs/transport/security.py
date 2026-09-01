"""Transport security and authentication helpers for MHS."""

from __future__ import annotations

import ipaddress
import os
import secrets

from openmhs.core.device import AccessLevel


def is_loopback(host: str) -> bool:
    """Check if the given host/address is a local loopback address."""
    host_str = host.strip().lower()
    if host_str in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host_str)
        return ip.is_loopback
    except ValueError:
        return False


def get_bearer_token() -> str | None:
    """Get the configured bearer token from the environment, if present."""
    token = os.environ.get("OPENMHS_BEARER_TOKEN", "").strip()
    return token if token else None


def is_behind_tls_proxy() -> bool:
    """Check if the service is marked as running behind a TLS-terminating reverse proxy."""
    val = os.environ.get("OPENMHS_BEHIND_TLS_PROXY", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def is_insecure_http_allowed() -> bool:
    """Check if insecure HTTP on non-loopback interfaces is explicitly allowed."""
    val = os.environ.get("OPENMHS_ALLOW_INSECURE_HTTP", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


class SecurityConfigurationError(ValueError):
    """Raised when server network binding violates security requirements."""


def validate_host_security(
    host: str,
    *,
    token: str | None = None,
    behind_tls: bool | None = None,
    allow_insecure: bool | None = None,
) -> None:
    """Validate that binding to the given host satisfies security policy."""
    if is_loopback(host):
        return

    effective_token = token if token is not None else get_bearer_token()
    if not effective_token:
        raise SecurityConfigurationError(
            f"Binding to non-loopback interface '{host}' requires a Bearer token. "
            "Set the OPENMHS_BEARER_TOKEN environment variable."
        )

    effective_tls = behind_tls if behind_tls is not None else is_behind_tls_proxy()
    effective_insecure = (
        allow_insecure if allow_insecure is not None else is_insecure_http_allowed()
    )

    if not effective_tls and not effective_insecure:
        raise SecurityConfigurationError(
            f"Binding to non-loopback interface '{host}' requires either running behind a TLS "
            "reverse proxy (set OPENMHS_BEHIND_TLS_PROXY=1) or explicitly allowing insecure HTTP "
            "(set OPENMHS_ALLOW_INSECURE_HTTP=1)."
        )


def verify_bearer_token(auth_header: str | None, expected_token: str | None) -> bool:
    """Verify an Authorization header against the expected bearer token in constant time."""
    if not expected_token:
        return True
    if not auth_header:
        return False
    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    provided_token = parts[1].strip()
    return secrets.compare_digest(provided_token, expected_token)


def get_request_access_level(
    auth_header: str | None,
    *,
    is_local: bool,
    expected_token: str | None,
) -> AccessLevel | None:
    """Determine the AccessLevel for a request, or None if unauthorized."""
    if is_local and not expected_token:
        return AccessLevel.ADMIN

    if not verify_bearer_token(auth_header, expected_token):
        return None

    # Configured/authenticated access receives ADMIN permissions
    return AccessLevel.ADMIN
