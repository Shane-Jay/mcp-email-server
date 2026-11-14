# mcp-email-server (self-hosted)

Self-hosted, multi-account IMAP/SMTP [MCP](https://modelcontextprotocol.io/)
server. Based on [Wh1isper/mcp-email-server](https://github.com/Wh1isper/mcp-email-server)
1.14.0 (BSD-3-Clause, `cb1191e4`). See [LICENSE](LICENSE), [NOTICE](NOTICE),
and [CHANGES-vs-upstream.md](CHANGES-vs-upstream.md).

Run **one container** on a NAS as Streamable HTTP with a bearer token. Default
access mode is LAN (`0.0.0.0`); switch to `local` for loopback-only. Accounts
can be env-only; the local V2 UI is optional. No telemetry. Do not commit real
credentials.

## Features

- Multi-account IMAP/SMTP: list, search, read, reply, forward, drafts, send, move, delete
- Bearer-token HTTP (`MCP_EMAIL_HTTP_BEARER_TOKEN`); refuses to listen without a token
- Host allowlist for LAN IP / hostname (`MCP_ALLOWED_HOSTS`); `MCP_ACCESS_MODE=local|lan`
- Tool allow/deny lists; `email_command` hidden and uncallable unless an account opts in
- Recipient allowlist (empty = deny all send/reply/forward/draft)
- `read_attachment` and base64 `attachments_inline` for clients that only keep text MCP results (both off until their flags are set)
- Encrypted local cache code is present but unused by the live read path; do not set `MCP_EMAIL_CACHE_KEY`
- IMAP ID modes and provider presets: `163`, `126`, `aliyun`, `icloud`, `qq`, `tsinghua`
- Outgoing attachments only from `MCP_EMAIL_SERVER_ATTACHMENT_DIR` when set
- Optional IMAP connection pool; Docker image is non-root and compose is read-only

## Quick start

```bash
cp env.example .env && chmod 600 .env
# Fill: bearer token, MCP_ALLOWED_HOSTS, recipient allowlist, account auth codes.
cp docker-compose.example.yml docker-compose.yml
docker compose build && docker compose up -d
```

MCP endpoint: `http://<NAS-LAN-IP-or-hostname>:9557/mcp`
Header: `Authorization: Bearer <token>`
Liveness: `GET /healthz` (no auth)

Locally:

```bash
uv sync
MCP_EMAIL_HTTP_BEARER_TOKEN=dev-token \
  uv run mcp-email-server streamable-http --host 127.0.0.1 --port 9557
```

Env accounts (163 / Aliyun / iCloud): see [env.example](env.example) and
[docs/OPERATIONS.md](docs/OPERATIONS.md). A reviewed Synology compose kit
(Docker secrets, `MCP_ACCESS_MODE=local|lan`, conservative tool list) is in
`deploy/synology/`. Presets never include passwords — use each provider's
IMAP authorization code, not the web login.

## Security notes

- Do not publish port 9557 or the management UI on the public internet.
- Empty `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS` denies sending, including reply,
  forward, `save_draft`, and `send_draft`.
- `get_attachment_content` needs **both** `MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_CONTENT=true`
  (or `enable_attachment_content = true` in TOML) **and** the tool allowed in
  the catalog. If you set `MCP_EMAIL_SERVER_TOOL_ALLOW`, include
  `get_attachment_content` there.
- Clients that drop embedded resources should use `read_attachment`
  (`MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_READ=true`) and `attachments_inline`
  (`MCP_EMAIL_SERVER_ENABLE_INLINE_ATTACHMENTS=true`). After those are verified,
  `download_attachment` and `get_attachment_content` can leave `TOOL_ALLOW`.
- After `send_draft`, the default `MCP_EMAIL_SERVER_SEND_DRAFT=safe` deletes the
  draft only when this server saved a Sent copy; otherwise the draft is kept.
- Logs redact configured passwords, bearer tokens, and the cache key, and strip
  LOGIN/AUTH args. Full Python tracebacks stay off unless
  `MCP_EMAIL_SERVER_LOG_TRACEBACKS=true` (frame locals can contain secrets).
- The encrypted cache module is not on the live read path. Leave its key and
  volume unset. The module is still in the tree and is not a place to store
  credentials.

## Tests

```bash
make test          # unit + fake IMAP/SMTP (excludes GreenMail)
make test-e2e      # GreenMail (Docker compose, or Java standalone)
```

## 中文快速开始

复制 `env.example` 为 `.env`（`chmod 600`），填写 Bearer 令牌、`MCP_ALLOWED_HOSTS`、
收件人白名单和 163/阿里云/iCloud 授权码。详见
[docs/OPERATIONS.md](docs/OPERATIONS.md) 和 `deploy/synology/`。默认 `lan` 绑定
`0.0.0.0`，局域网是明文 HTTP，优先走 WireGuard 或群晖反代 HTTPS。不要把 9557
或管理 UI 暴露到公网。空收件人白名单默认禁止发信。`get_attachment_content`
同时需要 `MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_CONTENT=true`（或 TOML 里
`enable_attachment_content = true`）以及工具目录放行：若设置了
`MCP_EMAIL_SERVER_TOOL_ALLOW`，必须把 `get_attachment_content` 写进该列表。

## Documentation

- [Operations (EN/中文)](docs/OPERATIONS.md) · [Synology kit](deploy/synology/README.md)
- [Configuration](docs/configuration.md) · [Providers](docs/providers.md)
- [Tools](docs/tools.md) · [Transports](docs/transports.md) · [Security](docs/security.md)

Upstream docs: <https://mcp-email-server.wh1isper.top/>

## License

BSD 3-Clause. Copyright (c) 2025-2026 wh1isper and contributors.
