"""Decode caller-supplied base64 attachments before any account work.

The decoder checks length before allocating the decoded buffer, requires the
standard base64 alphabet, and refuses a ``data:`` prefix or whitespace. An
optional sha256 must match or the caller sends nothing. Filenames are reduced
to a single NFC basename. MIME types are the caller's type/subtype when that
token is valid, otherwise a small magic-byte sniff.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import re
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_email_server.application.limits import (
    APPLICATION_LIMITS,
    attachment_byte_limit,
    total_attachment_byte_limit,
)
from mcp_email_server.application.mutations import InlineAttachment

_MIME_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}\Z")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}\Z")
_MAX_FILENAME_BYTES = 255
_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_ZIP_MAGIC = b"PK\x03\x04"


def max_base64_characters(raw_bytes: int) -> int:
    """Return the longest standard base64 string that can decode to ``raw_bytes``."""

    return 4 * ((raw_bytes + 2) // 3)


class InlineAttachmentInput(BaseModel):
    """One MCP inline attachment before decoding."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(max_length=APPLICATION_LIMITS.attachment_path_bytes)
    content_base64: str = Field(max_length=max_base64_characters(APPLICATION_LIMITS.attachment_bytes))
    mime_type: str | None = Field(default=None, max_length=256)
    sha256: str | None = Field(default=None, max_length=64)


def inline_attachments_enabled() -> bool:
    """Return whether callers may pass ``attachments_inline``.

    The environment variable is the pre-credential switch and defaults to off.
    When it is unset, a legacy settings field can still enable the feature.
    """

    raw = os.getenv("MCP_EMAIL_SERVER_ENABLE_INLINE_ATTACHMENTS")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    from mcp_email_server.config import get_settings

    try:
        return bool(get_settings().enable_inline_attachments)
    except Exception:
        return False


def decode_inline_attachments(
    raw: Sequence[InlineAttachmentInput | Mapping[str, Any]],
    *,
    path_count: int,
) -> tuple[InlineAttachment, ...]:
    """Validate and decode inline attachments. Raises before any provider I/O."""

    if path_count < 0:
        raise ValueError("attachment paths must be strings")
    if path_count + len(raw) > APPLICATION_LIMITS.attachments:
        raise ValueError(f"attachments must contain at most {APPLICATION_LIMITS.attachments} paths")
    per_file = attachment_byte_limit()
    total_limit = total_attachment_byte_limit()
    max_chars = max_base64_characters(per_file)
    decoded: list[tuple[str, str, bytes]] = []
    total = 0
    for item in raw:
        payload_input = item if isinstance(item, InlineAttachmentInput) else InlineAttachmentInput.model_validate(item)
        encoded = payload_input.content_base64
        if encoded.startswith("data:"):
            raise ValueError("attachments_inline content_base64 must not use a data: prefix")
        if any(character.isspace() for character in encoded):
            raise ValueError("attachments_inline content_base64 must not contain whitespace")
        if len(encoded) > max_chars:
            raise ValueError(f"an attachment exceeds {per_file} bytes")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("attachments_inline content_base64 must be standard base64") from exc
        if len(payload) > per_file:
            raise ValueError(f"an attachment exceeds {per_file} bytes")
        _require_sha256(payload_input.sha256, payload)
        total += len(payload)
        if total > total_limit:
            raise ValueError(f"attachments exceed {total_limit} bytes in total")
        filename = sanitize_attachment_filename(payload_input.filename)
        mime_type = resolve_mime_type(payload_input.mime_type, payload, filename)
        decoded.append((filename, mime_type, payload))
    names = _dedupe_filenames([item[0] for item in decoded])
    return tuple(
        InlineAttachment(filename=name, content_type=mime, payload=payload)
        for name, (_ignored, mime, payload) in zip(names, decoded, strict=True)
    )


def sanitize_attachment_filename(raw: str) -> str:
    """Return one NFC basename without separators, NUL, or other controls."""

    text = unicodedata.normalize("NFC", raw).replace("\\", "/").replace(":", "/")
    base = text.rsplit("/", 1)[-1]
    cleaned = "".join(
        character
        for character in base
        if character != "\x00" and unicodedata.category(character) != "Cc" and ord(character) != 0x7F
    )
    cleaned = cleaned.strip().strip(".")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "attachment"
    encoded = cleaned.encode("utf-8")
    if len(encoded) > _MAX_FILENAME_BYTES:
        cleaned = encoded[:_MAX_FILENAME_BYTES].decode("utf-8", "ignore").strip() or "attachment"
    return cleaned


def resolve_mime_type(declared: str | None, payload: bytes, filename: str) -> str:
    """Use a valid caller type/subtype, otherwise sniff magic bytes and the name."""

    validated = _valid_mime(declared)
    if validated is not None:
        return validated
    sniffed = _sniff_mime(payload)
    if sniffed is not None:
        return sniffed
    guessed, _encoding = mimetypes.guess_type(filename)
    if guessed and _valid_mime(guessed):
        return guessed
    return "application/octet-stream"


def _require_sha256(expected: str | None, payload: bytes) -> None:
    if expected is None:
        return
    digest = expected.strip().lower()
    if _SHA256_HEX.fullmatch(digest) is None:
        raise ValueError("attachment sha256 must be 64 hex characters")
    actual = hashlib.sha256(payload).hexdigest()
    if digest != actual:
        raise ValueError("attachment sha256 does not match the decoded bytes")


def _valid_mime(value: str | None) -> str | None:
    if value is None:
        return None
    if value.count("/") != 1:
        return None
    main, sub = value.split("/", 1)
    if _MIME_TOKEN.fullmatch(main) is None or _MIME_TOKEN.fullmatch(sub) is None:
        return None
    return f"{main.casefold()}/{sub.casefold()}"


def _sniff_mime(payload: bytes) -> str | None:
    if payload.startswith(_PDF_MAGIC):
        return "application/pdf"
    if payload.startswith(_PNG_MAGIC):
        return "image/png"
    if payload.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if payload.startswith(_ZIP_MAGIC):
        return _sniff_ooxml(payload)
    return None


def _sniff_ooxml(payload: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return "application/zip"
    if "word/document.xml" in names:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if any(name.startswith("ppt/slides/slide") for name in names):
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if "xl/workbook.xml" in names:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/zip"


def _dedupe_filenames(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    unique: list[str] = []
    for name in names:
        count = seen.get(name, 0) + 1
        seen[name] = count
        if count == 1:
            unique.append(name)
            continue
        candidate = _suffixed_name(name, count)
        while candidate in seen:
            count += 1
            seen[name] = count
            candidate = _suffixed_name(name, count)
        seen[candidate] = 1
        unique.append(candidate)
    return unique


def _suffixed_name(name: str, count: int) -> str:
    stem, dot, extension = name.rpartition(".")
    candidate = f"{stem}-{count}.{extension}" if dot and stem else f"{name}-{count}"
    encoded = candidate.encode("utf-8")
    if len(encoded) <= _MAX_FILENAME_BYTES:
        return candidate
    return encoded[:_MAX_FILENAME_BYTES].decode("utf-8", "ignore") or "attachment"
