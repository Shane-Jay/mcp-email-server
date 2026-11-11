# Changes versus upstream mcp-email-server

Base: [Wh1isper/mcp-email-server](https://github.com/Wh1isper/mcp-email-server) tag **1.14.0**
(`cb1191e4fc6d98a86aa35b4d0f1de72ff230a31d`), BSD-3-Clause.

Each row is a discrete change so generic work can be offered upstream as
separate PRs. "Upstream-able" means the default stays compatible with 1.14.0
behaviour when the new setting is unset.

| Change | Upstream-able? | Notes |
| --- | --- | --- |
| Bearer-token auth on SSE and Streamable HTTP; token from env or secret file; multiple tokens for rotation; constant-time compare; refuse to start HTTP without a token unless `MCP_EMAIL_HTTP_INSECURE=true`; `/healthz` is unauthenticated | **Yes** | Keep existing Host/Origin DNS-rebinding settings. Default-deny HTTP is a security fix for network transports. |
| Per-account `imap_id_mode`: `auto` (default, current behaviour), `always`, `never`. `always` sends the existing 163-compatible compact RFC 2971 ID even when CAPABILITY omits ID | **Yes** | Additive field; `auto` preserves 1.14.0. |
| Persistent IMAP connection pool (NOOP health check, idle timeout, reconnect, per-endpoint lock, discard on protocol error). SMTP pool optional. Off unless `MCP_EMAIL_SERVER_IMAP_POOL=true` | **Yes** | Default remains per-call connect/login/logout. |
| AES-256-GCM encrypted local cache for headers/bodies (cryptography / AESGCM, SQLite index with HMAC-SHA256 account/mailbox tokens, UIDVALIDITY invalidation, size/age limits). Off unless `MCP_EMAIL_CACHE_KEY` is set. Never stores credentials. Subjects/addresses are not stored in plaintext columns | **Yes** | Optional overlay; changing the key requires wiping the cache file. |
| `reply_email` tool (reply / reply-all) that reuses `send_email` after fetching the original | **Yes** | Does not duplicate SMTP/IMAP send paths. |
| `send_draft` tool that sends an existing draft then deletes it on a clean send | **Yes** | Reuses `send_email` and `delete_emails`. |
| Outgoing attachment allowlist directory (`MCP_EMAIL_SERVER_ATTACHMENT_DIR`); resolve symlinks and block traversal. Unset keeps CWD behaviour | **Yes** | Remote `get_attachment_content` unchanged. |
| Tool allow/deny lists so unused tools disappear from `tools/list` **and are rejected at `tools/call`**. `email_command` hidden and uncallable unless an account sets `allow_protocol_commands` | **Partial** | Allow/deny is generic. Hiding `email_command` by default changes the static catalog (update the catalog fixture). Upstream may prefer keep-listed-but-disabled. |
| `MCP_ALLOWED_HOSTS` explicit list (MagicDNS, tailnet IP, LAN `host:port`) unioned with loopback so a `0.0.0.0` bind still serves `/healthz` | **Yes** | Wildcard bind without the env var still defaults to loopback-only Hosts. |
| Loguru redaction of env secrets (tokens, cache key, passwords) and `log_safe_failure` for env-account parse errors | **Yes** | Does not rewrite every upstream `logger.error(f"{e}")` in classic.py. |
| `docs/OPERATIONS.md` bilingual Synology / token / provider-auth runbook | No | Fork documentation. |
| Env-var multi-account config (`MCP_EMAIL_SERVER_ACCOUNTS` + `MCP_EMAIL_SERVER_ACCOUNT_<name>_*`) and `PASSWORD_FILE` | **Yes** | Unprefixed `MCP_EMAIL_SERVER_*` account still works. |
| Provider presets: 163, 126, Aliyun enterprise, iCloud, QQ, Tsinghua Coremail | **Yes** | Data + docs; 163 `always`/`local` are preset defaults only. |
| Non-root, read-only-root-FS friendly Dockerfile; `docker-compose.example.yml`; `env.example` | **Partial** | Upstream image is stdio/root. NAS compose and HTTP default CMD are fork-specific. Non-root USER is worth upstreaming. |
| README English + Chinese quick start for NAS / Tailscale HTTP | No | Fork documentation. |
| Tests for auth, IMAP ID modes, pool, cache, reply headers, attachment paths, fake IMAP/SMTP | **Yes** | Travel with the corresponding PRs. |

## Upstream behaviours that stay the default

- Empty recipient allowlist still denies sending (`send_email`, `forward_email`, `save_to_mailbox`).
- `email_command` still requires per-account `allow_protocol_commands`.
- IMAP ID `auto` still skips the command when CAPABILITY omits ID.
- No connection reuse unless the pool env flag is on.
- No encrypted cache unless a key is configured.
- Host/Origin DNS-rebinding protection is unchanged (explicit `MCP_ALLOWED_HOSTS` still required for non-loopback names on a wildcard bind).
- Local Email App V2 / managed catalog / loopback UI remain available and are not required for env-based NAS operation.

## Fork-only product defaults (compose / env.example)

These are **documentation and example env**, not code defaults:

- HTTP Streamable HTTP as the container `CMD`
- IMAP pool enabled
- Attachment directory set to `/data/attachments`
- Tag tools denied in the example allow/deny list
