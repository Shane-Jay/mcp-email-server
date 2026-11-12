# Synology NAS deploy kit / 群晖部署包

Reviewed compose kit for one Streamable HTTP container on a Synology NAS,
reached only over Tailscale (or another private overlay). No real secrets
are stored in this directory.

这份 compose 套件在群晖上跑一个 Streamable HTTP 容器，只通过 Tailscale
（或其他私有网络）访问。目录里没有任何真实口令。

Image: `ghcr.io/shane-jay/mcp-email-server:latest`. Pin by digest after the
first pull:

镜像使用 `latest`。第一次拉取后请用 digest 钉死：

```bash
docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/shane-jay/mcp-email-server:latest
# then set image: ghcr.io/shane-jay/mcp-email-server@sha256:...
```

## 1. Create folders / 建目录

SSH into the NAS. Container Manager GUI can create the project later; secret
files and `chown` are easier from SSH.

用 SSH 登录群晖。后面可以用 Container Manager 导入项目，密钥文件和权限建议在 SSH 里做。

```bash
sudo mkdir -p /volume1/docker/mcp-email-server/{data,cache,attachments}
sudo mkdir -p /volume1/docker/mcp-email-server/app
# Copy this deploy/synology directory into app/ (compose + env.example + secrets/).
sudo chown -R 1000:1000 /volume1/docker/mcp-email-server
sudo chmod 700 /volume1/docker/mcp-email-server/app/secrets
```

The container runs as `1000:1000`. Named or bind-mounted folders under
`/volume1/docker/mcp-email-server` must be writable by that uid. On DSM:
Control Panel → Shared Folder → Edit → Permissions, or the `chown` above.

容器以 `1000:1000` 运行。`/volume1/docker/mcp-email-server` 下的数据目录必须对
uid 1000 可写。

## 2. Generate token and cache key / 生成令牌和缓存密钥

```bash
cd /volume1/docker/mcp-email-server/app
mkdir -p secrets
umask 077
openssl rand -hex 32 > secrets/http_bearer_token
openssl rand -hex 32 > secrets/cache_key
```

## 3. Provider authorization codes / 写入邮箱授权码

Put each provider's **IMAP authorization code** (not the web-mail password)
in its secret file:

把各邮箱的 **IMAP 授权码**（不是网页登录密码）写入对应文件：

```bash
printf '%s\n' '163-authorization-code' > secrets/netease_auth_code
printf '%s\n' 'aliyun-client-password' > secrets/aliyun_auth_code
printf '%s\n' 'icloud-app-specific-password' > secrets/icloud_app_password
chmod 600 secrets/*
```

| File                          | Account | Preset   | Where to get it                              |
| ----------------------------- | ------- | -------- | -------------------------------------------- |
| `secrets/netease_auth_code`   | 163     | `163`    | mail.163.com → IMAP/SMTP → 16-char auth code |
| `secrets/aliyun_auth_code`    | Aliyun  | `aliyun` | qiye.aliyun.com client / mailbox password    |
| `secrets/icloud_app_password` | iCloud  | `icloud` | appleid.apple.com → App-Specific Passwords   |

## 4. Environment file / 环境变量

```bash
cp env.example .env
chmod 600 .env
```

Edit `.env`:

- `MCP_BIND_IP` — NAS Tailscale IP (`100.x`), **not** `0.0.0.0`
- `MCP_ALLOWED_HOSTS` — MagicDNS name and that same tailnet IP
- `MCP_EMAIL_SERVER_ACCOUNT_*_EMAIL_ADDRESS` — the three placeholder addresses
- `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS` — who the tools may send to

Do not put the bearer token or mailbox passwords in `.env`. Compose points
`*_FILE` at `/run/secrets/...`.

不要把 Bearer 令牌或邮箱口令写进 `.env`。compose 会把 `*_FILE` 指到
`/run/secrets/...`。

Optional: `cp config.example.toml /volume1/docker/mcp-email-server/data/config.toml`
if you want a TOML overlay. Accounts still come from env + secret files.

## 5. Start / 启动

From the directory that contains `docker-compose.yml` and `.env`:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

Container Manager: **Project** → create from this folder. If the GUI cannot
mount Docker secrets, use the SSH commands above.

若套件中心无法挂载 Docker secrets，请用上面的 SSH 命令。

## 6. Verify / 验证

Replace the host with your MagicDNS name or tailnet IP:

把主机换成你的 MagicDNS 名或 tailnet IP：

```bash
curl -fsS "http://nas.tail-xxxx.ts.net:9557/healthz"
# from this repository:
scripts/smoke.sh "http://nas.tail-xxxx.ts.net:9557" ./secrets/http_bearer_token
```

`/healthz` is unauthenticated. `tools/list` requires
`Authorization: Bearer <token>`. The smoke script does both.

`/healthz` 不需要令牌。`tools/list` 需要 Bearer。`scripts/smoke.sh` 会做这两步。

Manual `tools/list` (same handshake the smoke script performs):

```bash
TOKEN=$(tr -d '\n' < secrets/http_bearer_token)
curl -fsS "http://nas.tail-xxxx.ts.net:9557/healthz"
```

MCP clients use `http://<MagicDNS-or-IP>:9557/mcp` with that bearer token.
Do not publish port 9557 on the public internet. Do not run the management UI
in this container.

不要把 9557 映射到公网。不要在此容器里启动管理 UI。

## 7. Rotate the HTTP token / 轮换令牌

1. `openssl rand -hex 32 > secrets/http_bearer_token.new`
2. Overlap (optional): put both values in `.env` as
   `MCP_EMAIL_HTTP_BEARER_TOKENS=new,old` and comment out the secret-file
   mapping in compose, **or** wait until every client can switch at once.
3. `mv secrets/http_bearer_token.new secrets/http_bearer_token`
4. `docker compose up -d --force-recreate`
5. Point clients at the new token. Drop `MCP_EMAIL_HTTP_BEARER_TOKENS` if used.

Changing `secrets/cache_key` makes existing ciphertext unreadable. Stop the
container, empty `/volume1/docker/mcp-email-server/cache`, then start again.

更换缓存密钥会使旧密文无法解密。停容器、清空 cache 目录再启动。

## Hardening already in compose / compose 已做的加固

- Host bind: `${MCP_BIND_IP}:9557`, default `100.64.0.1`
- `user: "1000:1000"`, `read_only: true`, `tmpfs` on `/tmp`
- `cap_drop: ALL`, `security_opt: no-new-privileges:true`
- Healthcheck against `/healthz`
- IMAP pool on, `SEND_DRAFT=safe`, conservative `TOOL_ALLOW`
- Attachment directory bind-mounted at `/data/attachments`

See also `docs/OPERATIONS.md` in the repository root.
