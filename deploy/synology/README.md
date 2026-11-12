# Synology NAS deploy kit / 群晖部署包

Reviewed compose kit for one Streamable HTTP container on a Synology NAS.
Default access mode is **LAN** (`0.0.0.0`): home LAN clients and devices that
arrive through the owner's WireGuard (routed subnets, for example
`192.168.1.0/24` and `10.8.0.0/24`) can reach port 9557. Switch to
**local** (`127.0.0.1`) to listen on the NAS itself only. No real secrets
are stored in this directory.

这份 compose 套件在群晖上跑一个 Streamable HTTP 容器。默认 **lan** 模式绑定
`0.0.0.0`，家里局域网以及经 WireGuard 路由进来的网段（例如 `192.168.1.0/24`、
`10.8.0.0/24`）都能访问 9557。改成 **local** 则只监听本机回环。目录里没有
任何真实口令。

Image: `ghcr.io/shane-jay/mcp-email-server:latest`. Pin by digest after the
first pull:

镜像使用 `latest`。第一次拉取后请用 digest 钉死：

```bash
docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/shane-jay/mcp-email-server:latest
# then set image: ghcr.io/shane-jay/mcp-email-server@sha256:...
```

LAN traffic to this container is **plain HTTP**. Prefer reaching it through
WireGuard, or put Synology Reverse Proxy (HTTPS) in front. Do not publish
port 9557 on the public internet.

局域网到本容器是 **明文 HTTP**。更稳妥的做法是走 WireGuard，或在前面加群晖反代
HTTPS。不要把 9557 映射到公网。

## Access mode / 访问模式

Compose v2 / Container Manager cannot compute bind addresses. Set
**both** `MCP_ACCESS_MODE` and `MCP_BIND_IP` to a matching pair in `.env`.
The server also reads `MCP_ACCESS_MODE`: `lan` and `local` refuse
`MCP_EMAIL_HTTP_INSECURE=true` and require a bearer token. `lan` additionally
requires a non-wildcard `MCP_ALLOWED_HOSTS` list, with DNS-rebinding
protection left on.

群晖 Container Manager 不能根据模式自动算出绑定地址。请在 `.env` 里把
`MCP_ACCESS_MODE` 和 `MCP_BIND_IP` **成对**填好。服务端也会读
`MCP_ACCESS_MODE`：`lan` / `local` 都拒绝关闭鉴权，且必须有 Bearer。`lan`
还要求明确的 `MCP_ALLOWED_HOSTS`（不能是 `*`），并保持 DNS 重绑定防护。

### `lan` (default)

```bash
MCP_ACCESS_MODE=lan
MCP_BIND_IP=0.0.0.0
MCP_ALLOWED_HOSTS=192.168.1.50:9557,nas.local:9557
MCP_ENABLE_DNS_REBINDING_PROTECTION=true
MCP_EMAIL_HTTP_INSECURE=false
```

Replace `192.168.1.50` and `nas.local` with this NAS LAN IP and hostname
(the `Host` header the MCP client will send). Routed WireGuard clients use
the same Host values; you do not list client subnets here.

把 `192.168.1.50` 和 `nas.local` 换成这台 NAS 的局域网 IP 和主机名（客户端
Host 头）。WireGuard 对端连过来时 Host 仍是 NAS 的地址，不要把客户端网段写进
`MCP_ALLOWED_HOSTS`。

### `local`

```bash
MCP_ACCESS_MODE=local
MCP_BIND_IP=127.0.0.1
MCP_ALLOWED_HOSTS=127.0.0.1:9557,localhost:9557
MCP_ENABLE_DNS_REBINDING_PROTECTION=true
MCP_EMAIL_HTTP_INSECURE=false
```

CLI alternative: `docker compose -f docker-compose.yml -f compose.local.yml up -d`.
Container Manager users should change `.env` instead of extra compose files.

命令行也可用 `compose.local.yml` 覆盖端口。套件中心用户改 `.env` 即可。

## 1. Create folders / 建目录

SSH into the NAS. Container Manager GUI can create the project later; secret
files and permissions are easier from SSH.

用 SSH 登录群晖。后面可以用 Container Manager 导入项目，密钥文件和权限建议在 SSH 里做。

```bash
sudo mkdir -p /volume1/docker/mcp-email-server/{data,cache,attachments}
sudo mkdir -p /volume1/docker/mcp-email-server/app/secrets
# Copy this deploy/synology directory into app/ (compose + env.example + secrets/).

# Data dirs must be writable by the container user (uid 1000).
sudo chown 1000:1000 /volume1/docker/mcp-email-server/data \
  /volume1/docker/mcp-email-server/cache \
  /volume1/docker/mcp-email-server/attachments

# Secret files are written by your DSM user and read by the container through
# group `users` (gid 100). Do not chown the project tree or the secrets
# directory to 1000:1000, and do not chmod 700 the secrets directory.
# Compose sets user: "1000:1000" and group_add: ["100"].
sudo chown "$(id -u):100" /volume1/docker/mcp-email-server/app/secrets
sudo chmod 750 /volume1/docker/mcp-email-server/app/secrets
```

Data, cache, and attachment bind mounts must be writable by uid 1000 (the
commands above, or DSM: Control Panel → Shared Folder → Edit → Permissions).

Secret files stay owned by your DSM user, group `users` (gid 100), mode `640`;
the secrets directory is mode `750`. On typical DSM systems the admin uid is
not 1000 (often 1026 or similar); use `id -u` and group `users` rather than
hard-coding a uid. The container can read those files because compose adds
`group_add: ["100"]`.

数据、缓存和附件目录必须对容器 uid 1000 可写。密钥文件归你的 DSM 用户所有，组为
`users`（gid 100），文件权限 `640`，目录 `750`。不要把整个项目或 secrets 目录
`chown` 成 `1000:1000`，也不要把 secrets 目录设为 `700`。compose 已加入
`group_add: ["100"]`，容器才能以组成员身份读取这些文件。

## 2. Generate token and cache key / 生成令牌和缓存密钥

```bash
cd /volume1/docker/mcp-email-server/app
mkdir -p secrets
umask 027
openssl rand -hex 32 > secrets/http_bearer_token
openssl rand -hex 32 > secrets/cache_key
chown "$(id -u):100" secrets/http_bearer_token secrets/cache_key
chmod 640 secrets/http_bearer_token secrets/cache_key
chmod 750 secrets
```

## 3. Provider authorization codes / 写入邮箱授权码

Put each provider's **IMAP authorization code** (not the web-mail password)
in its secret file:

把各邮箱的 **IMAP 授权码**（不是网页登录密码）写入对应文件：

```bash
printf '%s\n' '163-authorization-code' > secrets/netease_auth_code
printf '%s\n' 'aliyun-client-password' > secrets/aliyun_auth_code
printf '%s\n' 'icloud-app-specific-password' > secrets/icloud_app_password
chown "$(id -u):100" secrets/*
chmod 640 secrets/*
chmod 750 secrets
```

If a secret file is unreadable inside the container (wrong owner, group, or
mode), that account is skipped with `incomplete configuration`. Check the
container logs if an account is missing after startup. The HTTP bearer token
and cache key use the same ownership and mode; those failures show up as
auth or cache errors rather than a skipped account.

如果容器读不到某个密钥文件（属主、组或权限不对），对应账户会被跳过，日志里
出现 `incomplete configuration`。启动后若缺账户，先查容器日志。Bearer 令牌
和缓存密钥用同样的属主和权限；这两项读失败会表现为鉴权或缓存错误，而不是跳过账户。

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

- `MCP_ACCESS_MODE` / `MCP_BIND_IP` — matching pair (`lan`/`0.0.0.0` or `local`/`127.0.0.1`)
- `MCP_ALLOWED_HOSTS` — NAS LAN IP:port and hostname the client will send
- `MCP_EMAIL_SERVER_ACCOUNT_*_EMAIL_ADDRESS` — the three placeholder addresses
- `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS` — who the tools may send to

Do not put the bearer token or mailbox passwords in `.env`. Compose points
`*_FILE` at `/run/secrets/...`. Leave `MCP_EMAIL_HTTP_INSECURE=false`.

不要把 Bearer 令牌或邮箱口令写进 `.env`。compose 会把 `*_FILE` 指到
`/run/secrets/...`。不要打开 `MCP_EMAIL_HTTP_INSECURE`。

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

Replace the host with this NAS LAN IP or hostname (`lan`), or `127.0.0.1`
(`local`):

把主机换成 NAS 局域网 IP / 主机名（`lan`），或 `127.0.0.1`（`local`）：

```bash
curl -fsS "http://192.168.1.50:9557/healthz"
scripts/smoke.sh "http://192.168.1.50:9557" ./secrets/http_bearer_token
```

`/healthz` is unauthenticated. `tools/list` requires
`Authorization: Bearer <token>`. The smoke script does both.

`/healthz` 不需要令牌。`tools/list` 需要 Bearer。`scripts/smoke.sh` 会做这两步。

MCP clients use `http://<NAS-LAN-IP-or-hostname>:9557/mcp` with that bearer
token. Do not run the management UI in this container.

不要在此容器里启动管理 UI。

## 7. HTTPS via Synology Reverse Proxy / 群晖反代 HTTPS（可选）

Port 9557 on the LAN is still HTTP. For TLS, keep `MCP_ACCESS_MODE=local` /
`MCP_BIND_IP=127.0.0.1` and terminate HTTPS on DSM:

1. Control Panel → Login Portal → Advanced → Reverse Proxy → Create.
2. Source: HTTPS, hostname such as `mcp.nas.local`, port `443`.
3. Destination: `http://127.0.0.1:9557`.
4. Add that hostname to `MCP_ALLOWED_HOSTS` (and `MCP_ALLOWED_ORIGINS` if a
   browser client sends Origin).

Then point MCP clients at `https://mcp.nas.local/mcp` with the same bearer
token. WireGuard to the LAN IP remains the simpler path when you do not need
a browser.

局域网 9557 仍是 HTTP。若要 TLS：保持 `local` 绑定本机，用 DSM **登录门户 →
高级 → 反向代理** 把 HTTPS 主机名转到 `http://127.0.0.1:9557`，并把该主机名
写入 `MCP_ALLOWED_HOSTS`。不需要浏览器时，用 WireGuard 访问局域网 IP 更简单。

## 8. Rotate the HTTP token / 轮换令牌

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

- Access mode pair: `MCP_ACCESS_MODE` + `MCP_BIND_IP` (default lan / `0.0.0.0`)
- Bearer token required; `MCP_EMAIL_HTTP_INSECURE=false`
- `user: "1000:1000"`, `group_add: ["100"]`, `read_only: true`, `tmpfs` on `/tmp`
- `cap_drop: ALL`, `security_opt: no-new-privileges:true`
- Healthcheck against `/healthz`
- IMAP pool on, `SEND_DRAFT=safe`, conservative `TOOL_ALLOW`, recipient allowlist
- Attachment directory bind-mounted at `/data/attachments`

See also `docs/OPERATIONS.md` in the repository root.
