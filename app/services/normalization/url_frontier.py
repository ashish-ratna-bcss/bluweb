"""Frontier quality: sink-URL exclusion for auth/cart/account noise.

Generic path-segment patterns only — never site-specific. Soft-block
detection still runs after fetch; this avoids spending crawl budget on
URLs that almost never yield public content.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Path segment patterns (whole segment match, case-insensitive)
_SINK_SEGMENTS = frozenset(
    {
        "login",
        "logout",
        "signin",
        "sign-in",
        "signout",
        "sign-out",
        "register",
        "signup",
        "sign-up",
        "password",
        "forgot-password",
        "reset-password",
        "account",
        "accounts",
        "cart",
        "checkout",
        "wishlist",
        "my-account",
        "auth",
        "oauth",
        "sso",
    }
)

_SINK_PATH_RE = re.compile(
    r"/(?:login|logout|signin|sign-?in|signout|sign-?out|register|signup|sign-?up|"
    r"password|forgot-?password|reset-?password|account|cart|checkout|wishlist|"
    r"my-?account|auth|oauth|sso)(?:/|$|\?)",
    re.IGNORECASE,
)


def is_sink_url(url: str) -> bool:
    """True if URL path looks like auth/cart/account sink (not content)."""
    if not url or not str(url).strip():
        return False
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return False

    path = parts.path or ""
    if not path:
        return False

    for segment in path.split("/"):
        if segment and segment.lower() in _SINK_SEGMENTS:
            return True

    haystack = path
    if parts.query:
        haystack = f"{path}?{parts.query}"
    return bool(_SINK_PATH_RE.search(haystack))


def sink_reason(url: str) -> str | None:
    """Short reason string if sink, else None."""
    if not url or not str(url).strip():
        return None
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return None

    path = parts.path or ""
    for segment in path.split("/"):
        if segment and segment.lower() in _SINK_SEGMENTS:
            return f"sink_segment:{segment.lower()}"

    haystack = path
    if parts.query:
        haystack = f"{path}?{parts.query}"
    if _SINK_PATH_RE.search(haystack):
        return "sink_path_pattern"
    return None
