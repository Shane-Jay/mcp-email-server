"""Integration test against an in-process IMAP/SMTP double (no Docker)."""

from __future__ import annotations

import asyncio
import contextlib
import email
from email.mime.text import MIMEText

import aioimaplib
import aiosmtplib
import pytest
from pydantic import SecretStr

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import EmailClient, _imap_login, _send_imap_id
from mcp_email_server.reply_compose import build_thread_headers, reply_subject


class LineServer:
    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self._server: asyncio.AbstractServer | None = None
        self.host = "127.0.0.1"
        self.port = 0

    async def start(self, handler) -> None:
        self._server = await asyncio.start_server(handler, self.host, 0)
        sockets = self._server.sockets
        assert sockets
        self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


async def _imap_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:  # noqa: C901
    id_seen = False
    selected = False
    writer.write(b"* OK IMAP4rev1 fake ready\r\n")
    await writer.drain()
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            parts = text.split(" ", 2)
            tag = parts[0] if parts else "*"
            command = parts[1].upper() if len(parts) > 1 else ""
            if command == "CAPABILITY":
                writer.write(b"* CAPABILITY IMAP4rev1\r\n")
                writer.write(f"{tag} OK CAPABILITY completed\r\n".encode())
            elif command == "LOGIN":
                writer.write(f"{tag} OK LOGIN completed\r\n".encode())
            elif command == "ID":
                id_seen = True
                writer.write(f"{tag} OK ID completed\r\n".encode())
            elif command == "SELECT":
                selected = True
                writer.write(b"* 1 EXISTS\r\n")
                writer.write(b"* OK [UIDVALIDITY 42] UIDs valid\r\n")
                writer.write(b"* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)\r\n")
                writer.write(f"{tag} OK [READ-WRITE] SELECT completed\r\n".encode())
            elif command == "NOOP":
                writer.write(f"{tag} OK NOOP completed\r\n".encode())
            elif command == "LOGOUT":
                writer.write(b"* BYE\r\n")
                writer.write(f"{tag} OK LOGOUT completed\r\n".encode())
                await writer.drain()
                break
            elif command == "UID" and "SEARCH" in text.upper():
                writer.write(b"* SEARCH 1\r\n")
                writer.write(f"{tag} OK SEARCH completed\r\n".encode())
            elif command == "UID" and "FETCH" in text.upper():
                raw = (
                    b"From: Ada <ada@example.test>\r\n"
                    b"To: Me <me@example.test>\r\n"
                    b"Subject: Hello\r\n"
                    b"Message-ID: <mid-1@example.test>\r\n"
                    b"Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
                    b"\r\n"
                    b"Original body\r\n"
                )
                writer.write(f"* 1 FETCH (UID 1 RFC822 {{{len(raw)}}}\r\n".encode())
                writer.write(raw)
                writer.write(b")\r\n")
                writer.write(f"{tag} OK FETCH completed\r\n".encode())
            else:
                writer.write(f"{tag} OK {command or 'OK'} completed\r\n".encode())
            await writer.drain()
            _ = (id_seen, selected)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _smtp_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"220 localhost ESMTP fake\r\n")
    await writer.drain()
    data_mode = False
    buf = b""
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if data_mode:
                buf += line
                if buf.endswith(b"\r\n.\r\n") or line.strip() == b".":
                    data_mode = False
                    writer.write(b"250 OK queued\r\n")
                    await writer.drain()
                continue
            command = line.decode("ascii", "replace").strip().upper()
            if command.startswith("EHLO") or command.startswith("HELO"):
                writer.write(b"250-localhost\r\n250 AUTH LOGIN PLAIN\r\n")
            elif command.startswith("AUTH"):
                writer.write(b"235 AUTH OK\r\n")
            elif command.startswith("MAIL") or command.startswith("RCPT"):
                writer.write(b"250 OK\r\n")
            elif command.startswith("DATA"):
                data_mode = True
                buf = b""
                writer.write(b"354 go ahead\r\n")
            elif command.startswith("QUIT"):
                writer.write(b"221 bye\r\n")
                await writer.drain()
                break
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_imap_id_always_and_pool_noop_against_fake_server():
    imap_server = LineServer()
    await imap_server.start(_imap_handler)
    try:
        client = aioimaplib.IMAP4(host=imap_server.host, port=imap_server.port, timeout=5)
        await client.wait_hello_from_server()
        await _imap_login(client, "user", "pass")
        await _send_imap_id(client, mode="always")
        select = await client.select("INBOX")
        assert str(select.result).upper() == "OK"
        noop = await client.noop()
        assert str(noop.result).upper() == "OK"
        await client.logout()
    finally:
        await imap_server.stop()


@pytest.mark.asyncio
async def test_email_client_send_and_reply_headers_against_fake_smtp():
    smtp_server = LineServer()
    await smtp_server.start(_smtp_handler)
    try:
        server = EmailServer(
            user_name="me@example.test",
            password=SecretStr("pass"),
            host=smtp_server.host,
            port=smtp_server.port,
            use_ssl=False,
            start_ssl=False,
            verify_ssl=True,
        )
        client = EmailClient(server, sender_name="Me", sender_address="me@example.test")
        in_reply_to, references = build_thread_headers(message_id="mid-1@example.test")
        msg = MIMEText("Thanks\n\n> Original body")
        msg["Subject"] = reply_subject("Hello")
        msg["From"] = "Me <me@example.test>"
        msg["To"] = "ada@example.test"
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references
        smtp = aiosmtplib.SMTP(hostname=smtp_server.host, port=smtp_server.port, start_tls=False, use_tls=False)
        await smtp.connect()
        await smtp.ehlo()
        await smtp.send_message(msg)
        await smtp.quit()
        parsed = email.message_from_string(msg.as_string())
        assert parsed["Subject"] == "Re: Hello"
        assert parsed["In-Reply-To"] == "<mid-1@example.test>"
        assert client.sender_address == "me@example.test"
    finally:
        await smtp_server.stop()
