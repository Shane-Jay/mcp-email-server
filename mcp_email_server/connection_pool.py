"""Optional persistent IMAP (and SMTP) connection reuse.

Disabled by default so upstream per-call connect/login/logout behaviour is
unchanged. When enabled, one connection is kept per account endpoint, guarded
by an asyncio lock so concurrent tool calls cannot interleave IMAP commands.
A NOOP health check and idle timeout drop stale sockets; any protocol error
discards the connection so the next call opens a fresh one.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp_email_server.log import logger

_FALSE_VALUES = {"0", "false", "no", "off"}
ClientT = TypeVar("ClientT")
ConnectImap = Callable[[], Awaitable[Any]]
LoginImap = Callable[[Any], Awaitable[None]]
CloseImap = Callable[[Any], Awaitable[None]]


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES and bool(raw.strip())


def _parse_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def imap_pool_enabled() -> bool:
    return _parse_bool_env("MCP_EMAIL_SERVER_IMAP_POOL", False)


def smtp_pool_enabled() -> bool:
    return _parse_bool_env("MCP_EMAIL_SERVER_SMTP_POOL", False)


def pool_idle_timeout_seconds() -> float:
    return _parse_float_env("MCP_EMAIL_SERVER_POOL_IDLE_SECONDS", 60.0)


def endpoint_key(host: str, port: int, user_name: str, *, use_ssl: bool, start_ssl: bool) -> str:
    """Stable non-secret identity for one IMAP/SMTP endpoint."""

    return f"{host.lower()}|{port}|{user_name}|{int(use_ssl)}|{int(start_ssl)}"


@dataclass
class _ImapSlot:
    imap: Any
    last_used: float
    lock: asyncio.Lock


class ImapConnectionPool:
    """One reusable authenticated IMAP connection per endpoint."""

    def __init__(self, *, idle_timeout_seconds: float | None = None) -> None:
        self.idle_timeout_seconds = (
            pool_idle_timeout_seconds() if idle_timeout_seconds is None else idle_timeout_seconds
        )
        self._slots: dict[str, _ImapSlot] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _health_ok(self, imap: Any) -> bool:
        protocol = getattr(imap, "protocol", None)
        if protocol is None:
            return False
        try:
            response = await imap.noop()
        except Exception:
            return False
        status = getattr(response, "result", None)
        if status is None and isinstance(response, tuple | list) and response:
            status = response[0]
        return str(status).upper() == "OK"

    async def acquire(
        self,
        key: str,
        *,
        connect: ConnectImap,
        login: LoginImap,
    ) -> Any:
        """Return a logged-in IMAP client, creating or replacing the cached one as needed."""

        lock = self._lock_for(key)
        await lock.acquire()
        try:
            slot = self._slots.get(key)
            now = time.monotonic()
            if slot is not None:
                idle = now - slot.last_used
                if idle <= self.idle_timeout_seconds and await self._health_ok(slot.imap):
                    self._mark_owned(slot.imap, key)
                    return slot.imap
                await self._silent_close(slot.imap)
                self._slots.pop(key, None)
            imap = await connect()
            try:
                await login(imap)
            except BaseException:
                await self._silent_close(imap)
                raise
            self._mark_owned(imap, key)
            self._slots[key] = _ImapSlot(imap=imap, last_used=now, lock=lock)
            return imap
        except BaseException:
            if lock.locked():
                lock.release()
            raise

    def _mark_owned(self, imap: Any, key: str) -> None:
        imap._mcp_pool = self
        imap._mcp_pool_key = key
        imap._mcp_authenticated = True
        imap._mcp_from_pool = True

    def _clear_owned(self, imap: Any) -> None:
        imap._mcp_pool = None
        imap._mcp_pool_key = None

    async def release(self, imap: Any, *, discard: bool = False) -> None:
        key = getattr(imap, "_mcp_pool_key", None)
        lock = self._lock_for(key) if isinstance(key, str) else None
        protocol_dead = getattr(imap, "protocol", None) is None
        try:
            if discard or key is None or protocol_dead:
                if isinstance(key, str):
                    self._slots.pop(key, None)
                await self._silent_close(imap)
                imap._mcp_from_pool = False
                return
            slot = self._slots.get(key)
            if slot is None or slot.imap is not imap:
                self._slots[key] = _ImapSlot(imap=imap, last_used=time.monotonic(), lock=lock or asyncio.Lock())
            else:
                slot.last_used = time.monotonic()
        finally:
            if lock is not None and lock.locked():
                lock.release()
            self._clear_owned(imap)

    def discard_sync(self, imap: Any) -> None:
        """Drop a broken connection from the cache; the transport is already dying."""

        key = getattr(imap, "_mcp_pool_key", None)
        if isinstance(key, str):
            self._slots.pop(key, None)
        lock = self._locks.get(key) if isinstance(key, str) else None
        if lock is not None and lock.locked():
            lock.release()
        self._clear_owned(imap)
        imap._mcp_from_pool = False
        imap._mcp_authenticated = False

    async def aclose(self) -> None:
        slots = list(self._slots.values())
        self._slots.clear()
        for slot in slots:
            await self._silent_close(slot.imap)
            if slot.lock.locked():
                slot.lock.release()

    @staticmethod
    async def _silent_close(imap: Any) -> None:
        try:
            if getattr(imap, "protocol", None) is not None:
                await imap.logout()
        except Exception:
            logger.debug("IMAP pool logout failed")
        protocol = getattr(imap, "protocol", None)
        if protocol is not None:
            transport = getattr(protocol, "transport", None)
            if transport is not None:
                with contextlib.suppress(Exception):
                    transport.close()
            imap.protocol = None


@dataclass
class _SmtpSlot:
    smtp: Any
    last_used: float
    lock: asyncio.Lock


class SmtpConnectionPool:
    """Optional one-connection SMTP reuse. Protocol errors discard the socket."""

    def __init__(self, *, idle_timeout_seconds: float | None = None) -> None:
        self.idle_timeout_seconds = (
            pool_idle_timeout_seconds() if idle_timeout_seconds is None else idle_timeout_seconds
        )
        self._slots: dict[str, _SmtpSlot] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def acquire(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        lock = self._lock_for(key)
        await lock.acquire()
        try:
            slot = self._slots.get(key)
            now = time.monotonic()
            if slot is not None:
                if now - slot.last_used <= self.idle_timeout_seconds and getattr(slot.smtp, "is_connected", False):
                    slot.smtp._mcp_pool = self
                    slot.smtp._mcp_pool_key = key
                    return slot.smtp
                await self._silent_close(slot.smtp)
                self._slots.pop(key, None)
            smtp = await factory()
            smtp._mcp_pool = self
            smtp._mcp_pool_key = key
            self._slots[key] = _SmtpSlot(smtp=smtp, last_used=now, lock=lock)
            return smtp
        except BaseException:
            if lock.locked():
                lock.release()
            raise

    async def release(self, smtp: Any, *, discard: bool = False) -> None:
        key = getattr(smtp, "_mcp_pool_key", None)
        lock = self._lock_for(key) if isinstance(key, str) else None
        try:
            if discard or key is None:
                if isinstance(key, str):
                    self._slots.pop(key, None)
                await self._silent_close(smtp)
                return
            slot = self._slots.get(key)
            if slot is None or slot.smtp is not smtp:
                self._slots[key] = _SmtpSlot(smtp=smtp, last_used=time.monotonic(), lock=lock or asyncio.Lock())
            else:
                slot.last_used = time.monotonic()
        finally:
            if lock is not None and lock.locked():
                lock.release()
            smtp._mcp_pool = None

    async def aclose(self) -> None:
        slots = list(self._slots.values())
        self._slots.clear()
        for slot in slots:
            await self._silent_close(slot.smtp)
            if slot.lock.locked():
                slot.lock.release()

    @staticmethod
    async def _silent_close(smtp: Any) -> None:
        try:
            await smtp.quit()
        except Exception:
            with contextlib.suppress(Exception):
                smtp.close()


_imap_pool: ImapConnectionPool | None = None
_smtp_pool: SmtpConnectionPool | None = None


def pooled_imap(imap: Any) -> ImapConnectionPool | None:
    """Return the owning pool only for a real pooled client, never a test mock."""

    pool = getattr(imap, "_mcp_pool", None)
    return pool if isinstance(pool, ImapConnectionPool) else None


def imap_session_is_authenticated(imap: Any) -> bool:
    """True only when the pool marked this client; MagicMock attributes stay false."""

    return getattr(imap, "_mcp_authenticated", False) is True


def imap_was_pooled(imap: Any) -> bool:
    return getattr(imap, "_mcp_from_pool", False) is True


def get_imap_pool() -> ImapConnectionPool | None:
    """Return the process IMAP pool when reuse is enabled."""

    global _imap_pool
    if not imap_pool_enabled():
        return None
    if _imap_pool is None:
        _imap_pool = ImapConnectionPool()
    return _imap_pool


def get_smtp_pool() -> SmtpConnectionPool | None:
    global _smtp_pool
    if not smtp_pool_enabled():
        return None
    if _smtp_pool is None:
        _smtp_pool = SmtpConnectionPool()
    return _smtp_pool


async def close_connection_pools() -> None:
    """Close cached sockets on process shutdown."""

    global _imap_pool, _smtp_pool
    if _imap_pool is not None:
        await _imap_pool.aclose()
        _imap_pool = None
    if _smtp_pool is not None:
        await _smtp_pool.aclose()
        _smtp_pool = None
