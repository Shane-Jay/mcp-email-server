from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aioimaplib import Response

from mcp_email_server.emails.classic import _send_imap_id
from mcp_email_server.provider_presets import get_provider_preset


def _make_imap(*, capabilities: tuple[object, ...] = (b"imap4rev1", b"id")):
    imap = MagicMock()
    imap.timeout = 5
    imap.id = AsyncMock()
    protocol = MagicMock()
    protocol.new_tag.return_value = "A001"
    protocol.execute = AsyncMock(return_value=Response("OK", []))
    protocol.capabilities = capabilities
    protocol.transport = MagicMock()
    protocol.pending_async_commands = {}
    imap.protocol = protocol
    return imap


@pytest.mark.asyncio
async def test_auto_mode_skips_when_capability_missing():
    imap = _make_imap(capabilities=("IMAP4REV1",))
    await _send_imap_id(imap, mode="auto")
    imap.protocol.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_always_mode_sends_163_compact_id_without_capability():
    imap = _make_imap(capabilities=("IMAP4REV1",))
    await _send_imap_id(imap, mode="always")
    imap.protocol.execute.assert_awaited_once()
    command = imap.protocol.execute.await_args.args[0]
    assert command.name == "ID"
    assert command.args == ('("name" "mcp-email-server" "version" "1.0.0")',)


@pytest.mark.asyncio
async def test_never_mode_skips_even_when_advertised():
    imap = _make_imap(capabilities=("IMAP4REV1", "ID"))
    await _send_imap_id(imap, mode="never")
    imap.protocol.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_pooled_authenticated_connection_does_not_resend_id():
    imap = _make_imap()
    imap._mcp_authenticated = True
    await _send_imap_id(imap, mode="always")
    imap.protocol.execute.assert_not_awaited()


def test_163_and_tsinghua_presets_force_imap_id():
    assert get_provider_preset("163").imap_id_mode == "always"
    assert get_provider_preset("tsinghua").imap_id_mode == "always"
    assert get_provider_preset("icloud").imap_id_mode == "auto"
