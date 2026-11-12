# Secret files (not committed)

Create these files next to this README. One secret per file, no trailing
spaces, a single trailing newline is fine. Mode `600`.

| File                  | Contents                                     |
| --------------------- | -------------------------------------------- |
| `http_bearer_token`   | `openssl rand -hex 32`                       |
| `cache_key`           | `openssl rand -hex 32`                       |
| `netease_auth_code`   | 163 IMAP authorization code                  |
| `aliyun_auth_code`    | Aliyun enterprise client password            |
| `icloud_app_password` | iCloud app-specific password (Apple ID page) |

Compose mounts them at `/run/secrets/<name>`.
