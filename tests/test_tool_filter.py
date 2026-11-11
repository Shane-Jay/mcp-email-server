from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_email_server.app import mcp
from mcp_email_server.tool_filter import (
    DEFAULT_DENIED_TOOLS,
    filter_mcp_tools,
    filter_tool_names,
    tool_is_enabled,
)


def test_default_hides_raw_protocol_command():
    names = ["list_available_accounts", "send_email", "email_command"]
    assert filter_tool_names(names, allow=None, deny=DEFAULT_DENIED_TOOLS) == [
        "list_available_accounts",
        "send_email",
    ]


def test_allow_and_deny_lists():
    names = ["a", "b", "c", "email_command"]
    assert filter_tool_names(names, allow=("a", "c", "email_command"), deny=("c",)) == ["a", "email_command"]


def test_filter_mcp_tools_uses_name_attribute():
    tools = [SimpleNamespace(name="send_email"), SimpleNamespace(name="email_command")]
    kept = filter_mcp_tools(tools, allow=None, deny=None, hide_protocol_command=True)
    assert [tool.name for tool in kept] == ["send_email"]


def test_tool_is_enabled_matches_allow_and_deny(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_SERVER_TOOL_ALLOW", raising=False)
    monkeypatch.setenv("MCP_EMAIL_SERVER_TOOL_DENY", "send_email")
    assert tool_is_enabled("list_available_accounts") is True
    assert tool_is_enabled("send_email") is False
    assert tool_is_enabled("email_command", hide_protocol_command=True) is False
    assert tool_is_enabled("email_command", hide_protocol_command=False) is True
    monkeypatch.setenv("MCP_EMAIL_SERVER_TOOL_ALLOW", "list_available_accounts")
    assert tool_is_enabled("list_available_accounts") is True
    assert tool_is_enabled("get_emails_content") is False


def test_empty_allowlist_enables_nothing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_TOOL_ALLOW", "")
    assert tool_is_enabled("list_available_accounts") is False
    assert tool_is_enabled("email_command", hide_protocol_command=False) is False


@pytest.mark.asyncio
async def test_denied_tool_is_not_callable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_TOOL_DENY", "send_email")
    monkeypatch.setattr("mcp_email_server.app._protocol_command_advertised", lambda: False)
    result = await mcp.call_tool(
        "send_email",
        {"account_name": "x", "recipients": ["a@b.test"], "subject": "s", "body": "b"},
    )
    assert result.isError
    assert "tool_unavailable" in result.content[0].text


@pytest.mark.asyncio
async def test_unlisted_tool_is_not_callable_when_allowlist_is_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_TOOL_ALLOW", "list_available_accounts")
    monkeypatch.setattr("mcp_email_server.app._protocol_command_advertised", lambda: True)
    result = await mcp.call_tool(
        "send_email",
        {"account_name": "x", "recipients": ["a@b.test"], "subject": "s", "body": "b"},
    )
    assert result.isError
    assert "tool_unavailable" in result.content[0].text


@pytest.mark.asyncio
async def test_email_command_rejected_at_call_time_without_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_SERVER_TOOL_ALLOW", raising=False)
    monkeypatch.delenv("MCP_EMAIL_SERVER_TOOL_DENY", raising=False)
    monkeypatch.setattr("mcp_email_server.app._protocol_command_advertised", lambda: False)
    result = await mcp.call_tool("email_command", {"commands": [{"command": "HELP"}]})
    assert result.isError
    assert result.structuredContent == {"error": "tool_unavailable"}
    assert "HELP" not in result.content[0].text
    listed = await mcp.list_tools()
    assert all(tool.name != "email_command" for tool in listed)


@pytest.mark.asyncio
async def test_email_command_callable_when_an_account_opts_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_SERVER_TOOL_ALLOW", raising=False)
    monkeypatch.delenv("MCP_EMAIL_SERVER_TOOL_DENY", raising=False)
    monkeypatch.setattr("mcp_email_server.app._protocol_command_advertised", lambda: True)
    result = await mcp.call_tool("email_command", {"commands": [{"command": "HELP"}]})
    assert not result.isError
    assert result.structuredContent["results"][0]["code"] == "HELP"
