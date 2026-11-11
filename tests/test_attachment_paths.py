from __future__ import annotations

from pathlib import Path

import pytest

from mcp_email_server.attachment_paths import AttachmentPathError, resolve_allowed_attachment_path


def test_allowlist_accepts_real_file_and_blocks_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "note.txt"
    inside.write_text("hi", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ATTACHMENT_DIR", str(allowed))
    assert resolve_allowed_attachment_path(str(inside)) == inside.resolve()
    with pytest.raises(AttachmentPathError):
        resolve_allowed_attachment_path(str(outside))


def test_symlink_escape_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    link = allowed / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ATTACHMENT_DIR", str(allowed))
    with pytest.raises(AttachmentPathError):
        resolve_allowed_attachment_path(str(link))


def test_relative_path_inside_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "file.bin").write_bytes(b"data")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ATTACHMENT_DIR", str(allowed))
    monkeypatch.chdir(allowed)
    resolved = resolve_allowed_attachment_path("file.bin")
    assert resolved.name == "file.bin"


def test_traversal_tokens_cannot_leave_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("MCP_EMAIL_SERVER_ATTACHMENT_DIR", str(allowed))
    with pytest.raises((AttachmentPathError, FileNotFoundError)):
        resolve_allowed_attachment_path(str(allowed / ".." / "nope.txt"))


def test_existing_parent_file_cannot_escape_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "ok.txt").write_text("ok", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("classified", encoding="utf-8")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ATTACHMENT_DIR", str(allowed))
    with pytest.raises(AttachmentPathError):
        resolve_allowed_attachment_path(str(allowed / ".." / "secret.txt"))
    with pytest.raises(AttachmentPathError):
        resolve_allowed_attachment_path(str(secret))


def test_symlink_directory_escape_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    target = outside_dir / "file.txt"
    target.write_text("x", encoding="utf-8")
    link = allowed / "elsewhere"
    try:
        link.symlink_to(outside_dir)
    except OSError:
        pytest.skip("symlinks are not available")
    monkeypatch.setenv("MCP_EMAIL_SERVER_ATTACHMENT_DIR", str(allowed))
    with pytest.raises(AttachmentPathError):
        resolve_allowed_attachment_path(str(link / "file.txt"))
