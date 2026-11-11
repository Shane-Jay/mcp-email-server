from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_email_server.encrypted_cache import EncryptedMailCache, cache_key_material


def _cache(tmp_path: Path, key: bytes = b"k" * 32) -> EncryptedMailCache:
    return EncryptedMailCache(tmp_path / "cache.sqlite3", key, max_bytes=4096, max_age_seconds=3600)


def test_round_trip_and_never_stores_credentials(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.put(
        account="work",
        mailbox="INBOX",
        uid="12",
        uidvalidity=7,
        kind="body",
        payload={"subject": "Hello", "body": "secret-mail", "password": "should-not-persist", "sender": "a@b.test"},
    )
    raw = (tmp_path / "cache.sqlite3").read_bytes()
    assert b"secret-mail" not in raw
    assert b"should-not-persist" not in raw
    got = cache.get(account="work", mailbox="INBOX", uid="12", uidvalidity=7, kind="body")
    assert got is not None
    assert got["body"] == "secret-mail"
    assert "password" not in got
    cache.close()


def test_uidvalidity_change_invalidates_mailbox(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.put(
        account="work",
        mailbox="INBOX",
        uid="1",
        uidvalidity=1,
        kind="headers",
        payload={"subject": "old", "sender": "a@b.test", "recipients": ["c@d.test"]},
    )
    cache.ensure_uidvalidity("work", "INBOX", 2)
    assert cache.get(account="work", mailbox="INBOX", uid="1", uidvalidity=1, kind="headers") is None
    assert cache.search_headers(account="work", mailbox="INBOX", uidvalidity=1, subject="old") == []
    cache.close()


def test_wrong_key_cannot_read_records(tmp_path: Path):
    cache = _cache(tmp_path, key=b"a" * 32)
    cache.put(
        account="work",
        mailbox="INBOX",
        uid="9",
        uidvalidity=1,
        kind="body",
        payload={"body": "classified"},
    )
    cache.close()
    other = EncryptedMailCache(tmp_path / "cache.sqlite3", b"b" * 32)
    assert other.get(account="work", mailbox="INBOX", uid="9", uidvalidity=1, kind="body") is None
    other.close()


def test_local_header_search(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.put(
        account="163",
        mailbox="INBOX",
        uid="3",
        uidvalidity=5,
        kind="headers",
        payload={"subject": "Invoice 42", "sender": "billing@shop.test", "recipients": ["me@163.com"]},
    )
    hits = cache.search_headers(account="163", mailbox="INBOX", uidvalidity=5, subject="invoice")
    assert len(hits) == 1
    assert hits[0]["email_id"] == "3"
    assert cache.search_headers(account="163", mailbox="INBOX", uidvalidity=5, from_address="nope") == []
    cache.close()


def test_sqlite_index_does_not_store_plaintext_subjects_or_addresses(tmp_path: Path):
    cache = _cache(tmp_path)
    account = "acct-unique-billing-work"
    mailbox = "Mailbox-Unique-Archive-Folder"
    subject = "SECRET-SUBJECT-Invoice-4242"
    sender = "billing@shop.test"
    recipient = "finance-unique@example.test"
    cache.put(
        account=account,
        mailbox=mailbox,
        uid="3",
        uidvalidity=5,
        kind="headers",
        payload={"subject": subject, "sender": sender, "recipients": [recipient]},
    )
    cache.close()
    db_path = tmp_path / "cache.sqlite3"
    raw = db_path.read_bytes()
    for needle in (account.encode(), mailbox.encode(), subject.encode(), sender.encode(), recipient.encode()):
        assert needle not in raw
    conn = sqlite3.connect(db_path)
    try:
        account_row, mailbox_row = conn.execute("SELECT account, mailbox FROM records").fetchone()
        epoch_account, epoch_mailbox = conn.execute("SELECT account, mailbox FROM mailbox_epochs").fetchone()
    finally:
        conn.close()
    for token in (account_row, mailbox_row, epoch_account, epoch_mailbox):
        assert isinstance(token, str)
        assert len(token) == 64
        assert set(token) <= set("0123456789abcdef")
    cache = EncryptedMailCache(db_path, b"k" * 32, max_bytes=4096, max_age_seconds=3600)
    hits = cache.search_headers(account=account, mailbox=mailbox, uidvalidity=5, subject="invoice")
    assert len(hits) == 1
    assert hits[0]["subject"] == subject
    assert hits[0]["sender"] == sender
    cache.close()


def test_size_limit_evicts_oldest(tmp_path: Path):
    cache = EncryptedMailCache(tmp_path / "cache.sqlite3", b"k" * 32, max_bytes=200, max_age_seconds=3600)
    for uid in ("1", "2", "3"):
        cache.put(
            account="a",
            mailbox="INBOX",
            uid=uid,
            uidvalidity=1,
            kind="body",
            payload={"body": "x" * 80, "subject": uid},
        )
    remaining = cache._conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    assert remaining < 3
    cache.close()


def test_cache_key_material_hex_and_passphrase(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_CACHE_KEY", "ab" * 32)
    assert cache_key_material() == bytes.fromhex("ab" * 32)
    monkeypatch.setenv("MCP_EMAIL_CACHE_KEY", "passphrase")
    key = cache_key_material()
    assert key is not None and len(key) == 32
