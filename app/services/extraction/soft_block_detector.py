"""Generic soft-block / fake-200 detection (Universal Adaptive Web
Intelligence Phase B). A response can be transport-successful (HTTP 200)
while carrying no real content: a login wall, a bot challenge, a
cookie/consent interstitial, a generic error page rendered with a 200, or a
near-empty JS shell. Deterministic keyword + structural signals only,
mirroring quality_scorer.py's approach -- no site-specific rules, no ML
(spec section 10: "generic signals... never hardcode one website").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from selectolax.lexbor import LexborHTMLParser


class SoftBlockVerdict(StrEnum):
    REAL_CONTENT = "REAL_CONTENT"
    SOFT_BLOCK = "SOFT_BLOCK"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    BOT_CHALLENGE = "BOT_CHALLENGE"
    CONSENT_WALL = "CONSENT_WALL"
    ERROR_PAGE = "ERROR_PAGE"
    THIN_SHELL = "THIN_SHELL"
    UNKNOWN = "UNKNOWN"


# Deliberately conservative -- a false positive here silently blocks real
# content from ever being stored (see the crawl_engine.py write-gate), which
# is a worse failure mode than occasionally missing a real block. Each list
# requires a strong, near-unambiguous marker phrase, not a loosely related
# word ("login" alone would false-positive on any page that merely links to
# a login page).
_CHALLENGE_MARKERS = (
    "checking your browser", "verify you are human", "verify you're human",
    "captcha", "unusual traffic from your", "are you a robot",
    "access denied", "ddos protection by", "cloudflare ray id",
    "please wait while we verify", "enable javascript and cookies to continue",
    "pardon our interruption",
)
_LOGIN_MARKERS = (
    "please log in to continue", "please sign in to continue",
    "you must be logged in to", "login required", "sign in to view this",
    "this content is only available to members", "please create an account to continue",
)
_CONSENT_MARKERS = (
    "accept all cookies", "manage your cookie preferences", "cookie consent",
)
_ERROR_MARKERS = (
    "404 not found", "page not found", "500 internal server error",
    "something went wrong", "an error has occurred", "service unavailable",
    "this page isn't working",
)

_CHALLENGE_RE = re.compile("|".join(re.escape(p) for p in _CHALLENGE_MARKERS), re.IGNORECASE)
_LOGIN_RE = re.compile("|".join(re.escape(p) for p in _LOGIN_MARKERS), re.IGNORECASE)
_CONSENT_RE = re.compile("|".join(re.escape(p) for p in _CONSENT_MARKERS), re.IGNORECASE)
_ERROR_RE = re.compile("|".join(re.escape(p) for p in _ERROR_MARKERS), re.IGNORECASE)

# Markers are near-universally in the title/head/first-screen chrome --
# bounded scan so a huge real article body can't dilute/hide a match and so
# this stays cheap on multi-hundred-KB pages.
_SCAN_CHARS = 20_000
THIN_BODY_CHARS = 80  # below this there's essentially nothing to extract, whatever the markers say
_VERY_LOW_QUALITY = 0.15


@dataclass
class SoftBlockResult:
    verdict: SoftBlockVerdict
    confidence: float
    signals: list[str] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return self.verdict not in (SoftBlockVerdict.REAL_CONTENT, SoftBlockVerdict.UNKNOWN)


def detect_soft_block(
    *,
    html: str | None,
    extracted_body: str | None,
    extraction_quality: float | None,
) -> SoftBlockResult:
    text = (html or "")[:_SCAN_CHARS]
    body_len = len(extracted_body.strip()) if extracted_body else 0

    if _CHALLENGE_RE.search(text):
        return SoftBlockResult(SoftBlockVerdict.BOT_CHALLENGE, 0.9, ["bot-challenge marker text"])

    if _has_password_input(text) or _LOGIN_RE.search(text):
        signal = "login form (password field)" if _has_password_input(text) else "login-wall marker text"
        return SoftBlockResult(SoftBlockVerdict.LOGIN_REQUIRED, 0.85, [signal])

    if _ERROR_RE.search(text) and body_len < THIN_BODY_CHARS * 3:
        return SoftBlockResult(SoftBlockVerdict.ERROR_PAGE, 0.75, ["error-page marker text"])

    if _CONSENT_RE.search(text) and body_len < THIN_BODY_CHARS:
        return SoftBlockResult(SoftBlockVerdict.CONSENT_WALL, 0.7, ["consent-wall marker with near-empty body"])

    if body_len < THIN_BODY_CHARS:
        return SoftBlockResult(SoftBlockVerdict.THIN_SHELL, 0.5, [f"extracted body only {body_len} chars"])

    # Low quality alone is NOT proof of a block -- a genuinely bad extractor
    # result on real content looks the same. Only flagged combined with a
    # still-thin body, so a long low-quality page (e.g. heavy nav chrome
    # around a short real article) isn't wrongly caught here.
    if extraction_quality is not None and extraction_quality < _VERY_LOW_QUALITY and body_len < THIN_BODY_CHARS * 2:
        return SoftBlockResult(
            SoftBlockVerdict.SOFT_BLOCK, 0.4,
            [f"extraction quality {extraction_quality:.2f} with thin body ({body_len} chars)"],
        )

    return SoftBlockResult(SoftBlockVerdict.REAL_CONTENT, 1.0, [])


def _has_password_input(html: str) -> bool:
    try:
        tree = LexborHTMLParser(html)
        return bool(tree.css_first('input[type="password"]'))
    except Exception:  # noqa: BLE001 - detection must never crash the crawl
        return False


# Page-type override applied by crawl_engine.py when a verdict is blocked --
# see page_classifier.PageType for the matching members (item 9).
PAGE_TYPE_BY_VERDICT: dict[SoftBlockVerdict, str] = {
    SoftBlockVerdict.LOGIN_REQUIRED: "LOGIN",
    SoftBlockVerdict.BOT_CHALLENGE: "CAPTCHA",
    SoftBlockVerdict.CONSENT_WALL: "CONSENT_WALL",
    SoftBlockVerdict.ERROR_PAGE: "ERROR",
    SoftBlockVerdict.THIN_SHELL: "JS_SHELL",
    SoftBlockVerdict.SOFT_BLOCK: "SOFT_BLOCK",
}


def demo() -> None:
    """ponytail self-check (no framework/fixtures) -- run with
    `python -m app.services.extraction.soft_block_detector`."""
    real = detect_soft_block(
        html="<html><body><article>" + "Real news content. " * 50 + "</article></body></html>",
        extracted_body="Real news content. " * 50, extraction_quality=0.8,
    )
    assert real.verdict == SoftBlockVerdict.REAL_CONTENT and not real.is_blocked

    captcha = detect_soft_block(
        html="<html><body>Please complete the CAPTCHA to continue. Checking your browser...</body></html>",
        extracted_body="Please complete the captcha", extraction_quality=0.3,
    )
    assert captcha.verdict == SoftBlockVerdict.BOT_CHALLENGE and captcha.is_blocked

    login = detect_soft_block(
        html='<html><body><form><input type="password"></form></body></html>',
        extracted_body="", extraction_quality=None,
    )
    assert login.verdict == SoftBlockVerdict.LOGIN_REQUIRED and login.is_blocked

    thin = detect_soft_block(html="<html><body><div id='root'></div></body></html>", extracted_body="", extraction_quality=None)
    assert thin.verdict == SoftBlockVerdict.THIN_SHELL and thin.is_blocked

    print("soft_block_detector demo: all assertions passed")


if __name__ == "__main__":
    demo()
