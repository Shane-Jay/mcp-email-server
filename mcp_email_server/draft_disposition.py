"""What to do with a draft after send_draft reports a clean send."""

from __future__ import annotations

import os
from typing import Literal

from mcp_email_server.application.mutations import SentCopyMutationOutcome

SendDraftDisposition = Literal["safe", "delete", "move", "keep"]
_ENV = "MCP_EMAIL_SERVER_SEND_DRAFT"
_VALID: tuple[SendDraftDisposition, ...] = ("safe", "delete", "move", "keep")


def configured_send_draft_disposition() -> SendDraftDisposition:
    """Return the post-send draft policy. Default ``safe``.

    * ``safe`` (default): delete the draft only when a Sent copy was APPENDed
      by this server (or the operator has proof the provider auto-saves).
    * ``delete``: always delete the draft after a clean send.
    * ``move``: move the draft into Sent when this server did not already
      save a copy; if a Sent copy succeeded, delete the draft instead.
    * ``keep``: leave the draft in place.
    """

    raw = os.environ.get(_ENV)
    if raw is None or not raw.strip():
        return "safe"
    value = raw.strip().lower()
    if value not in _VALID:
        raise ValueError(f"{_ENV} must be one of {', '.join(_VALID)}")
    return value  # type: ignore[return-value]


def sent_copy_is_saved(sent_copy: SentCopyMutationOutcome) -> bool:
    """True when this process has evidence the message lives in Sent."""

    return sent_copy.status == "succeeded"


def draft_action_after_send(
    disposition: SendDraftDisposition,
    sent_copy: SentCopyMutationOutcome,
) -> Literal["delete", "move", "keep"]:
    """Decide delete / move / keep after a clean send."""

    if disposition == "keep":
        return "keep"
    if disposition == "delete":
        return "delete"
    if disposition == "move":
        return "delete" if sent_copy_is_saved(sent_copy) else "move"
    return "delete" if sent_copy_is_saved(sent_copy) else "keep"
