"""Named IMAP/SMTP connection presets for common personal-mail providers.

Presets fill hosts, ports, TLS mode, IMAP ID behaviour, and header-search
mode when an account does not set those fields explicitly. They never
supply credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ImapIdMode = Literal["auto", "always", "never"]
ImapSearchMode = Literal["server", "local"]


@dataclass(frozen=True)
class ProviderPreset:
    """Non-secret connection defaults for one well-known mail host."""

    name: str
    label: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    imap_start_ssl: bool
    smtp_host: str
    smtp_port: int
    smtp_ssl: bool
    smtp_start_ssl: bool
    imap_id_mode: ImapIdMode
    imap_search_mode: ImapSearchMode
    notes: str


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "163": ProviderPreset(
        name="163",
        label="NetEase 163 Mail",
        imap_host="imap.163.com",
        imap_port=993,
        imap_ssl=True,
        imap_start_ssl=False,
        smtp_host="smtp.163.com",
        smtp_port=465,
        smtp_ssl=True,
        smtp_start_ssl=False,
        imap_id_mode="always",
        imap_search_mode="local",
        notes=(
            "Use a 16-character authorization code, not the web-mail password. "
            "NetEase requires RFC 2971 IMAP ID after LOGIN even when CAPABILITY "
            "omits ID. Server SEARCH is unreliable; local header search is the default."
        ),
    ),
    "126": ProviderPreset(
        name="126",
        label="NetEase 126 Mail",
        imap_host="imap.126.com",
        imap_port=993,
        imap_ssl=True,
        imap_start_ssl=False,
        smtp_host="smtp.126.com",
        smtp_port=465,
        smtp_ssl=True,
        smtp_start_ssl=False,
        imap_id_mode="always",
        imap_search_mode="local",
        notes="Same IMAP ID and authorization-code requirements as 163 Mail.",
    ),
    "aliyun": ProviderPreset(
        name="aliyun",
        label="Aliyun Enterprise Mail (qiye.aliyun.com)",
        imap_host="imap.qiye.aliyun.com",
        imap_port=993,
        imap_ssl=True,
        imap_start_ssl=False,
        smtp_host="smtp.qiye.aliyun.com",
        smtp_port=465,
        smtp_ssl=True,
        smtp_start_ssl=False,
        imap_id_mode="auto",
        imap_search_mode="server",
        notes="Enterprise mailbox on imap.qiye.aliyun.com:993/SSL and smtp.qiye.aliyun.com:465/SSL.",
    ),
    "icloud": ProviderPreset(
        name="icloud",
        label="iCloud Mail",
        imap_host="imap.mail.me.com",
        imap_port=993,
        imap_ssl=True,
        imap_start_ssl=False,
        smtp_host="smtp.mail.me.com",
        smtp_port=587,
        smtp_ssl=False,
        smtp_start_ssl=True,
        imap_id_mode="auto",
        imap_search_mode="server",
        notes=(
            "Sign in with an app-specific password from appleid.apple.com. "
            "SMTP uses STARTTLS on port 587, not implicit TLS 465."
        ),
    ),
    "qq": ProviderPreset(
        name="qq",
        label="QQ Mail",
        imap_host="imap.qq.com",
        imap_port=993,
        imap_ssl=True,
        imap_start_ssl=False,
        smtp_host="smtp.qq.com",
        smtp_port=465,
        smtp_ssl=True,
        smtp_start_ssl=False,
        imap_id_mode="auto",
        imap_search_mode="server",
        notes=(
            "Use an IMAP/SMTP authorization code from QQ Mail settings. "
            "Non-ASCII SEARCH uses quoted UTF-8 strings automatically for imap.qq.com."
        ),
    ),
    "tsinghua": ProviderPreset(
        name="tsinghua",
        label="Tsinghua Coremail",
        imap_host="mails.tsinghua.edu.cn",
        imap_port=993,
        imap_ssl=True,
        imap_start_ssl=False,
        smtp_host="mails.tsinghua.edu.cn",
        smtp_port=465,
        smtp_ssl=True,
        smtp_start_ssl=False,
        imap_id_mode="always",
        imap_search_mode="server",
        notes=(
            "Coremail often wants IMAP ID after LOGIN even when it is not advertised. "
            "Confirm the current client-password / authorization-code policy with the campus mail help pages."
        ),
    ),
}

_ALIASES = {
    "netease": "163",
    "163.com": "163",
    "126.com": "126",
    "qiye.aliyun": "aliyun",
    "qiye.aliyun.com": "aliyun",
    "aliyun-enterprise": "aliyun",
    "icloud.com": "icloud",
    "me.com": "icloud",
    "mac.com": "icloud",
    "qq.com": "qq",
    "mails.tsinghua.edu.cn": "tsinghua",
    "coremail": "tsinghua",
}


def normalize_preset_name(name: str) -> str:
    """Return the canonical preset key, or the stripped lower-case input."""

    key = name.strip().casefold()
    return _ALIASES.get(key, key)


def get_provider_preset(name: str) -> ProviderPreset:
    """Look up a preset by name or alias; raise KeyError if unknown."""

    canonical = normalize_preset_name(name)
    try:
        return PROVIDER_PRESETS[canonical]
    except KeyError:
        known = ", ".join(sorted(PROVIDER_PRESETS))
        raise KeyError(f"Unknown mail provider preset {name!r}; known presets: {known}") from None
