"""Filter the MCP tool catalog by allow/deny lists without unregistering tools."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from typing import Any

_ALLOW_ENV = "MCP_EMAIL_SERVER_TOOL_ALLOW"
_DENY_ENV = "MCP_EMAIL_SERVER_TOOL_DENY"
# Hidden unless an account explicitly enables raw protocol access.
DEFAULT_DENIED_TOOLS = frozenset({"email_command"})


def _split_names(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def configured_tool_allowlist() -> tuple[str, ...] | None:
    """Return the allowlist, or None when every tool (minus denials) is advertised."""

    if _ALLOW_ENV not in os.environ:
        return None
    return _split_names(os.environ.get(_ALLOW_ENV))


def configured_tool_denylist(*, hide_protocol_command: bool = True) -> frozenset[str]:
    """Return tool names that must not appear in ``tools/list``."""

    denied = set(_split_names(os.environ.get(_DENY_ENV)))
    if hide_protocol_command:
        denied.update(DEFAULT_DENIED_TOOLS)
    return frozenset(denied)


def filter_tool_names(
    names: Iterable[str],
    *,
    allow: Sequence[str] | None,
    deny: Iterable[str],
) -> list[str]:
    """Apply allow then deny. An empty allowlist advertises no tools."""

    deny_set = set(deny)
    if allow is None:
        return [name for name in names if name not in deny_set]
    allowed = set(allow)
    return [name for name in names if name in allowed and name not in deny_set]


def filter_mcp_tools(
    tools: Sequence[Any],
    *,
    allow: Sequence[str] | None = None,
    deny: Iterable[str] | None = None,
    hide_protocol_command: bool = True,
) -> list[Any]:
    """Filter FastMCP ``Tool`` objects by ``name``."""

    allow_list = configured_tool_allowlist() if allow is None else allow
    deny_list = configured_tool_denylist(hide_protocol_command=hide_protocol_command) if deny is None else deny
    permitted = set(
        filter_tool_names(
            (getattr(tool, "name", "") for tool in tools),
            allow=allow_list,
            deny=deny_list,
        )
    )
    return [tool for tool in tools if getattr(tool, "name", "") in permitted]
