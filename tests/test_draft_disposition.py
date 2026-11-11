from __future__ import annotations

import pytest

from mcp_email_server.application.mutations import SentCopyMutationOutcome
from mcp_email_server.draft_disposition import (
    configured_send_draft_disposition,
    draft_action_after_send,
)


def test_default_disposition_is_safe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_SERVER_SEND_DRAFT", raising=False)
    assert configured_send_draft_disposition() == "safe"
    monkeypatch.setenv("MCP_EMAIL_SERVER_SEND_DRAFT", "MOVE")
    assert configured_send_draft_disposition() == "move"
    monkeypatch.setenv("MCP_EMAIL_SERVER_SEND_DRAFT", "bogus")
    with pytest.raises(ValueError, match="MCP_EMAIL_SERVER_SEND_DRAFT"):
        configured_send_draft_disposition()


@pytest.mark.parametrize(
    ("disposition", "sent_status", "expected"),
    [
        ("safe", "succeeded", "delete"),
        ("safe", "skipped", "keep"),
        ("safe", "failed", "keep"),
        ("delete", "skipped", "delete"),
        ("delete", "succeeded", "delete"),
        ("move", "skipped", "move"),
        ("move", "succeeded", "delete"),
        ("keep", "succeeded", "keep"),
        ("keep", "skipped", "keep"),
    ],
)
def test_draft_action_after_send(disposition, sent_status, expected):
    sent_copy = SentCopyMutationOutcome(sent_status, mailbox="Sent" if sent_status == "succeeded" else None)
    assert draft_action_after_send(disposition, sent_copy) == expected
