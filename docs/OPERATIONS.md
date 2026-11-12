# Operations / 运维手册

Self-hosted IMAP/SMTP MCP server on a NAS (Synology Docker / Container Manager)
or any Docker host. English first, Chinese immediately after each section.

在群晖 Docker / Container Manager（或任意 Docker 主机）上运行本服务。每节先英文后中文。

Do not put real credentials, tokens, or personal paths in git. Copy
`env.example` to `.env` (`chmod 600`) and edit it on the NAS only.

不要把真实口令、令牌或个人路径写入 git。把 `env.example` 复制为 `.env`（权限
`600`），只在 NAS 上编辑。

## 1. Deploy on Synology Docker / Container Manager

A reviewed NAS kit with Docker secrets, `MCP_ACCESS_MODE=local|lan`, and
`scripts/smoke.sh` lives in `deploy/synology/` (see that directory's README).
Default is `lan` (bind `0.0.0.0`) so the home LAN and WireGuard-routed
subnets can reach port 9557. The steps below remain valid for a from-source
compose using `docker-compose.example.yml`.

群晖密钥版 compose 套件在仓库的 `deploy/synology/`（该目录 README 有逐步说明）。
默认 `lan` 绑定 `0.0.0.0`，家里局域网和经 WireGuard 路由的网段都能访问。
下面步骤仍适用于用根目录 `docker-compose.example.yml` 从源码构建。

1. Copy this repository onto the NAS (or clone it into a shared folder).
2. `cp env.example .env && chmod 600 .env`
3. Fill in HTTP bearer token, cache key, `MCP_ALLOWED_HOSTS`, recipient
   allowlist, and at least one account (see below).
4. `cp docker-compose.example.yml docker-compose.yml`
5. In Container Manager: **Project** → create from the folder that contains
   `docker-compose.yml`, or from SSH: `docker compose up -d --build`.
6. Confirm the container is user `1000:1000`, `read_only: true`, and healthy
   (`/healthz`). Named volumes for `/data` must be writable by uid 1000
   (`chown 1000:1000` on the data volume, or Synology shared-folder
   permissions). For the Synology kit in `deploy/synology/`, secret files are
   owned by your DSM user, group `users` (gid 100), mode `640`, with
   `group_add: ["100"]` on the container — do not chown secrets to `1000:1000`.
   An unreadable password file skips that account with `incomplete configuration`.
7. Point the MCP client at `http://<NAS-LAN-IP-or-hostname>:9557/mcp` with
   `Authorization: Bearer <token>`. LAN traffic is plain HTTP; prefer
   WireGuard or Synology Reverse Proxy (HTTPS).

Do not publish port 9557 on the public internet. Do not run `mcp-email-server ui`
in this container.

1. 把本仓库放到群晖共享目录。
2. `cp env.example .env && chmod 600 .env`
3. 填写 HTTP Bearer 令牌、缓存密钥、`MCP_ALLOWED_HOSTS`、收件人白名单和至少一个账号。
4. `cp docker-compose.example.yml docker-compose.yml`
5. 打开套件中心 **Container Manager** → **项目**，用该目录创建；或 SSH 执行
   `docker compose up -d --build`。
6. 确认容器以 `1000:1000` 运行、根文件系统只读、`/healthz` 健康。`/data` 数据卷必须对 uid 1000 可写。
   `deploy/synology/` 套件里的密钥文件归 DSM 用户所有，组为 `users`（gid 100），权限 `640`，
   容器带 `group_add: ["100"]`；不要把密钥 chown 成 `1000:1000`。读不到的密码文件会让该账户
   被跳过，日志为 `incomplete configuration`。
7. 把 MCP 客户端指到 `http://<NAS 局域网 IP 或主机名>:9557/mcp`，请求头
   `Authorization: Bearer <令牌>`。局域网是明文 HTTP，优先走 WireGuard 或群晖反代 HTTPS。

不要把 9557 端口映射到公网。不要在此容器里启动管理 UI。

### Host headers (`MCP_ALLOWED_HOSTS`)

Default `lan` mode publishes `0.0.0.0:9557` on the host. DNS-rebinding
protection still requires an explicit Host allowlist. Include every name/IP
the MCP client will send (NAS LAN IP:port and hostname):

```bash
MCP_ALLOWED_HOSTS=192.168.1.50:9557,nas.local:9557
```

Loopback (`127.0.0.1`, `localhost`, `[::1]`) is always unioned in so the
container healthcheck can call `http://127.0.0.1:9557/healthz`. A Host of `*`
disables the protection — do not use that on a NAS.

compose 默认 `lan` 在宿主机上发布 `0.0.0.0:9557`。DNS 重绑定防护仍需要明确的 Host
白名单。把客户端会发送的局域网 IP:端口和主机名写进逗号分隔列表。回环地址会自动并入，
这样健康检查仍可用 `127.0.0.1`。不要在 NAS 上把 Host 设为 `*`。 `local` 模式把
`MCP_BIND_IP` 设为 `127.0.0.1`。

## 2. Generating tokens and the cache key

HTTP will not listen unless a bearer token is set (or
`MCP_EMAIL_HTTP_INSECURE=true`, which is for isolated development only).

```bash
# HTTP bearer token (32+ random bytes, hex)
python3 -c 'import secrets; print(secrets.token_hex(32))'

# Encrypted-cache passphrase (any long random string; SHA-256 if not 64 hex chars)
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Put the token in `MCP_EMAIL_HTTP_BEARER_TOKEN` (or a file referenced by
`MCP_EMAIL_HTTP_BEARER_TOKEN_FILE` with mode `0600`). Put the cache secret in
`MCP_EMAIL_CACHE_KEY` or `MCP_EMAIL_CACHE_KEY_FILE`. Leave the cache key unset
to disable the cache.

未配置令牌时 HTTP 传输会拒绝启动（除非显式 `MCP_EMAIL_HTTP_INSECURE=true`，仅用于隔离开发）。
用上面的命令生成长随机值。缓存密钥留空即关闭加密缓存。

## 3. Rotating tokens and the cache key

**HTTP token.** Set `MCP_EMAIL_HTTP_BEARER_TOKENS=new-token,old-token`, restart,
switch clients to `new-token`, then drop `old-token` and restart again. Both
values are compared in constant time; either one authenticates during overlap.

**Cache key.** Changing `MCP_EMAIL_CACHE_KEY` makes existing ciphertext
undecryptable. Stop the container, wipe the cache volume (see below), set the
new key, start again. Old rows are not migrated.

**HTTP 令牌：** 把新旧令牌同时写入 `MCP_EMAIL_HTTP_BEARER_TOKENS`，重启，客户端切到新令牌后再删掉旧令牌。
**缓存密钥：** 更换后旧密文无法解密。停容器、清空缓存卷、写入新密钥再启动。不会自动迁移。

## 4. Adding an account

Env-only (no V2 UI). Add a name to `MCP_EMAIL_SERVER_ACCOUNTS` and a matching
block. Restart the container.

```bash
MCP_EMAIL_SERVER_ACCOUNTS=netease,aliyun,icloud

MCP_EMAIL_SERVER_ACCOUNT_netease_EMAIL_ADDRESS=you@163.com
MCP_EMAIL_SERVER_ACCOUNT_netease_PASSWORD=sixteen-char-auth
MCP_EMAIL_SERVER_ACCOUNT_netease_PRESET=163
```

Optional: `*_PASSWORD_FILE=/run/secrets/...`, `*_IMAP_ID_MODE=always`,
`*_ALLOW_PROTOCOL_COMMANDS=true` (raw `email_command`; keep this off unless you
need it). Recipients still require `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS` (empty
= deny all sends, including reply / reply-all / forward / save_draft / send_draft).

只改环境变量即可，不必打开管理 UI。把账号名加入 `MCP_EMAIL_SERVER_ACCOUNTS`，再配一组
`MCP_EMAIL_SERVER_ACCOUNT_<name>_*`，然后重启。空的收件人白名单会拒绝发信，包括回复、转发、存草稿和发送草稿。

## 5. Per-provider authorization codes

Presets never include passwords. Generate a **client authorization code** (not
the web login password) on each provider, then put that code in `*_PASSWORD`.

各预设不会填写密码。必须在网页上开通 IMAP/SMTP 并生成**客户端授权码**（不是网页登录密码），再写入 `*_PASSWORD`。

### 163 / 126 (NetEase)

1. Sign in at [mail.163.com](https://mail.163.com) (or 126).
2. Settings → POP3/SMTP/IMAP → enable IMAP/SMTP.
3. Generate a 16-character authorization code. Use that as `PASSWORD`.
4. Keep `PRESET=163` (or `126`): IMAP ID is sent even when CAPABILITY omits it,
   and Subject/From/To search is local (163 SEARCH is often empty).

5. 登录 163/126 网页邮箱。
6. 设置 → POP3/SMTP/IMAP → 开启 IMAP/SMTP。
7. 生成 16 位授权码，填入 `PASSWORD`（不是网页密码）。
8. 使用 `PRESET=163` 或 `126`：即使 CAPABILITY 没有 ID 也会发 RFC 2971 ID；主题/发件人/收件人搜索走本地（163 的 SEARCH 经常为空）。

### Aliyun enterprise (`qiye.aliyun.com`)

1. Sign in to the Aliyun enterprise mailbox admin / user settings.
2. Enable IMAP/SMTP if the tenant has it disabled.
3. Use the mailbox password or the client password the admin issued.
4. `PRESET=aliyun` → `imap.qiye.aliyun.com:993` / `smtp.qiye.aliyun.com:465` SSL.

5. 登录阿里云企业邮箱管理端或用户设置。
6. 若租户关闭了 IMAP/SMTP，先开启。
7. 使用邮箱密码或管理员下发的客户端密码。
8. `PRESET=aliyun` 会填入 `imap.qiye.aliyun.com:993` 与 `smtp.qiye.aliyun.com:465`（SSL）。

### iCloud

1. Open [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security →
   App-Specific Passwords.
2. Generate a password for “Mail”. Use it as `PASSWORD` (the Apple ID password
   will not authenticate IMAP/SMTP).
3. `PRESET=icloud` → IMAP `imap.mail.me.com:993` SSL, SMTP
   `smtp.mail.me.com:587` STARTTLS.

4. 打开 Apple ID → 登录与安全 → App 专用密码。
5. 为“邮件”生成专用密码，填入 `PASSWORD`（Apple ID 密码不能登录 IMAP/SMTP）。
6. `PRESET=icloud`：IMAP `imap.mail.me.com:993` SSL，SMTP `smtp.mail.me.com:587` STARTTLS。

See [providers.md](providers.md) for QQ and Tsinghua Coremail.

QQ 与清华 Coremail 见 [providers.md](providers.md)。

## 6. Backup and wiping the encrypted cache

The cache lives in the `mcp-email-cache` volume (`MCP_EMAIL_CACHE_DIR`, default
`/data/cache`). The SQLite file `mail-cache.sqlite3` stores AES-GCM ciphertext
and HMAC-SHA256 tokens for account/mailbox names. Subjects and addresses are
not stored in plaintext. Credentials are never written.

**Backup.** Stop the container, archive the volume or copy
`/data/cache/mail-cache.sqlite3` plus the current cache key (without the key the
file is useless). Restart.

**Wipe.** Stop the container, delete the cache volume or the sqlite file, start
again. A mailbox UIDVALIDITY change also drops that mailbox's rows automatically.

缓存位于 `mcp-email-cache` 卷。SQLite 里只有密文和账号/邮箱名的 HMAC 令牌，没有明文主题或地址，也不会写口令。
备份时停容器，复制 sqlite 文件和当前密钥（没有密钥文件无用）。清空时删除卷或文件后重启。UIDVALIDITY 变化会自动丢掉该邮箱的行。

Local header search (`imap_search_mode=local`, used by the 163 preset) decrypts
rows in process; it works while the cache is encrypted.

163 的本地主题搜索会在进程内解密，加密缓存开启时仍然可用。

## 7. Troubleshooting

| Symptom                                 | Check                                                                                                                                                        |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Container exits immediately             | HTTP started without a token, or `MCP_ACCESS_MODE=lan` with insecure/missing `MCP_ALLOWED_HOSTS`.                                                            |
| `/healthz` OK, MCP client 401           | Missing or wrong `Authorization: Bearer`. Rotate with `MCP_EMAIL_HTTP_BEARER_TOKENS`.                                                                        |
| MCP client 421/invalid Host             | Add the NAS LAN IP:port and hostname to `MCP_ALLOWED_HOSTS`.                                                                                                 |
| 163 LOGIN OK then idle failures         | Keep `PRESET=163` (`imap_id_mode=always`).                                                                                                                   |
| 163 SEARCH returns nothing              | Expected; local search + encrypted cache. Confirm `MCP_EMAIL_CACHE_KEY` if you want the cache.                                                               |
| `tool_unavailable`                      | Tool is denied (`MCP_EMAIL_SERVER_TOOL_DENY` / allowlist) or `email_command` without `allow_protocol_commands`.                                              |
| `get_attachment_content` missing/denied | Needs **both** `MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_CONTENT=true` (or TOML `enable_attachment_content`) **and** the tool in `TOOL_ALLOW` if that list is set. |
| Send / reply / forward denied           | Empty recipient allowlist denies all. Set `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS`.                                                                             |
| Attachment path rejected                | Outgoing files must sit inside `MCP_EMAIL_SERVER_ATTACHMENT_DIR`. Forwarded IMAP parts are not subject to that directory.                                    |
| Draft still in Drafts after send_draft  | Default `MCP_EMAIL_SERVER_SEND_DRAFT=safe` keeps the draft unless a Sent copy was saved. Set `delete` or enable `save_to_sent`.                              |
| Cache search empty after key change     | Wipe the cache; old ciphertext cannot be read with a new key.                                                                                                |
| Read-only root FS write errors          | Data belongs on `/data` and `/tmp` (tmpfs). Do not write under `/app`.                                                                                       |

| 现象                            | 排查                                                                                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 容器立刻退出                    | HTTP 未配置令牌，或 `MCP_ACCESS_MODE=lan` 开了 insecure / 没写 `MCP_ALLOWED_HOSTS`。                                                                |
| `/healthz` 正常，MCP 客户端 401 | Bearer 缺失或错误。用 `MCP_EMAIL_HTTP_BEARER_TOKENS` 轮换。                                                                                         |
| 客户端报 Host 无效              | 把 NAS 局域网 IP:端口和主机名加入 `MCP_ALLOWED_HOSTS`。                                                                                             |
| 163 能 LOGIN 随后失败           | 保持 `PRESET=163`（始终发 IMAP ID）。                                                                                                               |
| 163 SEARCH 为空                 | 预期行为；用本地搜索 + 加密缓存。                                                                                                                   |
| `tool_unavailable`              | 工具被 allow/deny 排除，或未给账号打开 `email_command`。                                                                                            |
| `get_attachment_content` 不可用 | 需要同时打开 `MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_CONTENT=true`（或 TOML `enable_attachment_content`），并在设置了 `TOOL_ALLOW` 时把该工具写进列表。 |
| send_draft 后草稿还在           | 默认 `MCP_EMAIL_SERVER_SEND_DRAFT=safe`：只有成功写入已发送时才删草稿。需要的话设 `delete` 或打开 `save_to_sent`。                                  |
| 发信/回复/转发被拒              | 空收件人白名单默认拒绝。设置 `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS`。                                                                                |
| 附件路径被拒                    | 本地附件必须在 `MCP_EMAIL_SERVER_ATTACHMENT_DIR` 内。转发原信的 IMAP 附件不受该目录限制。                                                           |
| 换密钥后缓存搜索为空            | 清空缓存；旧密文不能用新密钥读。                                                                                                                    |
| 只读根文件系统写失败            | 数据写 `/data` 和 `/tmp`（tmpfs），不要写 `/app`。                                                                                                  |

More protocol-level notes: [troubleshooting.md](troubleshooting.md),
[security.md](security.md), [transports.md](transports.md).
