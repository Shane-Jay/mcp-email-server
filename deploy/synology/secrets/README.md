# Secret files (not committed)

Create these files next to this README. One secret per file, no trailing
spaces, a single trailing newline is fine.

Ownership and mode: owner = your DSM user, group = `users` (gid 100),
file mode `640`, directory mode `750`. The container runs as `1000:1000`
with `group_add: ["100"]` so it can read them. Do not chown these files
to `1000:1000` or set mode `600` — an unreadable password file causes that
account to be skipped with `incomplete configuration`.

| File                  | Contents                                     |
| --------------------- | -------------------------------------------- |
| `http_bearer_token`   | `openssl rand -hex 32`                       |
| `cache_key`           | `openssl rand -hex 32`                       |
| `netease_auth_code`   | 163 IMAP authorization code                  |
| `aliyun_auth_code`    | Aliyun enterprise client password            |
| `icloud_app_password` | iCloud app-specific password (Apple ID page) |

Compose mounts them at `/run/secrets/<name>`.
