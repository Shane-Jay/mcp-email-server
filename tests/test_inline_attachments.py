from __future__ import annotations

import base64
import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from mcp_email_server.app import save_draft, send_email
from mcp_email_server.application.mutations import (
    InlineAttachment,
    MutationAccountSnapshot,
    RecipientPolicyDeniedError,
    SendCommand,
    SendMutationOutcome,
    SentCopyMutationOutcome,
    TargetMutationOutcome,
    _validate_recipient_policy,
)
from mcp_email_server.emails.classic import EmailClient
from mcp_email_server.imap_keywords import ImapKeywordRegistry
from mcp_email_server.inline_payload import (
    InlineAttachmentInput,
    decode_inline_attachments,
    sanitize_attachment_filename,
)


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _input(
    filename: str, payload: bytes, *, mime: str | None = None, digest: str | None = None
) -> InlineAttachmentInput:
    return InlineAttachmentInput(
        filename=filename,
        content_base64=base64.b64encode(payload).decode("ascii"),
        mime_type=mime,
        sha256=digest,
    )


def _sent() -> SendMutationOutcome:
    return SendMutationOutcome(
        delivery=(TargetMutationOutcome("bob@example.test", "succeeded"),),
        sent_copy=SentCopyMutationOutcome("skipped"),
        message_id="<sent@example.test>",
    )


def test_filename_sanitization_strips_paths_controls_and_keeps_chinese() -> None:
    assert sanitize_attachment_filename("../x") == "x"
    assert sanitize_attachment_filename("..\\secret\\x") == "x"
    assert sanitize_attachment_filename("a\x00b.txt") == "ab.txt"
    assert sanitize_attachment_filename("报告.pdf") == "报告.pdf"
    assert len(sanitize_attachment_filename("a" * 400).encode("utf-8")) <= 255
    assert sanitize_attachment_filename("   ") == "attachment"
    assert sanitize_attachment_filename("R12:30.pdf") == "R12_30.pdf"
    assert sanitize_attachment_filename("\u202egpj.exe") == "gpj.exe"
    assert sanitize_attachment_filename("a\u200bb\u2028c\u2029.txt") == "abc.txt"


def test_duplicate_and_chinese_names_round_trip_through_mime() -> None:
    payload = _pdf_bytes()
    decoded = decode_inline_attachments(
        [
            _input("报告.pdf", payload),
            _input("报告.pdf", payload),
            _input("../x", b"hello"),
        ],
        path_count=0,
    )
    assert [item.filename for item in decoded] == ["报告.pdf", "报告-2.pdf", "x"]
    assert decoded[0].content_type == "application/pdf"
    assert decoded[2].content_type == "application/octet-stream"
    part = EmailClient.attachment_part_from_bytes(decoded[0].filename, decoded[0].content_type, decoded[0].payload)
    assert part.get_filename() == "报告.pdf"


def test_sha256_mismatch_and_bad_base64_raise_before_a_result() -> None:
    payload = b"abc"
    with pytest.raises(ValueError, match="sha256 does not match"):
        decode_inline_attachments([_input("a.txt", payload, digest="0" * 64)], path_count=0)
    with pytest.raises(ValueError, match="standard base64"):
        decode_inline_attachments(
            [InlineAttachmentInput(filename="a.txt", content_base64="@@@@")],
            path_count=0,
        )
    with pytest.raises(ValueError, match="data: prefix"):
        decode_inline_attachments(
            [InlineAttachmentInput(filename="a.txt", content_base64="data:text/plain;base64,YQ==")],
            path_count=0,
        )
    with pytest.raises(ValueError, match="whitespace"):
        decode_inline_attachments(
            [InlineAttachmentInput(filename="a.txt", content_base64="YQ==\n")],
            path_count=0,
        )


def test_matching_sha256_is_accepted() -> None:
    payload = b"hello"
    digest = hashlib.sha256(payload).hexdigest()
    decoded = decode_inline_attachments([_input("note.txt", payload, digest=digest.upper())], path_count=0)
    assert decoded[0].payload == payload


def test_inline_cap_uses_environment_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_SERVER_MAX_ATTACHMENT_BYTES", "4")
    with pytest.raises(ValueError, match="exceeds 4 bytes"):
        decode_inline_attachments([_input("a.txt", b"12345")], path_count=0)


def test_recipient_allowlist_still_denies_inline_compose() -> None:
    command = SendCommand(
        account_name="work",
        recipients=("bob@example.test",),
        subject="Hi",
        body="Body",
        inline_attachments=(InlineAttachment("a.txt", "text/plain", b"hi"),),
    )
    account = MutationAccountSnapshot(
        account_name="work",
        mode="legacy",
        allowed_senders=(),
        allowed_recipients=("alice@example.test",),
        report_blocked_mutations=False,
        can_send=True,
        tag_registry=ImapKeywordRegistry(),
    )
    with pytest.raises(RecipientPolicyDeniedError):
        _validate_recipient_policy(command, account)


@pytest.mark.asyncio
async def test_send_email_passes_inline_bytes_and_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_SERVER_ENABLE_INLINE_ATTACHMENTS", "true")
    payload = _pdf_bytes()
    handler = AsyncMock(return_value=_sent())
    with patch("mcp_email_server.app.send_email_command", handler):
        result = await send_email(
            account_name="work",
            recipients=["bob@example.test"],
            subject="Hi",
            body="Body",
            attachments_inline=[_input("note.pdf", payload)],
        )
    command = handler.await_args.args[0]
    assert isinstance(command, SendCommand)
    assert command.inline_attachments[0].payload == payload
    assert command.inline_attachments[0].content_type == "application/pdf"
    assert result.startswith("Email sent successfully to bob@example.test with 1 attachment(s): ")
    assert f"note.pdf size={len(payload)} sha256={hashlib.sha256(payload).hexdigest()}" in result


@pytest.mark.asyncio
async def test_rejected_inline_never_calls_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_SERVER_ENABLE_INLINE_ATTACHMENTS", "true")
    handler = AsyncMock()
    with patch("mcp_email_server.app.send_email_command", handler):
        with pytest.raises(ValueError, match="standard base64"):
            await send_email(
                account_name="work",
                recipients=["bob@example.test"],
                subject="Hi",
                body="Body",
                attachments_inline=[InlineAttachmentInput(filename="a.txt", content_base64="@@@@")],
            )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_flag_off_rejects_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_SERVER_ENABLE_INLINE_ATTACHMENTS", "false")
    handler = AsyncMock()
    with patch("mcp_email_server.app.send_email_command", handler):
        with pytest.raises(ValueError, match="ENABLE_INLINE_ATTACHMENTS"):
            await send_email(
                account_name="work",
                recipients=["bob@example.test"],
                subject="Hi",
                body="Body",
                attachments_inline=[_input("a.txt", b"hi")],
            )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowlist_denial_is_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_SERVER_ENABLE_INLINE_ATTACHMENTS", "true")
    handler = AsyncMock(side_effect=RecipientPolicyDeniedError("denied"))
    with (
        patch("mcp_email_server.app.send_email_command", handler),
        pytest.raises(ValueError, match="allowlist"),
    ):
        await send_email(
            account_name="work",
            recipients=["bob@example.test"],
            subject="Hi",
            body="Body",
            attachments_inline=[_input("a.txt", b"hi")],
        )
    assert handler.await_args.args[0].inline_attachments[0].payload == b"hi"


@pytest.mark.asyncio
async def test_save_draft_accepts_inline_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_SERVER_ENABLE_INLINE_ATTACHMENTS", "true")
    from mcp_email_server.application.mutations import AppendMutationOutcome

    handler = AsyncMock(
        return_value=AppendMutationOutcome(
            status="succeeded",
            message_id="<draft@example.test>",
            uid="9",
            mailbox="Drafts",
        )
    )
    runtime = type("Runtime", (), {})()
    runtime.mutations = type("Mutations", (), {"save_draft": type("Service", (), {"execute": handler})()})()
    with patch("mcp_email_server.app.get_application_runtime", return_value=runtime):
        result = await save_draft(
            account_name="work",
            subject="Draft",
            body="Body",
            attachments_inline=[_input("a.txt", b"hi")],
        )
    assert handler.await_args.args[0].inline_attachments[0].payload == b"hi"
    assert result.startswith("Draft save [succeeded: Drafts;")
    assert "size=2 sha256=" in result
