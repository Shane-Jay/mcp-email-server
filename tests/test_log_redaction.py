from __future__ import annotations

import os

import pytest

from mcp_email_server.log import (
    log_provider_failure,
    log_safe_failure,
    logger,
    register_secret,
    sanitize_log_text,
)


def test_logger_redacts_cache_key_and_bearer_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_CACHE_KEY", "super-secret-cache-key-value")
    monkeypatch.setenv("MCP_EMAIL_HTTP_BEARER_TOKEN", "bearer-token-value-xyz")
    captured: list[str] = []
    handler_id = logger.add(lambda message: captured.append(str(message.record["message"])), format="{message}")
    try:
        logger.info(
            "cache={} token={}",
            os.environ["MCP_EMAIL_CACHE_KEY"],
            os.environ["MCP_EMAIL_HTTP_BEARER_TOKEN"],
        )
    finally:
        logger.remove(handler_id)
    text = "\n".join(captured)
    assert "super-secret-cache-key-value" not in text
    assert "bearer-token-value-xyz" not in text
    assert "[redacted]" in text


def test_log_safe_failure_omits_exception_text_and_cache_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_CACHE_KEY", "cache-key-should-never-appear")
    captured: list[str] = []
    handler_id = logger.add(lambda message: captured.append(str(message.record["message"])), format="{message}")
    try:
        log_safe_failure(
            "cache failed",
            ValueError("cache-key-should-never-appear leaked in exception"),
        )
    finally:
        logger.remove(handler_id)
    text = "\n".join(captured)
    assert "cache-key-should-never-appear" not in text
    assert "leaked in exception" not in text
    assert "ValueError" in text
    assert "cache failed" in text


def test_sanitize_strips_login_args_and_sasl_without_registry():
    text = sanitize_log_text("A001 NO LOGIN alice hunter2-auth-code AUTHENTICATE PLAIN ABCDEFGHIJKLMNOP==")
    assert "hunter2-auth-code" not in text
    assert "ABCDEFGHIJKLMNOP" not in text
    assert "LOGIN [redacted]" in text
    auth = sanitize_log_text("AUTH PLAIN " + ("A" * 24))
    assert "AUTH [redacted]" in auth
    assert "A" * 24 not in auth
    phase = sanitize_log_text("SMTP phase=authenticate outcome=succeeded")
    assert phase == "SMTP phase=authenticate outcome=succeeded"


def test_registered_short_secret_is_redacted_regardless_of_length():
    register_secret("s3cret")
    captured: list[str] = []
    handler_id = logger.add(lambda message: captured.append(str(message.record["message"])), format="{message}")
    try:
        logger.error("IMAP LOGIN failed for user alice password=s3cret")
    finally:
        logger.remove(handler_id)
    text = "\n".join(captured)
    assert "s3cret" not in text
    assert "[redacted]" in text


def test_login_failure_log_does_not_leak_password():
    secret = "pw16char-authcode"  # noqa: S105
    register_secret(secret)
    captured: list[str] = []
    handler_id = logger.add(lambda message: captured.append(str(message.record["message"])), format="{message}")
    try:
        log_provider_failure(
            "IMAP login failed",
            RuntimeError(f"A001 NO [AUTHENTICATIONFAILED] LOGIN alice {secret}"),
        )
        logger.error("wire command was LOGIN alice {}", secret)
    finally:
        logger.remove(handler_id)
    text = "\n".join(captured)
    assert secret not in text
    assert "LOGIN [redacted]" in text
    assert "Traceback (most recent call last)" not in text


def _login_error() -> RuntimeError:
    try:
        raise RuntimeError("LOGIN alice leaked-password-value")  # noqa: TRY301
    except RuntimeError as exc:
        return exc


def test_provider_failure_tracebacks_stay_off_unless_flagged(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_SERVER_LOG_TRACEBACKS", raising=False)
    captured: list[str] = []
    handler_id = logger.add(lambda message: captured.append(str(message)), format="{message}")
    try:
        log_provider_failure("IMAP login failed", _login_error())
    finally:
        logger.remove(handler_id)
    serialized = "".join(captured)
    assert "leaked-password-value" not in serialized
    assert "Traceback (most recent call last)" not in serialized

    monkeypatch.setenv("MCP_EMAIL_SERVER_LOG_TRACEBACKS", "true")
    captured.clear()
    handler_id = logger.add(lambda message: captured.append(str(message)), format="{message}")
    try:
        log_provider_failure("IMAP login failed", _login_error())
    finally:
        logger.remove(handler_id)
    serialized = "".join(captured)
    assert "Traceback (most recent call last)" in serialized
