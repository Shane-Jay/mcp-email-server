"""Load one or more legacy-mode email accounts from environment variables.

The unprefixed ``MCP_EMAIL_SERVER_*`` variables continue to define a single
account (upstream behaviour). ``MCP_EMAIL_SERVER_ACCOUNTS`` lists additional
account names loaded from ``MCP_EMAIL_SERVER_ACCOUNT_<name>_*``.
"""

from __future__ import annotations

import os
from typing import Any

from mcp_email_server.log import log_safe_failure, logger
from mcp_email_server.provider_presets import ProviderPreset, get_provider_preset, normalize_preset_name
from mcp_email_server.secrets_io import read_secret_from_env

_PREFIX = "MCP_EMAIL_SERVER_"
_ACCOUNT_PREFIX = "MCP_EMAIL_SERVER_ACCOUNT_"


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, prefix: str) -> str | None:
    return os.environ.get(f"{prefix}{name}")


def _apply_preset(values: dict[str, Any], preset: ProviderPreset) -> dict[str, Any]:
    """Fill unset connection fields from a named provider preset."""

    values.setdefault("imap_host", preset.imap_host)
    values.setdefault("imap_port", preset.imap_port)
    values.setdefault("imap_ssl", preset.imap_ssl)
    values.setdefault("imap_start_ssl", preset.imap_start_ssl)
    values.setdefault("smtp_host", preset.smtp_host)
    values.setdefault("smtp_port", preset.smtp_port)
    values.setdefault("smtp_ssl", preset.smtp_ssl)
    values.setdefault("smtp_start_ssl", preset.smtp_start_ssl)
    values.setdefault("imap_id_mode", preset.imap_id_mode)
    values.setdefault("imap_search_mode", preset.imap_search_mode)
    if preset.save_to_sent is not None:
        values.setdefault("save_to_sent", preset.save_to_sent)
    return values


def collect_account_kwargs(prefix: str, *, account_name: str | None = None) -> dict[str, Any] | None:
    """Read one account's constructor kwargs from an environment prefix.

    ``prefix`` is ``MCP_EMAIL_SERVER_`` or ``MCP_EMAIL_SERVER_ACCOUNT_work_``.
    Returns None when the minimum identity (email + password + IMAP host after
    preset) is not present.
    """

    try:
        return _collect_account_kwargs(prefix, account_name=account_name)
    except (ValueError, TypeError) as exc:
        log_safe_failure("Failed to parse email account environment variables", exc)
        return None


def _collect_account_kwargs(prefix: str, *, account_name: str | None = None) -> dict[str, Any] | None:  # noqa: C901
    email_address = _env("EMAIL_ADDRESS", prefix)
    password = read_secret_from_env(f"{prefix}PASSWORD", file_env_name=f"{prefix}PASSWORD_FILE")
    if not email_address or not password:
        return None

    preset_name = _env("PRESET", prefix)
    preset: ProviderPreset | None = None
    if preset_name:
        try:
            preset = get_provider_preset(preset_name)
        except KeyError:
            logger.error(f"Unknown mail provider preset {preset_name!r} for account {account_name or email_address}")
            return None

    def _optional_int(name: str) -> int | None:
        raw = _env(name, prefix)
        if raw is None or not raw.strip():
            return None
        return int(raw.strip())

    def _optional_bool(name: str) -> bool | None:
        raw = _env(name, prefix)
        if raw is None:
            return None
        return _parse_bool(raw, False)

    values: dict[str, Any] = {
        "account_name": account_name or _env("ACCOUNT_NAME", prefix) or "default",
        "full_name": _env("FULL_NAME", prefix) or email_address.split("@")[0],
        "email_address": email_address,
        "user_name": _env("USER_NAME", prefix) or email_address,
        "password": password,
    }
    imap_host = _env("IMAP_HOST", prefix)
    if imap_host:
        values["imap_host"] = imap_host
    smtp_host = _env("SMTP_HOST", prefix)
    if smtp_host:
        values["smtp_host"] = smtp_host
    imap_port = _optional_int("IMAP_PORT")
    if imap_port is not None:
        values["imap_port"] = imap_port
    smtp_port = _optional_int("SMTP_PORT")
    if smtp_port is not None:
        values["smtp_port"] = smtp_port
    for field, env_name in (
        ("imap_ssl", "IMAP_SSL"),
        ("imap_start_ssl", "IMAP_START_SSL"),
        ("imap_verify_ssl", "IMAP_VERIFY_SSL"),
        ("smtp_ssl", "SMTP_SSL"),
        ("smtp_start_ssl", "SMTP_START_SSL"),
        ("smtp_verify_ssl", "SMTP_VERIFY_SSL"),
        ("save_to_sent", "SAVE_TO_SENT"),
        ("allow_protocol_commands", "ALLOW_PROTOCOL_COMMANDS"),
    ):
        parsed = _optional_bool(env_name)
        if parsed is not None:
            values[field] = parsed
    imap_user = _env("IMAP_USER_NAME", prefix)
    if imap_user:
        values["imap_user_name"] = imap_user
    smtp_user = _env("SMTP_USER_NAME", prefix)
    if smtp_user:
        values["smtp_user_name"] = smtp_user
    imap_password = read_secret_from_env(f"{prefix}IMAP_PASSWORD", file_env_name=f"{prefix}IMAP_PASSWORD_FILE")
    if imap_password:
        values["imap_password"] = imap_password
    smtp_password = read_secret_from_env(f"{prefix}SMTP_PASSWORD", file_env_name=f"{prefix}SMTP_PASSWORD_FILE")
    if smtp_password:
        values["smtp_password"] = smtp_password
    sent_folder = _env("SENT_FOLDER_NAME", prefix)
    if sent_folder:
        values["sent_folder_name"] = sent_folder
    drafts = _env("DRAFTS_MAILBOX", prefix)
    if drafts:
        values["drafts_mailbox"] = drafts
    id_mode = _env("IMAP_ID_MODE", prefix)
    if id_mode:
        values["imap_id_mode"] = id_mode.strip().casefold()
    search_mode = _env("IMAP_SEARCH_MODE", prefix)
    if search_mode:
        values["imap_search_mode"] = search_mode.strip().casefold()
    if preset is not None:
        _apply_preset(values, preset)
        if "account_name" in values and account_name is None and values["account_name"] == "default":
            values["account_name"] = normalize_preset_name(preset_name or preset.name)
    if "imap_host" not in values:
        logger.warning("Missing required email configuration environment variable: IMAP_HOST")
        return None
    return values


def iter_env_account_specs() -> list[dict[str, Any]]:
    """Return constructor kwargs for every environment-defined account."""

    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    unprefixed = collect_account_kwargs(_PREFIX)
    if unprefixed is not None:
        specs.append(unprefixed)
        seen.add(unprefixed["account_name"])
    named = os.environ.get("MCP_EMAIL_SERVER_ACCOUNTS")
    if not named:
        return specs
    for raw_name in named.split(","):
        name = raw_name.strip()
        if not name:
            continue
        prefix = f"{_ACCOUNT_PREFIX}{name}_"
        spec = collect_account_kwargs(prefix, account_name=name)
        if spec is None:
            logger.warning(f"Skipping environment account {name!r}: incomplete configuration")
            continue
        spec["account_name"] = name
        if name in seen:
            specs[:] = [item if item["account_name"] != name else spec for item in specs]
        else:
            specs.append(spec)
            seen.add(name)
    return specs
