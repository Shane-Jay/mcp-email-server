from __future__ import annotations

import hashlib
import hmac

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_email_server.http_auth import (
    BearerAuthMiddleware,
    HttpAuthError,
    constant_time_token_match,
    extract_bearer_token,
    load_http_auth_settings,
    require_http_auth,
    wrap_http_app,
)


async def _ok(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _app(tokens: tuple[str, ...] = ("secret-token",)):
    inner = Starlette(routes=[Route("/mcp", _ok, methods=["POST"]), Route("/healthz", _health, methods=["GET"])])
    return BearerAuthMiddleware(inner, tokens)


def test_extract_bearer_token_parses_scheme():
    assert extract_bearer_token("Bearer abc") == "abc"
    assert extract_bearer_token("bearer abc") == "abc"
    assert extract_bearer_token("Basic abc") is None
    assert extract_bearer_token(None) is None
    assert extract_bearer_token("Bearer") is None


def test_constant_time_token_match_rotates_without_early_exit_shape():
    tokens = ("alpha-token", "beta-token")
    assert constant_time_token_match("beta-token", tokens) is True
    assert constant_time_token_match("gamma-token", tokens) is False
    assert constant_time_token_match("alpha-token", ()) is False
    assert constant_time_token_match("x", ("long-secret-token",)) is False
    digest_a = hashlib.sha256(b"x").digest()
    digest_b = hashlib.sha256(b"long-secret-token").digest()
    assert len(digest_a) == len(digest_b) == 32
    assert hmac.compare_digest(digest_a, digest_b) is False


def test_middleware_rejects_missing_and_wrong_tokens():
    client = TestClient(_app())
    denied = client.post("/mcp")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"].startswith("Bearer")
    wrong = client.post("/mcp", headers={"Authorization": "Bearer no-such-token"})
    assert wrong.status_code == 401


def test_middleware_accepts_any_configured_token_and_skips_healthz():
    client = TestClient(_app(("one", "two")))
    assert client.get("/healthz").status_code == 200
    assert client.post("/mcp", headers={"Authorization": "Bearer two"}).status_code == 200
    assert client.post("/mcp", headers={"Authorization": "Bearer one"}).json() == {"ok": True}


def test_require_http_auth_refuses_network_listen_without_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKENS", raising=False)
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKEN_FILE", raising=False)
    monkeypatch.delenv("MCP_EMAIL_HTTP_INSECURE", raising=False)
    with pytest.raises(HttpAuthError, match="Refusing to start"):
        require_http_auth()


def test_require_http_auth_allows_explicit_insecure_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKENS", raising=False)
    monkeypatch.setenv("MCP_EMAIL_HTTP_INSECURE", "true")
    settings = require_http_auth()
    assert settings.insecure is True
    assert settings.tokens == ()
    inner = Starlette(routes=[Route("/mcp", _ok, methods=["POST"])])
    wrapped = wrap_http_app(inner, settings)
    assert wrapped is inner


def test_token_file_and_rotation_list(monkeypatch: pytest.MonkeyPatch, tmp_path):
    secret = tmp_path / "token"
    secret.write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("MCP_EMAIL_HTTP_BEARER_TOKEN_FILE", str(secret))
    monkeypatch.setenv("MCP_EMAIL_HTTP_BEARER_TOKENS", "rot-a,rot-b")
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKEN", raising=False)
    settings = load_http_auth_settings()
    assert settings.tokens == ("file-token", "rot-a", "rot-b")
