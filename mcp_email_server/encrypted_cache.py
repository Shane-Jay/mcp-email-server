"""Optional AES-GCM encrypted on-disk cache for message metadata and bodies.

The cache is off unless a key is supplied through ``MCP_EMAIL_CACHE_KEY`` or
``MCP_EMAIL_CACHE_KEY_FILE``. Credentials are never written. Records are bound
to IMAP UIDVALIDITY so a mailbox reset cannot serve stale UIDs. The SQLite
index stores HMAC-SHA256 tokens for account and mailbox names plus IMAP UIDs;
subjects and addresses live only inside AES-GCM ciphertext.
"""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mcp_email_server.log import logger
from mcp_email_server.secrets_io import read_secret_from_env

_KEY_ENV = "MCP_EMAIL_CACHE_KEY"
_KEY_FILE_ENV = "MCP_EMAIL_CACHE_KEY_FILE"
_DIR_ENV = "MCP_EMAIL_CACHE_DIR"
_MAX_BYTES_ENV = "MCP_EMAIL_CACHE_MAX_BYTES"
_MAX_AGE_ENV = "MCP_EMAIL_CACHE_MAX_AGE_SECONDS"
_DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_AGE = 7 * 24 * 3600
_FORBIDDEN_FIELDS = frozenset({
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
    "authorization",
})


class CacheEncryptionError(RuntimeError):
    """The cache key is missing, invalid, or a record failed to decrypt."""


def _parse_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def cache_key_material() -> bytes | None:
    """Return a 32-byte AES key, or None when the cache is disabled."""

    secret = read_secret_from_env(_KEY_ENV, file_env_name=_KEY_FILE_ENV)
    if secret is None:
        return None
    # Accept 64-char hex, 32 raw/utf-8 bytes, or any passphrase (SHA-256).
    stripped = secret.strip()
    if len(stripped) == 64:
        try:
            key = bytes.fromhex(stripped)
        except ValueError:
            key = None
        else:
            if len(key) == 32:
                return key
    raw = secret.encode("utf-8")
    if len(raw) == 32:
        return raw
    return sha256(raw).digest()


def cache_directory() -> Path:
    raw = os.environ.get(_DIR_ENV)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path(os.environ.get("HOME", tempfile.gettempdir())) / ".cache" / "mcp-email-server"


def _sanitize_record(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.casefold() in _FORBIDDEN_FIELDS:
            continue
        clean[key] = value
    return clean


class EncryptedMailCache:
    """SQLite-backed AES-256-GCM cache. One ciphertext blob per record."""

    def __init__(
        self,
        path: Path,
        key: bytes,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        max_age_seconds: int = _DEFAULT_MAX_AGE,
    ) -> None:
        if len(key) != 32:
            raise CacheEncryptionError("cache key must be 32 bytes")
        self.path = path
        self._key = key
        self._aesgcm = AESGCM(key)
        self.max_bytes = max_bytes
        self.max_age_seconds = max_age_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                account TEXT NOT NULL,
                mailbox TEXT NOT NULL,
                uid TEXT NOT NULL,
                uidvalidity INTEGER NOT NULL,
                kind TEXT NOT NULL,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL,
                created_at REAL NOT NULL,
                last_access REAL NOT NULL,
                size INTEGER NOT NULL,
                PRIMARY KEY (account, mailbox, uid, uidvalidity, kind)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mailbox_epochs (
                account TEXT NOT NULL,
                mailbox TEXT NOT NULL,
                uidvalidity INTEGER NOT NULL,
                PRIMARY KEY (account, mailbox)
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _encrypt(self, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return nonce, self._aesgcm.encrypt(nonce, plaintext, aad)

    def _decrypt(self, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        return self._aesgcm.decrypt(nonce, ciphertext, aad)

    def _aad(self, account: str, mailbox: str, uid: str, uidvalidity: int, kind: str) -> bytes:
        return f"{account}\0{mailbox}\0{uid}\0{uidvalidity}\0{kind}".encode()

    def _index_token(self, kind: str, value: str) -> str:
        """HMAC-SHA256 index key so the SQLite columns never store account/mailbox names."""

        return hmac.new(self._key, f"{kind}:{value}".encode(), sha256).hexdigest()

    def _account_token(self, account: str) -> str:
        return self._index_token("account", account)

    def _mailbox_token(self, mailbox: str) -> str:
        return self._index_token("mailbox", mailbox)

    def ensure_uidvalidity(self, account: str, mailbox: str, uidvalidity: int) -> None:
        """Drop cached rows when the provider reports a new UIDVALIDITY."""

        account_token = self._account_token(account)
        mailbox_token = self._mailbox_token(mailbox)
        row = self._conn.execute(
            "SELECT uidvalidity FROM mailbox_epochs WHERE account = ? AND mailbox = ?",
            (account_token, mailbox_token),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO mailbox_epochs(account, mailbox, uidvalidity) VALUES (?, ?, ?)",
                (account_token, mailbox_token, uidvalidity),
            )
            self._conn.commit()
            return
        if int(row[0]) == uidvalidity:
            return
        self._conn.execute(
            "DELETE FROM records WHERE account = ? AND mailbox = ?",
            (account_token, mailbox_token),
        )
        self._conn.execute(
            "UPDATE mailbox_epochs SET uidvalidity = ? WHERE account = ? AND mailbox = ?",
            (uidvalidity, account_token, mailbox_token),
        )
        self._conn.commit()

    def put(
        self,
        *,
        account: str,
        mailbox: str,
        uid: str,
        uidvalidity: int,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        if kind not in {"headers", "body"}:
            raise ValueError("cache kind must be 'headers' or 'body'")
        self.ensure_uidvalidity(account, mailbox, uidvalidity)
        document = _sanitize_record(payload)
        blob = json.dumps(document, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        aad = self._aad(account, mailbox, uid, uidvalidity, kind)
        nonce, ciphertext = self._encrypt(blob, aad)
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO records(account, mailbox, uid, uidvalidity, kind, nonce, ciphertext, created_at, last_access, size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account, mailbox, uid, uidvalidity, kind) DO UPDATE SET
                nonce = excluded.nonce,
                ciphertext = excluded.ciphertext,
                created_at = excluded.created_at,
                last_access = excluded.last_access,
                size = excluded.size
            """,
            (
                self._account_token(account),
                self._mailbox_token(mailbox),
                uid,
                uidvalidity,
                kind,
                nonce,
                ciphertext,
                now,
                now,
                len(nonce) + len(ciphertext),
            ),
        )
        self._conn.commit()
        self.enforce_limits()

    def get(
        self,
        *,
        account: str,
        mailbox: str,
        uid: str,
        uidvalidity: int,
        kind: str,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT nonce, ciphertext FROM records
            WHERE account = ? AND mailbox = ? AND uid = ? AND uidvalidity = ? AND kind = ?
            """,
            (self._account_token(account), self._mailbox_token(mailbox), uid, uidvalidity, kind),
        ).fetchone()
        if row is None:
            return None
        nonce, ciphertext = row
        aad = self._aad(account, mailbox, uid, uidvalidity, kind)
        try:
            plaintext = self._decrypt(nonce, ciphertext, aad)
        except Exception:
            self._conn.execute(
                "DELETE FROM records WHERE account = ? AND mailbox = ? AND uid = ? AND uidvalidity = ? AND kind = ?",
                (self._account_token(account), self._mailbox_token(mailbox), uid, uidvalidity, kind),
            )
            self._conn.commit()
            return None
        self._conn.execute(
            """
            UPDATE records SET last_access = ?
            WHERE account = ? AND mailbox = ? AND uid = ? AND uidvalidity = ? AND kind = ?
            """,
            (
                time.time(),
                self._account_token(account),
                self._mailbox_token(mailbox),
                uid,
                uidvalidity,
                kind,
            ),
        )
        self._conn.commit()
        document = json.loads(plaintext.decode("utf-8"))
        if not isinstance(document, dict):
            return None
        return _sanitize_record(document)

    def search_headers(
        self,
        *,
        account: str,
        mailbox: str,
        uidvalidity: int,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return cached header records that match case-insensitive substrings."""

        rows = self._conn.execute(
            """
            SELECT uid, nonce, ciphertext FROM records
            WHERE account = ? AND mailbox = ? AND uidvalidity = ? AND kind = 'headers'
            """,
            (self._account_token(account), self._mailbox_token(mailbox), uidvalidity),
        ).fetchall()
        matches: list[dict[str, Any]] = []
        subject_needle = subject.casefold() if subject else None
        from_needle = from_address.casefold() if from_address else None
        to_needle = to_address.casefold() if to_address else None
        for uid, nonce, ciphertext in rows:
            aad = self._aad(account, mailbox, uid, uidvalidity, "headers")
            try:
                document = json.loads(self._decrypt(nonce, ciphertext, aad).decode("utf-8"))
            except Exception:
                logger.debug("Skipping undecryptable cache header row")
                continue
            if not isinstance(document, dict):
                continue
            hay_subject = str(document.get("subject", "")).casefold()
            hay_from = str(document.get("sender", document.get("from", ""))).casefold()
            recipients = document.get("recipients") or document.get("to") or []
            hay_to = (
                " ".join(str(item) for item in recipients).casefold()
                if isinstance(recipients, list)
                else str(recipients).casefold()
            )
            if subject_needle and subject_needle not in hay_subject:
                continue
            if from_needle and from_needle not in hay_from:
                continue
            if to_needle and to_needle not in hay_to:
                continue
            document["email_id"] = uid
            matches.append(document)
        return matches

    def enforce_limits(self) -> None:
        now = time.time()
        if self.max_age_seconds > 0:
            cutoff = now - self.max_age_seconds
            self._conn.execute("DELETE FROM records WHERE created_at < ?", (cutoff,))
        if self.max_bytes > 0:
            total = self._conn.execute("SELECT COALESCE(SUM(size), 0) FROM records").fetchone()[0]
            if int(total) > self.max_bytes:
                rows = self._conn.execute(
                    "SELECT account, mailbox, uid, uidvalidity, kind, size FROM records ORDER BY last_access ASC"
                ).fetchall()
                remaining = int(total)
                for account, mailbox, uid, uidvalidity, kind, size in rows:
                    if remaining <= self.max_bytes:
                        break
                    self._conn.execute(
                        "DELETE FROM records WHERE account = ? AND mailbox = ? AND uid = ? AND uidvalidity = ? AND kind = ?",
                        (account, mailbox, uid, uidvalidity, kind),
                    )
                    remaining -= int(size)
        self._conn.commit()


_cache: EncryptedMailCache | None = None
_cache_disabled = False


def get_encrypted_cache() -> EncryptedMailCache | None:
    """Return the process cache when a key is configured, else None."""

    global _cache, _cache_disabled
    if _cache_disabled:
        return None
    if _cache is not None:
        return _cache
    key = cache_key_material()
    if key is None:
        return None
    _cache = EncryptedMailCache(
        cache_directory() / "mail-cache.sqlite3",
        key,
        max_bytes=_parse_int_env(_MAX_BYTES_ENV, _DEFAULT_MAX_BYTES),
        max_age_seconds=_parse_int_env(_MAX_AGE_ENV, _DEFAULT_MAX_AGE),
    )
    return _cache


def close_encrypted_cache() -> None:
    global _cache
    if _cache is not None:
        _cache.close()
        _cache = None


@dataclass(frozen=True)
class CachedMessage:
    headers: dict[str, Any]
    body: str | None = None
