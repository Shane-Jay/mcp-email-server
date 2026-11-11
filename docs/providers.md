# Provider presets

Named presets fill IMAP/SMTP hosts, ports, TLS mode, RFC 2971 IMAP ID
behaviour, and header-search mode. They never supply passwords. Set
`MCP_EMAIL_SERVER_ACCOUNT_<name>_PRESET` (or `MCP_EMAIL_SERVER_PRESET` for the
unprefixed account) to one of the keys below.

| Key | Service | IMAP | SMTP | IMAP ID | Search | Auth notes |
| --- | --- | --- | --- | --- | --- | --- |
| `163` | NetEase 163 Mail | `imap.163.com:993` SSL | `smtp.163.com:465` SSL | `always` | `local` | 16-character authorization code, not the web password |
| `126` | NetEase 126 Mail | `imap.126.com:993` SSL | `smtp.126.com:465` SSL | `always` | `local` | Same authorization-code rules as 163 |
| `aliyun` | Aliyun enterprise | `imap.qiye.aliyun.com:993` SSL | `smtp.qiye.aliyun.com:465` SSL | `auto` | `server` | Enterprise mailbox password / client password |
| `icloud` | iCloud Mail | `imap.mail.me.com:993` SSL | `smtp.mail.me.com:587` STARTTLS | `auto` | `server` | App-specific password from appleid.apple.com |
| `qq` | QQ Mail | `imap.qq.com:993` SSL | `smtp.qq.com:465` SSL | `auto` | `server` | IMAP/SMTP authorization code; non-ASCII SEARCH is quoted automatically |
| `tsinghua` | Tsinghua Coremail | `mails.tsinghua.edu.cn:993` SSL | `mails.tsinghua.edu.cn:465` SSL | `always` | `server` | Confirm the current client-password policy with campus mail help |

Aliases: `netease` → `163`, `qiye.aliyun.com` → `aliyun`, `me.com`/`mac.com` → `icloud`, `coremail` → `tsinghua`.

Explicit `IMAP_HOST`, `SMTP_PORT`, `IMAP_ID_MODE`, and similar variables override the preset.

## NetEase 163

163.com frequently:

- rejects passwords that are not the 16-character authorization code;
- wants a compact RFC 2971 `ID` after `LOGIN` even when `CAPABILITY` omits `ID`;
- returns empty or incomplete `SEARCH` results for Subject/From/To.

This tree defaults the 163 preset to `imap_id_mode=always` and
`imap_search_mode=local`. Local search uses a metadata snapshot (and, when
configured, the encrypted header cache) instead of trusting server SEARCH.

## Aliyun enterprise

Use the `qiye.aliyun.com` hosts, implicit TLS on both IMAP 993 and SMTP 465.
Keep `verify_ssl=true`.

## iCloud

SMTP is STARTTLS on 587 (`use_ssl=false`, `start_ssl=true`). IMAP remains
implicit TLS 993. Generate an app-specific password; the Apple ID password
will not authenticate IMAP/SMTP.

## QQ Mail

Use an authorization code. Non-ASCII SEARCH already uses quoted UTF-8 strings
for `imap.qq.com` in upstream `auto` mode.

## Tsinghua Coremail

Coremail deployments often require IMAP ID after login. The `tsinghua` preset
sends ID unconditionally. Re-check hosts if the campus documents a different
frontend.
