"""HTTP listener access mode: loopback-only vs LAN.

``MCP_ACCESS_MODE`` is optional. Unset keeps the existing HTTP policy
(bearer token, or explicit ``MCP_EMAIL_HTTP_INSECURE=true`` for isolated
development). When set to ``local`` or ``lan``, insecure mode is refused
and a bearer token is required. ``lan`` also requires an explicit Host
allowlist with DNS-rebinding protection left on.
"""

from __future__ import annotations

import os

ACCESS_MODE_ENV = "MCP_ACCESS_MODE"
VALID_ACCESS_MODES = frozenset({"local", "lan"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_DNS_REBINDING_ENV = "MCP_ENABLE_DNS_REBINDING_PROTECTION"
_ALLOWED_HOSTS_ENV = "MCP_ALLOWED_HOSTS"
_INSECURE_ENV = "MCP_EMAIL_HTTP_INSECURE"


class AccessModeError(RuntimeError):
    """HTTP transport refused to start because access-mode policy failed."""


def load_access_mode() -> str | None:
    """Return ``local`` or ``lan``, or None when the variable is unset."""

    raw = os.environ.get(ACCESS_MODE_ENV)
    if raw is None or not raw.strip():
        return None
    mode = raw.strip().casefold()
    if mode not in VALID_ACCESS_MODES:
        known = ", ".join(sorted(VALID_ACCESS_MODES))
        raise AccessModeError(f"{ACCESS_MODE_ENV}={raw!r} is not supported; use one of: {known}")
    return mode


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _dns_rebinding_enabled() -> bool:
    value = os.environ.get(_DNS_REBINDING_ENV)
    if value is None:
        return True
    return value.strip().lower() not in _FALSE_VALUES


def enforce_access_mode(*, insecure: bool, has_token: bool) -> str | None:
    """Refuse HTTP startup that violates the configured access mode.

    Returns the resolved mode, or None when ``MCP_ACCESS_MODE`` is unset.
    """

    mode = load_access_mode()
    if mode is None:
        return None
    if insecure:
        raise AccessModeError(
            f"{ACCESS_MODE_ENV}={mode} refuses {_INSECURE_ENV}=true. "
            "Set a bearer token instead of disabling authentication."
        )
    if not has_token:
        raise AccessModeError(
            f"{ACCESS_MODE_ENV}={mode} requires a bearer token "
            "(MCP_EMAIL_HTTP_BEARER_TOKEN, MCP_EMAIL_HTTP_BEARER_TOKENS, "
            "or MCP_EMAIL_HTTP_BEARER_TOKEN_FILE)."
        )
    if not _dns_rebinding_enabled():
        raise AccessModeError(
            f"{ACCESS_MODE_ENV}={mode} requires {_DNS_REBINDING_ENV}=true "
            "(do not disable Host/Origin DNS-rebinding protection)."
        )
    allowed_hosts = _split_csv(os.environ.get(_ALLOWED_HOSTS_ENV))
    if "*" in allowed_hosts:
        raise AccessModeError(
            f"{ACCESS_MODE_ENV}={mode} refuses {_ALLOWED_HOSTS_ENV}=*. "
            "List the NAS LAN IP:port and hostname explicitly."
        )
    if mode == "lan" and not allowed_hosts:
        raise AccessModeError(
            f"{ACCESS_MODE_ENV}=lan requires an explicit {_ALLOWED_HOSTS_ENV} "
            "list (NAS LAN IP:port and hostname the MCP client will send)."
        )
    return mode
