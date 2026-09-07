"""Map URLSecurityError → APIError with safe, user-facing messages.

Raw DNS / SSRF internals stay in logs / exception `__cause__`; the API
envelope only exposes stable codes + short copy (WI-10 / WI-11).
"""

from __future__ import annotations

from fastapi import status

from app.core.errors import APIError
from app.services.security.url_security import URLSecurityError

_USER_MESSAGES = {
    "URL_INVALID": "Enter a valid http(s) URL.",
    "DNS_RESOLUTION_FAILED": "Could not resolve that hostname.",
    "URL_BLOCKED": "That URL is not allowed.",
}


def url_security_to_api_error(exc: URLSecurityError) -> APIError:
    code = getattr(exc, "code", None) or "URL_BLOCKED"
    if code not in _USER_MESSAGES:
        code = "URL_BLOCKED"
    return APIError(
        code=code,
        message=_USER_MESSAGES[code],
        status_code=status.HTTP_400_BAD_REQUEST,
        details={"reason": str(exc)},
    )
