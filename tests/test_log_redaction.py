from __future__ import annotations

import os

import pytest

from mcp_email_server.log import log_safe_failure, logger


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
