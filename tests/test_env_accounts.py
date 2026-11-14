from __future__ import annotations

from pathlib import Path

import pytest

import mcp_email_server.config as config_module
from mcp_email_server.config import EmailSettings, clear_settings_cache, get_settings
from mcp_email_server.env_accounts import collect_account_kwargs, iter_env_account_specs
from mcp_email_server.log import logger
from mcp_email_server.provider_presets import get_provider_preset


def test_named_accounts_and_presets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNTS", "work,icloud")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_work_EMAIL_ADDRESS", "user@company.example")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_work_PASSWORD", "auth-code-16chars")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_work_PRESET", "aliyun")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_icloud_EMAIL_ADDRESS", "user@icloud.com")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_icloud_PASSWORD", "app-specific-password")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_icloud_PRESET", "icloud")
    specs = iter_env_account_specs()
    names = [item["account_name"] for item in specs]
    assert names == ["work", "icloud"]
    work = next(item for item in specs if item["account_name"] == "work")
    assert work["imap_host"] == "imap.qiye.aliyun.com"
    assert work["smtp_port"] == 465
    icloud = next(item for item in specs if item["account_name"] == "icloud")
    assert icloud["smtp_port"] == 587
    assert icloud["smtp_start_ssl"] is True
    assert icloud["smtp_ssl"] is False


def test_password_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    secret = tmp_path / "pw"
    secret.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("MCP_EMAIL_SERVER_EMAIL_ADDRESS", "user@163.com")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("MCP_EMAIL_SERVER_PRESET", "163")
    spec = collect_account_kwargs("MCP_EMAIL_SERVER_")
    assert spec is not None
    assert spec["password"] == "file-secret"  # noqa: S105
    assert spec["imap_id_mode"] == "always"
    assert spec["imap_search_mode"] == "local"
    account = EmailSettings.from_env_kwargs(spec)
    assert account is not None
    assert account.incoming.host == "imap.163.com"


def test_netease_presets_default_save_to_sent_false_unless_explicit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_EMAIL_ADDRESS", "user@163.com")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PASSWORD", "auth-code-16chars")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PRESET", "163")
    implied = collect_account_kwargs("MCP_EMAIL_SERVER_")
    assert implied is not None
    assert implied["save_to_sent"] is False
    monkeypatch.setenv("MCP_EMAIL_SERVER_SAVE_TO_SENT", "true")
    explicit = collect_account_kwargs("MCP_EMAIL_SERVER_")
    assert explicit is not None
    assert explicit["save_to_sent"] is True
    monkeypatch.delenv("MCP_EMAIL_SERVER_SAVE_TO_SENT")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PRESET", "126")
    netease_126 = collect_account_kwargs("MCP_EMAIL_SERVER_")
    assert netease_126 is not None
    assert netease_126["save_to_sent"] is False
    monkeypatch.setenv("MCP_EMAIL_SERVER_PRESET", "aliyun")
    monkeypatch.setenv("MCP_EMAIL_SERVER_EMAIL_ADDRESS", "user@company.example")
    other = collect_account_kwargs("MCP_EMAIL_SERVER_")
    assert other is not None
    assert "save_to_sent" not in other
    account = EmailSettings.from_env_kwargs(other)
    assert account is not None
    assert account.save_to_sent is True


def test_unreadable_password_file_skips_only_that_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    good_secret = tmp_path / "good-secret"
    good_secret.write_text("good-account-secret\n", encoding="utf-8")
    bad_dir = tmp_path / "not-a-file"
    bad_dir.mkdir()
    monkeypatch.delenv("MCP_EMAIL_SERVER_EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("MCP_EMAIL_SERVER_PASSWORD", raising=False)
    monkeypatch.delenv("MCP_EMAIL_SERVER_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNTS", "good,bad")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_good_EMAIL_ADDRESS", "good@example.test")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_good_PASSWORD_FILE", str(good_secret))
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_good_IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_bad_EMAIL_ADDRESS", "bad@example.test")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_bad_PASSWORD_FILE", str(bad_dir))
    monkeypatch.setenv("MCP_EMAIL_SERVER_ACCOUNT_bad_IMAP_HOST", "imap.example.test")
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.toml")
    clear_settings_cache()
    captured: list[str] = []
    sink = logger.add(lambda message: captured.append(str(message.record["message"])), level="ERROR")
    try:
        specs = iter_env_account_specs()
        loaded = [account.account_name for account in get_settings(reload=True).emails]
    finally:
        logger.remove(sink)
        clear_settings_cache()
    assert [item["account_name"] for item in specs] == ["good"]
    assert loaded == ["good"]
    logged = "\n".join(captured)
    assert "MCP_EMAIL_SERVER_ACCOUNT_bad_PASSWORD_FILE" in logged
    assert str(bad_dir) in logged
    assert "good-account-secret" not in logged


def test_unknown_preset_is_skipped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_EMAIL_ADDRESS", "a@b.test")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PASSWORD", "x")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PRESET", "not-a-real-provider")
    assert collect_account_kwargs("MCP_EMAIL_SERVER_") is None


def test_provider_preset_aliases():
    assert get_provider_preset("qiye.aliyun.com").name == "aliyun"
    assert get_provider_preset("mails.tsinghua.edu.cn").imap_port == 993
    assert get_provider_preset("qq").imap_host == "imap.qq.com"
