"""
src/core/llm_errors.py - Recognize Claude API failures that make further calls pointless.

Credits running out or the account's usage limit being reached should stop a
run immediately with a clear message, instead of being fed back to the agent
as if it were model output.
"""

from typing import Any, Optional

CREDITS_EXHAUSTED = "credits_exhausted"
AUTH_ERROR = "auth_error"

# Billing problems arrive as 402 billing_error, but low-balance and usage-limit
# errors have also been returned as 400 invalid_request_error with these phrases.
_CREDIT_PHRASES = (
    "credit balance",
    "usage limit",
    "spend limit",
    "spending limit",
    "billing",
    "insufficient credit",
    "out of credits",
)

USER_MESSAGES = {
    CREDITS_EXHAUSTED: (
        "The app has run out of Anthropic API credits (or reached its usage limit), so the agent can't make "
        "any more calls right now. Please try again later."
    ),
    AUTH_ERROR: "The server's Anthropic API key was rejected, so the agent can't make any calls. The app owner needs to fix the key.",
}


class LLMUnavailableError(RuntimeError):
    """The Claude API refused service in a way retrying won't fix."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{USER_MESSAGES[kind]} ({detail})")
        self.kind = kind
        self.user_message = USER_MESSAGES[kind]
        self.detail = detail


def _status_and_type(exc: Any) -> tuple:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    body = getattr(exc, "body", None)
    error_type = None
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            error_type = error.get("type")
    return status, error_type


def classify_llm_error(exc: BaseException) -> Optional[str]:
    """CREDITS_EXHAUSTED, AUTH_ERROR, or None for errors that may be transient."""
    seen = set()
    while exc is not None and id(exc) not in seen:  # also look through wrapped causes
        seen.add(id(exc))
        status, error_type = _status_and_type(exc)
        text = str(exc).lower()
        if status == 402 or error_type == "billing_error" or any(phrase in text for phrase in _CREDIT_PHRASES):
            return CREDITS_EXHAUSTED
        if status in (401, 403) or error_type in ("authentication_error", "permission_error"):
            return AUTH_ERROR
        exc = exc.__cause__ or exc.__context__
    return None
