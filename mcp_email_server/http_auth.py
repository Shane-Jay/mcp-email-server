"""Bearer-token authentication for SSE and Streamable HTTP transports.

Tokens are compared as SHA-256 digests with ``hmac.compare_digest`` so token
length does not leak through ``compare_digest``'s equal-length requirement.
Multiple tokens are supported for rotation: every configured token is compared
and the results are OR-ed without an early return.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_email_server.secrets_io import read_secret_from_env, read_secret_list_from_env

_FALSE_VALUES = {"0", "false", "no", "off"}
_PUBLIC_PATHS = frozenset({"/healthz", "/health"})
_AUTH_ENV = "MCP_EMAIL_HTTP_BEARER_TOKEN"
_AUTH_FILE_ENV = "MCP_EMAIL_HTTP_BEARER_TOKEN_FILE"
_AUTH_LIST_ENV = "MCP_EMAIL_HTTP_BEARER_TOKENS"
_INSECURE_ENV = "MCP_EMAIL_HTTP_INSECURE"


class HttpAuthError(RuntimeError):
    """HTTP transport refused to start because authentication is not configured."""


@dataclass(frozen=True)
class HttpAuthSettings:
    """Resolved bearer-token policy for one HTTP listener."""

    tokens: tuple[str, ...]
    insecure: bool

    @property
    def required(self) -> bool:
        return not self.insecure

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES and bool(raw.strip())


def load_http_auth_settings() -> HttpAuthSettings:
    """Load bearer tokens from env, a rotation list, and/or a secret file."""

    tokens: list[str] = []
    single = read_secret_from_env(_AUTH_ENV, file_env_name=_AUTH_FILE_ENV)
    if single:
        tokens.append(single)
    tokens.extend(read_secret_list_from_env(_AUTH_LIST_ENV))
    unique = tuple(dict.fromkeys(token for token in tokens if token))
    return HttpAuthSettings(tokens=unique, insecure=_parse_bool_env(_INSECURE_ENV, False))


def require_http_auth(settings: HttpAuthSettings | None = None) -> HttpAuthSettings:
    """Refuse network-listening HTTP unless a token or an explicit insecure flag is set."""

    resolved = settings if settings is not None else load_http_auth_settings()
    if resolved.required and not resolved.tokens:
        raise HttpAuthError(
            "Refusing to start an HTTP transport without bearer-token authentication. "
            f"Set {_AUTH_ENV}, {_AUTH_LIST_ENV}, or {_AUTH_FILE_ENV}, "
            f"or set {_INSECURE_ENV}=true for an isolated development listener."
        )
    return resolved


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def constant_time_token_match(provided: str, tokens: tuple[str, ...]) -> bool:
    """Return True if ``provided`` matches any configured token, in constant time per token."""

    if not tokens:
        return False
    provided_digest = _digest(provided)
    matched = False
    for token in tokens:
        matched = hmac.compare_digest(provided_digest, _digest(token)) or matched
    return matched


def extract_bearer_token(authorization: str | None) -> str | None:
    """Parse a Bearer token from an Authorization header value."""

    if authorization is None:
        return None
    scheme, _, remainder = authorization.partition(" ")
    if scheme.lower() != "bearer" or not remainder:
        return None
    token = remainder.strip()
    return token or None


def _unauthorized() -> Response:
    return JSONResponse(
        {"error": "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="mcp-email-server"'},
    )


class BearerAuthMiddleware:
    """ASGI middleware that requires a matching Bearer token on MCP HTTP routes."""

    def __init__(self, app: ASGIApp, tokens: tuple[str, ...], *, public_paths: frozenset[str] = _PUBLIC_PATHS) -> None:
        self.app = app
        self.tokens = tokens
        self.public_paths = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path in self.public_paths:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        token = extract_bearer_token(headers.get("authorization"))
        if token is None or not constant_time_token_match(token, self.tokens):
            response = _unauthorized()
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def wrap_http_app(app: ASGIApp, settings: HttpAuthSettings | None = None) -> ASGIApp:
    """Wrap a Starlette/ASGI app with bearer auth when tokens are configured."""

    resolved = settings if settings is not None else load_http_auth_settings()
    if not resolved.enabled:
        return app
    return BearerAuthMiddleware(app, resolved.tokens)


async def healthz(_request: Request) -> Response:
    """Unauthenticated liveness probe for container health checks."""

    return JSONResponse({"status": "ok"})
