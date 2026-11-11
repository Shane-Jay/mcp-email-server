from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mcp_email_server.connection_pool import ImapConnectionPool, close_connection_pools, endpoint_key, get_imap_pool


class FakeImap:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.logged_in = False
        self.logged_out = False
        self.noop_calls = 0
        self.protocol = SimpleNamespace(transport=SimpleNamespace(close=lambda: None))

    async def noop(self):
        self.noop_calls += 1
        if not self.healthy:
            raise ConnectionError("broken")
        return SimpleNamespace(result="OK")

    async def logout(self):
        self.logged_out = True
        self.protocol = None


@pytest.mark.asyncio
async def test_pool_reuses_healthy_connection_and_reconnects_after_noop_failure():
    created: list[FakeImap] = []

    async def connect() -> FakeImap:
        imap = FakeImap()
        created.append(imap)
        return imap

    async def login(imap: FakeImap) -> None:
        imap.logged_in = True

    pool = ImapConnectionPool(idle_timeout_seconds=30)
    first = await pool.acquire("acct", connect=connect, login=login)
    await pool.release(first, discard=False)
    second = await pool.acquire("acct", connect=connect, login=login)
    assert second is first
    assert first.noop_calls == 1
    assert first.logged_in is True
    first.healthy = False
    await pool.release(second, discard=False)
    third = await pool.acquire("acct", connect=connect, login=login)
    assert third is not first
    assert len(created) == 2
    await pool.aclose()


@pytest.mark.asyncio
async def test_pool_discards_on_protocol_error_and_serializes_lock():
    created: list[FakeImap] = []

    async def connect() -> FakeImap:
        imap = FakeImap()
        created.append(imap)
        return imap

    async def login(imap: FakeImap) -> None:
        imap.logged_in = True

    pool = ImapConnectionPool(idle_timeout_seconds=30)
    held = await pool.acquire("acct", connect=connect, login=login)
    waiter = asyncio.create_task(pool.acquire("acct", connect=connect, login=login))
    await asyncio.sleep(0)
    assert waiter.done() is False
    await pool.release(held, discard=True)
    reused_or_new = await waiter
    assert reused_or_new is not held
    await pool.release(reused_or_new, discard=True)
    await pool.aclose()


@pytest.mark.asyncio
async def test_idle_timeout_forces_reconnect():
    created: list[FakeImap] = []

    async def connect() -> FakeImap:
        imap = FakeImap()
        created.append(imap)
        return imap

    async def login(imap: FakeImap) -> None:
        imap.logged_in = True

    pool = ImapConnectionPool(idle_timeout_seconds=0)
    first = await pool.acquire("acct", connect=connect, login=login)
    await pool.release(first)
    second = await pool.acquire("acct", connect=connect, login=login)
    assert second is not first
    assert first.logged_out is True
    await pool.release(second, discard=True)
    await pool.aclose()


def test_endpoint_key_is_stable_and_omits_secrets():
    assert endpoint_key("Imap.Example.test", 993, "user", use_ssl=True, start_ssl=False) == endpoint_key(
        "imap.example.test", 993, "user", use_ssl=True, start_ssl=False
    )
    assert "password" not in endpoint_key("h", 1, "u", use_ssl=True, start_ssl=False)


def test_get_imap_pool_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_EMAIL_SERVER_IMAP_POOL", raising=False)
    assert get_imap_pool() is None


@pytest.mark.asyncio
async def test_close_connection_pools_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_EMAIL_SERVER_IMAP_POOL", "true")
    pool = get_imap_pool()
    assert pool is not None
    await close_connection_pools()
    await close_connection_pools()
