import os
import re
from collections import deque
from traceback import walk_tb
from typing import Any

USER_DEFINED_LOG_LEVEL = os.getenv("MCP_EMAIL_SERVER_LOG_LEVEL", "INFO")

os.environ["LOGURU_LEVEL"] = USER_DEFINED_LOG_LEVEL

from loguru import logger as _raw_logger  # noqa: E402

__all__ = ["log_safe_failure", "logger"]

_SECRET_ENV_NAME = re.compile(
    r"(PASSWORD|PASSWD|TOKEN|SECRET|CACHE_KEY|BEARER|AUTHORIZATION|API[_-]?KEY|CREDENTIAL)",
    re.IGNORECASE,
)
_MIN_SECRET_LEN = 8
_REDACTED = "[redacted]"


def _secret_fragments() -> tuple[str, ...]:
    """Collect env secret values long enough to redact without matching 'true'/'false'."""

    values: list[str] = []
    for name, raw in os.environ.items():
        if not _SECRET_ENV_NAME.search(name) or not raw:
            continue
        for item in raw.replace("\n", ",").split(","):
            item = item.strip()
            if len(item) >= _MIN_SECRET_LEN:
                values.append(item)
    values.sort(key=len, reverse=True)
    return tuple(values)


def _redact(message: str) -> str:
    for secret in _secret_fragments():
        if secret in message:
            message = message.replace(secret, _REDACTED)
    return message


def _redact_record(record: Any) -> None:
    record["message"] = _redact(str(record["message"]))


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
