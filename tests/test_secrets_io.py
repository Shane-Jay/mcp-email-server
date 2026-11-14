from __future__ import annotations

from pathlib import Path

import pytest

from mcp_email_server.secrets_io import SecretFileError, read_secret_file, read_secret_from_env


def test_missing_and_empty_secret_files_are_unset(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    empty = tmp_path / "empty"
    empty.write_bytes(b"\n")
    assert read_secret_file(missing, source_env="MCP_EMAIL_SERVER_PASSWORD_FILE") is None
    assert read_secret_file(empty, source_env="MCP_EMAIL_SERVER_PASSWORD_FILE") is None


def test_unreadable_secret_file_names_env_and_path_without_contents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "password"
    secret.write_text("super-secret-value\n", encoding="utf-8")

    def _denied(self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", _denied)
    with pytest.raises(SecretFileError, match="MCP_EMAIL_SERVER_PASSWORD_FILE") as exc_info:
        read_secret_file(secret, source_env="MCP_EMAIL_SERVER_PASSWORD_FILE")
    message = str(exc_info.value)
    assert str(secret) in message
    assert "super-secret-value" not in message
    assert "PermissionError" in message


def test_non_utf8_secret_file_does_not_echo_bytes(tmp_path: Path) -> None:
    secret = tmp_path / "password"
    secret.write_bytes(b"\xff\xfe secret-bytes")
    with pytest.raises(SecretFileError, match="UnicodeDecodeError") as exc_info:
        read_secret_file(secret, source_env="MCP_EMAIL_SERVER_PASSWORD_FILE")
    assert "secret-bytes" not in str(exc_info.value)


def test_directory_secret_path_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MCP_EMAIL_HTTP_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("MCP_EMAIL_HTTP_BEARER_TOKEN_FILE", str(tmp_path))
    with pytest.raises(SecretFileError, match="MCP_EMAIL_HTTP_BEARER_TOKEN_FILE"):
        read_secret_from_env("MCP_EMAIL_HTTP_BEARER_TOKEN", file_env_name="MCP_EMAIL_HTTP_BEARER_TOKEN_FILE")
