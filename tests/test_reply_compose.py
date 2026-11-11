from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mcp_email_server.app import reply_email, save_draft, send_draft
from mcp_email_server.application.accounts import AvailableAccount
from mcp_email_server.application.mutations import DeleteCommand, RecipientPolicyDeniedError
from mcp_email_server.emails.models import EmailBodyResponse, EmailContentBatchResponse
from mcp_email_server.reply_compose import (
    build_reply_body,
    build_thread_headers,
    format_original_date,
    quote_plain_original,
    reply_recipients,
    reply_subject,
)


def test_reply_subject_does_not_stack_prefix():
    assert reply_subject("Hello") == "Re: Hello"
    assert reply_subject("Re: Hello") == "Re: Hello"
    assert reply_subject("RE: Hello") == "RE: Hello"
    assert reply_subject("") == "Re:"


def test_quote_and_body():
    quoted = quote_plain_original(sender="Ada <ada@example.test>", date="Mon, 01 Jan 2024 00:00 +0000", body="Hi\nThere")
    assert quoted.startswith("On Mon, 01 Jan 2024 00:00 +0000, Ada <ada@example.test> wrote:")
    assert "> Hi" in quoted
    assert "> There" in quoted
    combined = build_reply_body(
        note="Thanks",
        sender="Ada <ada@example.test>",
        date="Mon",
        original_body="Hi",
    )
    assert combined.startswith("Thanks\n\n")
    assert "> Hi" in combined


def test_thread_headers_append_message_id():
    in_reply_to, references = build_thread_headers(
        message_id="mid-2@example.test",
        existing_references="<mid-1@example.test>",
    )
    assert in_reply_to == "<mid-2@example.test>"
    assert references == "<mid-1@example.test> <mid-2@example.test>"
    assert build_thread_headers(message_id=None) == (None, None)


def test_reply_and_reply_all_recipients():
    to, cc = reply_recipients(
        mode="reply",
        sender="Ada <ada@example.test>",
        to_header="Me <me@example.test>, Bob <bob@example.test>",
        cc_header="Cara <cara@example.test>",
        self_address="me@example.test",
    )
    assert to == ["ada@example.test"]
    assert cc == []
    to_all, cc_all = reply_recipients(
        mode="reply-all",
        sender="Ada <ada@example.test>",
        to_header="Me <me@example.test>, Bob <bob@example.test>",
        cc_header="Cara <cara@example.test>",
        self_address="me@example.test",
    )
    assert to_all == ["ada@example.test"]
    assert cc_all == ["bob@example.test", "cara@example.test"]


def test_format_original_date_accepts_datetime():
    stamp = datetime(2024, 1, 2, 3, 4, tzinfo=UTC)
    text = format_original_date(stamp)
    assert "2024" in text
    assert "Jan" in text
    assert format_original_date("Mon, 01 Jan 2024 00:00:00 +0000")
    assert format_original_date(None) == ""


def _body() -> EmailBodyResponse:
    return EmailBodyResponse(
        email_id="42",
        message_id="<mid-1@example.test>",
        subject="Hello",
        sender="Ada <ada@example.test>",
        recipients=["me@example.test", "bob@example.test"],
        date=datetime(2024, 1, 1, tzinfo=UTC),
        attachments=[],
        in_reply_to=None,
        references=None,
        body="Original body",
    )


@pytest.mark.asyncio
async def test_reply_email_tool_sets_thread_headers_and_reuses_send():
    batch = EmailContentBatchResponse(emails=[_body()], requested_count=1, retrieved_count=1, failed_ids=[])
    account = AvailableAccount(
        account_name="personal",
        account_type="email",
        description="personal",
        email_address="me@example.test",
        can_receive=True,
        can_send=True,
    )
    with (
        patch("mcp_email_server.app.get_email_content_query", AsyncMock(return_value=batch)),
        patch("mcp_email_server.app.list_effective_accounts", return_value=[account]),
        patch("mcp_email_server.app.send_email", AsyncMock(return_value="Email sent successfully")) as send,
    ):
        result = await reply_email(
            account_name="personal",
            email_id="42",
            body="Thanks",
            mailbox="INBOX",
            reply_all=True,
        )
    assert result == "Email sent successfully"
    kwargs = send.await_args.kwargs
    assert kwargs["recipients"] == ["ada@example.test"]
    assert kwargs["subject"] == "Re: Hello"
    assert kwargs["in_reply_to"] == "<mid-1@example.test>"
    assert "Thanks" in kwargs["body"]
    assert "> Original body" in kwargs["body"]
    assert "bob@example.test" in (kwargs["cc"] or [])


@pytest.mark.asyncio
async def test_send_draft_tool_deletes_draft_after_clean_send():
    draft = _body()
    draft.subject = "Draft subject"
    draft.recipients = ["ada@example.test"]
    batch = EmailContentBatchResponse(emails=[draft], requested_count=1, retrieved_count=1, failed_ids=[])
    delete = AsyncMock()
    with (
        patch("mcp_email_server.app.get_email_content_query", AsyncMock(return_value=batch)),
        patch("mcp_email_server.app.send_email", AsyncMock(return_value="Email sent successfully [id: 1]")) as send,
        patch("mcp_email_server.app.delete_emails_command", delete),
    ):
        result = await send_draft(account_name="personal", email_id="42", mailbox="Drafts")
    assert result.startswith("Email sent successfully")
    send.assert_awaited_once()
    delete.assert_awaited_once()
    command = delete.await_args.args[0]
    assert isinstance(command, DeleteCommand)
    assert command.email_ids == ("42",)
    assert command.mailbox == "Drafts"


@pytest.mark.asyncio
async def test_reply_email_enforces_recipient_allowlist():
    batch = EmailContentBatchResponse(emails=[_body()], requested_count=1, retrieved_count=1, failed_ids=[])
    account = AvailableAccount(
        account_name="personal",
        account_type="email",
        description="personal",
        email_address="me@example.test",
        can_receive=True,
        can_send=True,
    )
    with (
        patch("mcp_email_server.app.get_email_content_query", AsyncMock(return_value=batch)),
        patch("mcp_email_server.app.list_effective_accounts", return_value=[account]),
        patch(
            "mcp_email_server.app.send_email_command",
            AsyncMock(side_effect=RecipientPolicyDeniedError("recipient policy denied one or more addresses")),
        ),
    ):
        with pytest.raises(ValueError, match="not in allowlist"):
            await reply_email(account_name="personal", email_id="42", body="Thanks")


@pytest.mark.asyncio
async def test_send_draft_enforces_recipient_allowlist_and_keeps_the_draft():
    draft = _body()
    draft.recipients = ["blocked@evil.test"]
    batch = EmailContentBatchResponse(emails=[draft], requested_count=1, retrieved_count=1, failed_ids=[])
    delete = AsyncMock()
    with (
        patch("mcp_email_server.app.get_email_content_query", AsyncMock(return_value=batch)),
        patch(
            "mcp_email_server.app.send_email_command",
            AsyncMock(side_effect=RecipientPolicyDeniedError("recipient policy denied one or more addresses")),
        ),
        patch("mcp_email_server.app.delete_emails_command", delete),
    ):
        with pytest.raises(ValueError, match="not in allowlist"):
            await send_draft(account_name="personal", email_id="42", mailbox="Drafts")
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_draft_enforces_recipient_allowlist():
    execute = AsyncMock(side_effect=RecipientPolicyDeniedError("recipient policy denied one or more addresses"))
    runtime = SimpleNamespace(mutations=SimpleNamespace(save_draft=SimpleNamespace(execute=execute)))
    with patch("mcp_email_server.app.get_application_runtime", return_value=runtime):
        with pytest.raises(ValueError, match="not in allowlist"):
            await save_draft(
                account_name="personal",
                subject="Draft",
                body="Hello",
                recipients=["blocked@evil.test"],
            )
    execute.assert_awaited_once()
