"""Pure helpers for reply subject, quoting, threading headers, and recipients."""

from __future__ import annotations

from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime

from mcp_email_server.application.mutations import normalize_thread_message_ids


def reply_subject(original_subject: str) -> str:
    """Return an RFC-style ``Re:`` subject without stacking a second prefix."""

    stripped = original_subject.strip()
    if stripped.casefold().startswith("re:"):
        return stripped
    if stripped.casefold().startswith("aw:"):
        return stripped
    return f"Re: {stripped}" if stripped else "Re:"


def quote_plain_original(*, sender: str, date: str, body: str) -> str:
    """Quote the original plain-text body for a reply."""

    header = f"On {date}, {sender} wrote:" if date else f"{sender} wrote:"
    if not body:
        return header
    quoted_lines = [f"> {line}" if line else ">" for line in body.splitlines()]
    return header + "\n" + "\n".join(quoted_lines)


def build_reply_body(*, note: str, sender: str, date: str, original_body: str) -> str:
    """Place the caller's note above a quoted original."""

    quoted = quote_plain_original(sender=sender, date=date, body=original_body)
    if not note:
        return quoted
    return f"{note.rstrip()}\n\n{quoted}"


def build_thread_headers(
    *,
    message_id: str | None,
    existing_references: str | None = None,
    existing_in_reply_to: str | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(in_reply_to, references)`` for a reply to ``message_id``.

    ``References`` is the original chain plus the message being replied to.
    A missing Message-Id yields no threading headers rather than fabricating one.
    """

    del existing_in_reply_to
    if not message_id or not message_id.strip():
        return None, None
    in_reply_to = normalize_thread_message_ids(message_id.strip())
    ancestors = (existing_references or "").split()
    chain = [normalize_thread_message_ids(item) for item in ancestors if item]
    if in_reply_to not in chain:
        chain.append(in_reply_to)
    references = " ".join(chain) if chain else in_reply_to
    return in_reply_to, references


def _address_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [address.strip() for _name, address in getaddresses([raw]) if address.strip()]


def _normalize_addr(value: str) -> str:
    return value.strip().casefold()


def reply_recipients(
    *,
    mode: str,
    sender: str,
    to_header: str | None,
    cc_header: str | None,
    self_address: str,
) -> tuple[list[str], list[str]]:
    """Return ``(to, cc)`` for reply or reply-all, excluding the sending mailbox."""

    sender_addresses = _address_list(sender)
    if not sender_addresses:
        raise ValueError("original message has no usable From address to reply to")
    to = [sender_addresses[0]]
    if mode != "reply-all":
        return to, []
    seen = {_normalize_addr(self_address), _normalize_addr(to[0])}
    cc: list[str] = []
    for address in [*_address_list(to_header), *_address_list(cc_header)]:
        key = _normalize_addr(address)
        if key in seen:
            continue
        seen.add(key)
        cc.append(address)
    return to, cc


def format_original_date(value: object) -> str:
    """Format a datetime or RFC 5322 date string for the quoting preamble."""

    if value is None:
        return ""
    if isinstance(value, datetime):
        try:
            return value.strftime("%a, %d %b %Y %H:%M %z").strip()
        except (ValueError, TypeError, OverflowError):
            return str(value)
    text = str(value).strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return text
    if parsed is None:
        return text
    try:
        return parsed.strftime("%a, %d %b %Y %H:%M %z").strip()
    except (ValueError, TypeError, OverflowError):
        return text
