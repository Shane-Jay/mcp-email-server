from __future__ import annotations

import pytest

from mcp_email_server.config import EmailSettings
from mcp_email_server.env_accounts import collect_account_kwargs, iter_env_account_specs
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


def test_unknown_preset_is_skipped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_EMAIL_ADDRESS", "a@b.test")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PASSWORD", "x")
    monkeypatch.setenv("MCP_EMAIL_SERVER_PRESET", "not-a-real-provider")
    assert collect_account_kwargs("MCP_EMAIL_SERVER_") is None


def test_provider_preset_aliases():
    assert get_provider_preset("qiye.aliyun.com").name == "aliyun"
    assert get_provider_preset("mails.tsinghua.edu.cn").imap_port == 993
    assert get_provider_preset("qq").imap_host == "imap.qq.com"
