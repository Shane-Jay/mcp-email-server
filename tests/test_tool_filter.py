from __future__ import annotations

from types import SimpleNamespace

from mcp_email_server.tool_filter import DEFAULT_DENIED_TOOLS, filter_mcp_tools, filter_tool_names


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
