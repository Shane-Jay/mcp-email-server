"""Restrict outgoing attachment reads to a configured allowlisted directory."""

from __future__ import annotations

import os
from pathlib import Path

_ENV = "MCP_EMAIL_SERVER_ATTACHMENT_DIR"


class AttachmentPathError(ValueError):
    """A caller-supplied attachment path is outside the allowlisted directory."""


def configured_attachment_dir() -> Path | None:
    """Return the configured attachment allowlist directory, if any."""

    raw = os.environ.get(_ENV)
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser()


def resolve_allowed_attachment_path(file_path: str, allowlist: Path | None = None) -> Path:
    """Resolve ``file_path`` and reject traversal / symlink escapes from the allowlist.

    When no allowlist is configured this still resolves the path (following
    symlinks) so callers get a canonical file, matching upstream path behaviour.
    """

    root = allowlist if allowlist is not None else configured_attachment_dir()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError(f"Attachment file not found: {file_path}") from exc
    if not resolved.is_file():
        raise ValueError(f"Attachment path is not a file: {file_path}")
    if root is None:
        return resolved
    try:
        allowed_root = root.resolve(strict=True)
    except OSError as exc:
        raise AttachmentPathError(
            "Attachment allowlist directory is missing or unreadable; "
            f"set {_ENV} to an existing directory"
        ) from exc
    if not allowed_root.is_dir():
        raise AttachmentPathError(f"Attachment allowlist {_ENV} is not a directory")
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        raise AttachmentPathError(
            "Outgoing attachments must be files inside the configured attachment directory"
        ) from None
    return resolved
