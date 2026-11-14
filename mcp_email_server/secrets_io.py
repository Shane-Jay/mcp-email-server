"""Load secrets from environment variables or secret files.

Values from files are stripped of a single trailing newline so Docker and
systemd credential files work as-is. Empty files are treated as missing.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp_email_server.log import register_secret


class SecretFileError(OSError):
    """A configured secret file exists but cannot be read.

    The message names the environment variable and path. It never includes the
    file contents.
    """


def read_secret_file(path: str | os.PathLike[str], *, source_env: str | None = None) -> str | None:
    """Return the contents of a secret file, or None if it is missing or empty.

    Permission errors and other OS errors raise ``SecretFileError``. A missing
    file is treated as unset so an optional secret can stay absent.
    """

    file_path = Path(path)
    try:
        payload = file_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        env_label = source_env or "the configured secret-file variable"
        raise SecretFileError(
            f"Cannot read secret file named by {env_label} at {file_path}: {type(exc).__name__}"
        ) from None
    if payload.endswith(b"\r\n"):
        payload = payload[:-2]
    elif payload.endswith(b"\n") or payload.endswith(b"\r"):
        payload = payload[:-1]
    if not payload:
        return None
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError:
        env_label = source_env or "the configured secret-file variable"
        raise SecretFileError(
            f"Cannot read secret file named by {env_label} at {file_path}: UnicodeDecodeError"
        ) from None
    register_secret(value)
    return value


def read_secret_from_env(
    env_name: str,
    *,
    file_env_name: str | None = None,
) -> str | None:
    """Read a secret from ``env_name``, falling back to the file in ``file_env_name``.

    Direct environment values win so a one-off override can replace a mounted
    secret file without deleting it.
    """

    direct = os.environ.get(env_name)
    if direct is not None:
        stripped = direct.strip("\n\r")
        if stripped:
            register_secret(stripped)
            return stripped
        return None
    if file_env_name is None:
        return None
    file_path = os.environ.get(file_env_name)
    if not file_path:
        return None
    return read_secret_file(file_path, source_env=file_env_name)


def read_secret_list_from_env(
    env_name: str,
    *,
    file_env_name: str | None = None,
) -> tuple[str, ...]:
    """Read one or more secrets from a comma-separated env var or a file.

    File form is one secret per line. Blank lines are ignored. Direct env
    values may be comma-separated to allow token rotation.
    """

    direct = os.environ.get(env_name)
    if direct is not None:
        values = tuple(item.strip() for item in direct.split(",") if item.strip())
        for value in values:
            register_secret(value)
        return values
    if file_env_name is None:
        return ()
    file_path = os.environ.get(file_env_name)
    if not file_path:
        return ()
    payload = read_secret_file(file_path, source_env=file_env_name)
    if payload is None:
        return ()
    values = tuple(line.strip() for line in payload.splitlines() if line.strip())
    for value in values:
        register_secret(value)
    return values
