import os
import re
from collections import deque
from traceback import walk_tb
from typing import Any

USER_DEFINED_LOG_LEVEL = os.getenv("MCP_EMAIL_SERVER_LOG_LEVEL", "INFO")

os.environ["LOGURU_LEVEL"] = USER_DEFINED_LOG_LEVEL

from loguru import logger as _raw_logger  # noqa: E402

__all__ = [
    "log_provider_failure",
    "log_safe_failure",
    "logger",
    "register_secret",
    "sanitize_log_text",
    "tracebacks_enabled",
]

_SECRET_ENV_NAME = re.compile(
    r"(PASSWORD|PASSWD|TOKEN|SECRET|CACHE_KEY|BEARER|AUTHORIZATION|API[_-]?KEY|CREDENTIAL)",
    re.IGNORECASE,
)
_MIN_HEURISTIC_SECRET_LEN = 8
_MIN_REGISTERED_SECRET_LEN = 2
_REDACTED = "[redacted]"
_TRACEBACK_ENV = "MCP_EMAIL_SERVER_LOG_TRACEBACKS"
_FALSE_VALUES = {"0", "false", "no", "off", ""}

# IMAP LOGIN user pass, or SMTP/IMAP AUTH[ENTICATE] <mech> [initial-response].
# Do not match prose such as "SMTP phase=authenticate outcome=...".
_LOGIN_ARGS = re.compile(
    r"(?i)\b(LOGIN)\s+\S+\s+\S+"
    r"|\b(AUTH(?:ENTICATE)?)\s+(?:PLAIN|LOGIN|XOAUTH2|OAUTHBEARER|CRAM-MD5)(?:\s+\S+)?"
)
_AUTH_HEADER = re.compile(r"(?i)(?:^|[\s,;])authorization\s*[:=]\s*(?:bearer\s+)?\S+")
_SASL_MECH = re.compile(r"(?i)\b(?:PLAIN|LOGIN|XOAUTH2|OAUTHBEARER)\s+[A-Za-z0-9+/_-]{12,}={0,2}")
_B64_BLOB = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")
_BODY_BLOCK = re.compile(r"(\r?\n){2,}[\s\S]{80,}")

_registered_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember an exact configured secret so logs redact it regardless of length.

    Values shorter than two characters are ignored so a one-character password
    cannot blank out every log line. Heuristic env scanning still requires
    eight characters so tokens like ``true`` are not stripped.
    """

    if not value:
        return
    stripped = value.strip()
    if len(stripped) < _MIN_REGISTERED_SECRET_LEN or set(stripped) <= {"*"}:
        return
    _registered_secrets.add(stripped)


def tracebacks_enabled() -> bool:
    """Return whether full exception tracebacks may be attached to logs.

    Off by default. ``MCP_EMAIL_SERVER_LOG_TRACEBACKS=true`` attaches Loguru
    exception info, which can include frame locals (passwords, message bodies).
    Use only on an isolated debugging session.
    """

    raw = os.environ.get(_TRACEBACK_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in _FALSE_VALUES


def _heuristic_env_secrets() -> tuple[str, ...]:
    values: list[str] = []
    for name, raw in os.environ.items():
        if not _SECRET_ENV_NAME.search(name) or not raw:
            continue
        for item in raw.replace("\n", ",").split(","):
            item = item.strip()
            if len(item) >= _MIN_HEURISTIC_SECRET_LEN:
                values.append(item)
    return tuple(values)


def _all_secrets() -> tuple[str, ...]:
    values = set(_registered_secrets)
    values.update(_heuristic_env_secrets())
    return tuple(sorted(values, key=len, reverse=True))


def sanitize_log_text(message: str) -> str:
    """Strip configured secrets, AUTH/LOGIN args, SASL blobs, and bodies."""

    text = message
    for secret in _all_secrets():
        if secret in text:
            text = text.replace(secret, _REDACTED)
    text = _AUTH_HEADER.sub(" Authorization: [redacted]", text)
    text = _LOGIN_ARGS.sub(
        lambda match: f"{match.group(1) or match.group(2)} [redacted]",
        text,
    )
    text = _SASL_MECH.sub("[redacted-sasl]", text)
    text = _B64_BLOB.sub("[redacted-b64]", text)
    return _BODY_BLOCK.sub("\n\n[redacted-body]", text)


def _redact_record(record: Any) -> None:
    record["message"] = sanitize_log_text(str(record["message"]))


logger = _raw_logger.patch(_redact_record)


def log_safe_failure(message: str, exc: BaseException) -> None:
    """Log a fixed message, exception type, and at most 20 value-free frames.

    Never attach the exception to Loguru: diagnostic sinks can expose credentials
    in frame locals, and exception messages/chains can contain provider data.
    Function names and line numbers retain useful failure locations without
    source lines, filesystem paths, locals, or exception text.
    """
    frames = deque(
        (f"{frame.f_code.co_name}:{line}" for frame, line in walk_tb(exc.__traceback__)),
        maxlen=20,
    )
    logger.opt(exception=False).warning(
        "{} ({}); Traceback: {}", message, type(exc).__name__, " -> ".join(frames) or "unavailable"
    )


def log_provider_failure(message: str, exc: BaseException) -> None:
    """Log IMAP/SMTP (and similar) failures with a sanitized exception message.

    Default: exception type + sanitized text, no Python traceback. Set
    ``MCP_EMAIL_SERVER_LOG_TRACEBACKS=true`` only when you accept that frame
    locals may contain passwords or message bodies.
    """

    sanitized = sanitize_log_text(str(exc))
    if tracebacks_enabled():
        logger.opt(exception=exc).error("{} ({}); {}", message, type(exc).__name__, sanitized)
        return
    logger.opt(exception=False).error("{} ({}); {}", message, type(exc).__name__, sanitized)
