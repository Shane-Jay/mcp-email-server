# mcp-email-server (self-hosted)

A self-hosted, multi-account IMAP/SMTP [MCP](https://modelcontextprotocol.io/)
server for reading, searching, organizing, and sending email. This tree is based
on the open-source project
[Wh1isper/mcp-email-server](https://github.com/Wh1isper/mcp-email-server)
(BSD-3-Clause) tag **1.14.0**, commit
`cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d`. The upstream LICENSE is kept in
[LICENSE](LICENSE). See [NOTICE](NOTICE) and
[CHANGES-vs-upstream.md](CHANGES-vs-upstream.md) for attribution and a
change-by-change list of what can later be offered upstream.

This fork is meant to run as **one container** on a NAS (for example Synology
Docker, linux/amd64), holding several personal mailboxes, and to be reached by
an AI assistant as a remote MCP connector over a private network
(Tailscale/WireGuard) using **Streamable HTTP** plus bearer-token
authentication. The Local Email App V2 management UI still exists but is **not
required**: accounts can be defined entirely with environment variables.

No telemetry is collected. Do not put real credentials, hostnames, or personal
paths in git.

## Features added in this tree

- Bearer-token auth on SSE and Streamable HTTP (env or secret file, rotation,
  constant-time compare). HTTP refuse-to-start without a token unless
  `MCP_EMAIL_HTTP_INSECURE=true`. Host/Origin DNS-rebinding protection is
  unchanged.
- Per-account IMAP ID: `auto` (upstream default), `always`, `never`. `always`
  sends the compact RFC 2971 ID 163/Coremail expect even when CAPABILITY omits
  ID.
- Optional persistent IMAP connections (NOOP health check, idle timeout,
  reconnect, per-endpoint lock). SMTP reuse is optional and off by default.
- Optional AES-256-GCM encrypted local cache for headers and bodies
  (`cryptography` / AESGCM, SQLite index, UIDVALIDITY invalidation). Off unless
  a cache key is configured. Credentials are never stored.
- `reply_email` (reply / reply-all) and `send_draft`, reusing `send_email`.
  Upstream `forward_email` and `save_draft` are unchanged.
- Outgoing attachments may only be read from `MCP_EMAIL_SERVER_ATTACHMENT_DIR`
  when that directory is set (symlink-resolved, traversal blocked).
- Tool allow/deny lists. `email_command` is hidden unless an account sets
  `allow_protocol_commands`. Empty recipient allowlist still denies sending.
- Provider presets: `163`, `126`, `aliyun`, `icloud`, `qq`, `tsinghua`.

## Quick start (Docker on a NAS)

```bash
cp env.example .env
chmod 600 .env
# Edit .env: bearer token, cache key, recipient allowlist, account placeholders.
cp docker-compose.example.yml docker-compose.yml
docker compose build
docker compose up -d
```

The container listens on port **9557** (`/mcp` Streamable HTTP, `/healthz`
unauthenticated liveness). Point the MCP client at
`http://<tailscale-name>:9557/mcp` with header
`Authorization: Bearer <token>`.

Do not publish the local management UI. Do not expose this port on the public
internet.

## Environment accounts (no admin UI)

```bash
MCP_EMAIL_SERVER_ACCOUNTS=netease,aliyun,icloud

MCP_EMAIL_SERVER_ACCOUNT_netease_EMAIL_ADDRESS=you@163.com
MCP_EMAIL_SERVER_ACCOUNT_netease_PASSWORD=sixteen-char-auth
MCP_EMAIL_SERVER_ACCOUNT_netease_PRESET=163

MCP_EMAIL_SERVER_ACCOUNT_aliyun_EMAIL_ADDRESS=you@company.example
MCP_EMAIL_SERVER_ACCOUNT_aliyun_PASSWORD=replace-me
MCP_EMAIL_SERVER_ACCOUNT_aliyun_PRESET=aliyun

MCP_EMAIL_SERVER_ACCOUNT_icloud_EMAIL_ADDRESS=you@icloud.com
MCP_EMAIL_SERVER_ACCOUNT_icloud_PASSWORD=app-specific-password
MCP_EMAIL_SERVER_ACCOUNT_icloud_PRESET=icloud
```

Passwords may also come from `*_PASSWORD_FILE`. The unprefixed
`MCP_EMAIL_SERVER_EMAIL_ADDRESS` / `PASSWORD` / `IMAP_HOST` account from
upstream still works for a single mailbox. See [env.example](env.example) and
[docs/providers.md](docs/providers.md).

Empty `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS` denies all sending. Set explicit
addresses or `*` if you intend to allow everyone.

## Run locally without Docker

Python 3.11+ and [uv](https://docs.astral.sh/uv/) 0.11.29:

```bash
uv sync
uv run pytest tests -q
MCP_EMAIL_HTTP_BEARER_TOKEN=dev-token \
  uv run mcp-email-server streamable-http --host 127.0.0.1 --port 9557
```

stdio (local MCP client):

```bash
uv run mcp-email-server stdio
```

## Tests

```bash
make test          # unit + fake IMAP/SMTP integration (excludes GreenMail)
make test-e2e      # optional GreenMail container suite
```

## 中文快速开始

在群晖 Docker 上复制 `env.example` 为 `.env`（权限 `600`），填入 HTTP Bearer
令牌、缓存密钥、收件人白名单，以及 163 / 阿里云企业邮 / iCloud 等账号（163
使用 16 位授权码，不是网页密码）。`docker compose -f docker-compose.example.yml
up -d` 后，只在 Tailscale/WireGuard 内网把 MCP 客户端指到
`http://<主机>:9557/mcp`，请求头 `Authorization: Bearer <令牌>`。不要把管理 UI
或 9557 端口暴露到公网。空的收件人白名单默认禁止发信。

## Documentation

- [Getting Started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [Provider presets](docs/providers.md)
- [MCP Tools](docs/tools.md)
- [Transports](docs/transports.md)
- [Security](docs/security.md)

Upstream project documentation:
[https://mcp-email-server.wh1isper.top/](https://mcp-email-server.wh1isper.top/).

## License

BSD 3-Clause. Copyright (c) 2025-2026 wh1isper and contributors. See
[LICENSE](LICENSE) and [NOTICE](NOTICE).
