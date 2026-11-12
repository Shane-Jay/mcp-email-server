from __future__ import annotations

import pytest

from mcp_email_server.access_mode import AccessModeError, enforce_access_mode, load_access_mode
from mcp_email_server.http_auth import HttpAuthError, require_http_auth


def test_unset_access_mode_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_ACCESS_MODE", raising=False)
    assert load_access_mode() is None
    assert enforce_access_mode(insecure=True, has_token=False) is None


def test_unknown_access_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "tailscale")
    with pytest.raises(AccessModeError, match="not supported"):
        load_access_mode()


def test_lan_refuses_insecure_even_with_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "lan")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.1.50:9557,nas.local:9557")
    monkeypatch.setenv("MCP_ENABLE_DNS_REBINDING_PROTECTION", "true")
    with pytest.raises(AccessModeError, match="refuses MCP_EMAIL_HTTP_INSECURE"):
        enforce_access_mode(insecure=True, has_token=True)


def test_lan_requires_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "LAN")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.1.50:9557")
    with pytest.raises(AccessModeError, match="requires a bearer token"):
        enforce_access_mode(insecure=False, has_token=False)


def test_lan_requires_explicit_allowed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "lan")
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    with pytest.raises(AccessModeError, match="MCP_ALLOWED_HOSTS"):
        enforce_access_mode(insecure=False, has_token=True)


def test_lan_refuses_wildcard_hosts_and_disabled_dns_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "lan")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")
    with pytest.raises(AccessModeError, match="refuses"):
        enforce_access_mode(insecure=False, has_token=True)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "nas.local:9557")
    monkeypatch.setenv("MCP_ENABLE_DNS_REBINDING_PROTECTION", "false")
    with pytest.raises(AccessModeError, match="DNS-rebinding"):
        enforce_access_mode(insecure=False, has_token=True)


def test_lan_accepts_token_and_explicit_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "lan")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.1.50:9557,nas.local:9557")
    monkeypatch.delenv("MCP_ENABLE_DNS_REBINDING_PROTECTION", raising=False)
    assert enforce_access_mode(insecure=False, has_token=True) == "lan"


def test_local_requires_token_but_not_extra_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "local")
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    assert enforce_access_mode(insecure=False, has_token=True) == "local"
    with pytest.raises(AccessModeError, match="refuses MCP_EMAIL_HTTP_INSECURE"):
        enforce_access_mode(insecure=True, has_token=True)


def test_require_http_auth_then_lan_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ACCESS_MODE", "lan")
    monkeypatch.setenv("MCP_EMAIL_HTTP_INSECURE", "true")
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKENS", raising=False)
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKEN_FILE", raising=False)
    settings = require_http_auth()
    assert settings.insecure is True
    with pytest.raises(AccessModeError, match="refuses MCP_EMAIL_HTTP_INSECURE"):
        enforce_access_mode(insecure=settings.insecure, has_token=bool(settings.tokens))
    monkeypatch.delenv("MCP_EMAIL_HTTP_INSECURE", raising=False)
    with pytest.raises(HttpAuthError, match="Refusing to start"):
        require_http_auth()
