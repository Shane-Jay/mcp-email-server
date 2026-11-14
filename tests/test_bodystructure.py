from __future__ import annotations

import asyncio
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import (
    EmailClient,
    _extract_bodystructure,
    _parse_imap_sexp,
    attachments_from_bodystructure,
)

_MIXED_PDF = (
    '("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 12 1 NIL NIL NIL NIL)'
    '("APPLICATION" "PDF" ("NAME" "note.pdf") NIL NIL "BASE64" 1234 NIL '
    '("ATTACHMENT" ("FILENAME" "note.pdf")) NIL) "MIXED"'
)


def test_attachments_from_mixed_pdf_bodystructure() -> None:
    structure, _ = _parse_imap_sexp(f"({_MIXED_PDF})")
    names, info = attachments_from_bodystructure(structure)
    assert names == ["note.pdf"]
    assert info == [{"filename": "note.pdf", "size": 1234 * 3 // 4, "content_type": "application/pdf"}]


def test_base64_bodystructure_size_uses_three_quarter_estimate() -> None:
    structure, _ = _parse_imap_sexp(
        '(("APPLICATION" "PDF" ("NAME" "exact.pdf") NIL NIL "BASE64" 1200 NIL NIL NIL)'
        '("TEXT" "PLAIN" ("NAME" "note.txt") NIL NIL "7BIT" 1200 NIL NIL NIL) "MIXED")'
    )
    _names, info = attachments_from_bodystructure(structure)
    assert info[0]["size"] == 1200 * 3 // 4
    assert info[1]["size"] == 1200


def test_alternative_without_filename_is_not_an_attachment() -> None:
    structure, _ = _parse_imap_sexp(
        '(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 5 1)'
        '("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "7BIT" 9 1) "ALTERNATIVE")'
    )
    names, info = attachments_from_bodystructure(structure)
    assert names == []
    assert info == []


def test_named_multipart_related_is_one_attachment_root() -> None:
    structure, _ = _parse_imap_sexp(
        '((("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "7BIT" 20 1)'
        '("IMAGE" "PNG" ("NAME" "pic.png") NIL NIL "BASE64" 80 NIL NIL NIL) "RELATED"'
        ' ("BOUNDARY" "inner") ("ATTACHMENT" ("FILENAME" "gallery.mhtml")) NIL) "MIXED")'
    )
    names, info = attachments_from_bodystructure(structure)
    assert names == ["gallery.mhtml"]
    assert info[0]["content_type"] == "multipart/related"


def test_extract_bodystructure_from_trailing_fetch_line() -> None:
    structure = _extract_bodystructure([
        b"1 FETCH (UID 9 FLAGS (\\Seen) BODY[HEADER] {12}",
        b' BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 12 1) "MIXED"))',
    ])
    names, _info = attachments_from_bodystructure(structure)
    assert names == []


def _client() -> EmailClient:
    return EmailClient(
        EmailServer(
            user_name="user@example.test",
            password=SecretStr("secret"),
            host="imap.example.test",
            port=993,
        ),
        sender="User <user@example.test>",
    )


@pytest.mark.asyncio
async def test_header_fetch_populates_attachment_info_from_bodystructure() -> None:
    client = _client()
    imap = AsyncMock()
    raw_headers = (
        b"From: sender@example.test\r\nTo: user@example.test\r\n"
        b"Subject: PDF note\r\nDate: Tue, 22 Jul 2026 04:00:00 +0000\r\n\r\n"
    )
    trail = f" BODYSTRUCTURE ({_MIXED_PDF}))".encode()
    imap.uid.return_value = (
        "OK",
        [
            b"1 FETCH (UID 9 FLAGS (\\Seen) BODY[HEADER] {%d}" % len(raw_headers),
            bytearray(raw_headers),
            trail,
        ],
    )

    result = await client._batch_fetch_headers(imap, ["9"], include_flags=True)

    assert result["9"]["attachments"] == ["note.pdf"]
    assert result["9"]["attachment_info"] == [
        {"filename": "note.pdf", "size": 1234 * 3 // 4, "content_type": "application/pdf"}
    ]
    imap.uid.assert_awaited_once_with("fetch", "9", "(FLAGS BODY.PEEK[HEADER]<0.65537> BODYSTRUCTURE)")


def _html_draft_bytes() -> bytes:
    message = EmailMessage()
    message["From"] = "me@example.test"
    message["To"] = "bob@example.test"
    message["Cc"] = "cara@example.test"
    message["Bcc"] = "secret@example.test"
    message["Subject"] = "HTML draft"
    message["In-Reply-To"] = "<mid-1@example.test>"
    message.set_content("Hello plain")
    message.add_alternative("<p>Hello</p>", subtype="html")
    message.add_attachment(b"%PDF-1.4 test", maintype="application", subtype="pdf", filename="note.pdf")
    return message.as_bytes()


@pytest.mark.asyncio
async def test_fetch_draft_source_preserves_html_cc_bcc_and_pdf() -> None:
    raw_email = _html_draft_bytes()
    mock_imap = AsyncMock()
    mock_imap._client_task = asyncio.Future()
    mock_imap._client_task.set_result(None)
    mock_imap.wait_hello_from_server = AsyncMock()
    mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
    mock_imap.select = AsyncMock(return_value=("OK", [b"1"]))
    mock_imap.logout = AsyncMock()
    mock_imap.protocol = None

    async def _fake_fetch(_imap, _email_id):
        return [b"1 FETCH (BODY[] {%d}" % len(raw_email), bytearray(raw_email), b")"]

    client = _client()
    with (
        patch.object(client, "_fetch_email_with_formats", side_effect=_fake_fetch),
        patch.object(client, "_connect_imap", AsyncMock(return_value=mock_imap)),
    ):
        result = await client.fetch_draft_source("1", mailbox="Drafts")

    assert result["to"] == ["bob@example.test"]
    assert result["cc"] == ["cara@example.test"]
    assert result["bcc"] == ["secret@example.test"]
    assert result["subject"] == "HTML draft"
    assert result["html"] is True
    assert "Hello plain" in result["body"]
    assert result["html_body"] is not None and "<p>Hello</p>" in result["html_body"]
    assert result["in_reply_to"] == "<mid-1@example.test>"
    assert len(result["attachments"]) == 1
    attachment = result["attachments"][0]
    assert attachment["filename"] == "note.pdf"
    assert attachment["content_type"] == "application/pdf"
    assert attachment["payload"] == b"%PDF-1.4 test"


def test_inline_literals_in_bodystructure_are_folded_back() -> None:
    from mcp_email_server.emails.classic import _extract_bodystructure, _inline_non_header_literals

    name = "测试附件.pdf".encode()
    data = [
        b'72 FETCH (UID 99 FLAGS (\\Seen) BODYSTRUCTURE (("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 5 1) '
        b'("application" "pdf" ("name" {' + str(len(name)).encode() + b"}",
        bytearray(name),
        b' "x") NIL NIL "base64" 100) "mixed" ("boundary" "b")) BODY[HEADER]<0> {10}',
        bytearray(b"Subject: x"),
        b")",
        b"Fetch completed",
    ]
    merged = _inline_non_header_literals(data)
    assert [type(item).__name__ for item in merged] == ["bytes", "bytearray", "bytes", "bytes"]
    assert b"BODY[HEADER]<0> {10}" in merged[0]
    assert b"UID 99" in merged[0]
    names, info = attachments_from_bodystructure(_extract_bodystructure([merged[0]]))
    assert names == ["测试附件.pdf"]
    assert info[0]["content_type"] == "application/pdf"
