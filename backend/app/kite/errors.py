"""Kite authentication-failure classification shared by REST and ticker paths."""

from __future__ import annotations

from typing import Any

# Phrases that specifically indicate the access token/session itself is invalid or
# expired (as opposed to a permission, input, or upstream error that also happens to
# carry an HTTP 401/403). Kept narrow on purpose: a false positive here quarantines a
# still-valid token and hides a different failure as "token expired".
_TOKEN_TEXT_MARKERS = (
    "access token",
    "api_key or access_token",
    "incorrect api_key or access_token",
    "invalid access token",
    "invalid api credentials",
    "invalid session",
    "invalid token",
    "session expired",
    "token expired",
    "token has expired",
    "token is invalid",
    "tokenexception",
)

# Kite SDK exception types that legitimately default to a 401/403-style ``code`` but
# do NOT mean the access token is invalid (e.g. an entitlement/permission failure on
# an otherwise valid session). Their presence overrides a bare status-code match.
_NON_TOKEN_KITE_EXCEPTION_MARKERS = (
    "dataexception",
    "generalexception",
    "inputexception",
    "networkexception",
    "orderexception",
    "permissionexception",
)


class KiteAuthenticationError(Exception):
    """Raised when the active Kite access token is no longer usable."""


def is_authentication_error(
    error: BaseException | None = None,
    *,
    code: Any = None,
    reason: Any = None,
) -> bool:
    """Conservatively identify invalid/expired Kite session failures.

    KiteTicker reports callback ``code``/``reason`` values while REST clients expose
    either Kite ``TokenException`` objects or HTTP response status codes. This helper
    accepts both forms without depending on one SDK exception implementation, and
    deliberately avoids generic "authentication"/"authorization"/"forbidden" text or a
    bare HTTP 403 as sufficient signals, since Kite's own ``PermissionException`` and
    other non-token errors also default to 403.
    """
    values = [reason]
    if error is not None:
        if isinstance(error, KiteAuthenticationError):
            return True
        error_type = type(error).__name__.lower()
        if error_type == "tokenexception":
            return True
        if code is None:
            code = getattr(error, "code", None)
        response = getattr(error, "response", None)
        if code is None and response is not None:
            code = getattr(response, "status_code", None)
        values.extend((error_type, str(error)))

    text = " ".join(str(value).lower() for value in values if value is not None)
    text = text.replace("_", " ").replace("-", " ")

    if any(marker.replace("_", " ") in text for marker in _NON_TOKEN_KITE_EXCEPTION_MARKERS):
        return False
    if any(marker.replace("_", " ") in text for marker in _TOKEN_TEXT_MARKERS):
        return True

    try:
        status = int(code)
    except (TypeError, ValueError):
        return False
    # 401 Unauthorized is unambiguous. A bare 403 is not (PermissionException also
    # defaults to it), so it is only treated as an auth failure when nothing above
    # positively identified it as a different, non-token Kite exception.
    return status == 401 or status == 403
