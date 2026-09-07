"""Normalizes fetch outcomes into failure categories (spec Phase L) so
downstream logic treats "the page is genuinely gone" differently from
"we got rate-limited" or "the network hiccuped" -- these need different
responses (REMOVED vs back off vs just retry), and conflating them was a
real bug: the phase-3 REMOVED detector counted a 429 the same as a 404.
"""

from __future__ import annotations

from enum import StrEnum


class FailureCategory(StrEnum):
    DNS_FAILURE = "dns_failure"
    TLS_FAILURE = "tls_failure"
    TIMEOUT = "timeout"
    CONNECTION_FAILURE = "connection_failure"
    ROBOTS_BLOCKED = "robots_blocked"
    HTTP_403 = "http_403"
    HTTP_404 = "http_404"
    HTTP_410 = "http_410"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    OTHER_HTTP_ERROR = "other_http_error"
    EMPTY_CONTENT = "empty_content"
    EXTRACTION_FAILURE = "extraction_failure"
    # Phase 9 additions (spec section 39) -- discovery/quality-specific
    # categories, observability-only: none of these participate in
    # REMOVED-threshold logic (is_removal_eligible below is unchanged),
    # so adding them cannot regress Phase 3/7's failure-counting behavior.
    JAVASCRIPT_REQUIRED = "javascript_required"
    LOW_EXTRACTION_QUALITY = "low_extraction_quality"
    DISCOVERY_FAILURE = "discovery_failure"
    SITEMAP_FAILURE = "sitemap_failure"
    FEED_FAILURE = "feed_failure"
    # Universal Adaptive Web Intelligence Phase B: a transport-successful
    # (HTTP 200) response whose body is a login/captcha/consent/error
    # interstitial rather than real content -- see soft_block_detector.py.
    # Reuses the existing failure_counts/circuit-breaker machinery instead
    # of a bespoke counter (ponytail).
    SOFT_BLOCKED = "soft_blocked"
    NONE = "none"  # success


# A failure in this set means "the site is actively pushing back" -- the
# page probably still exists, we're just not currently allowed to see it.
# Never lets REMOVED-detection fire, no matter how many times it repeats:
# unlike a 404, "blocked" carries no information that the content is gone.
BLOCKED_OR_THROTTLED = frozenset({
    FailureCategory.HTTP_403,
    FailureCategory.HTTP_429,
    FailureCategory.ROBOTS_BLOCKED,
    FailureCategory.SOFT_BLOCKED,
})


def is_removal_eligible(category: FailureCategory) -> bool:
    """Everything except a block/throttle (and success) counts toward the
    consecutive-failure REMOVED threshold -- including repeated timeouts
    and 5xx, which a single occurrence should never flip to REMOVED but a
    sustained run of genuinely might mean the site's gone. The
    consecutive-count threshold (not the category alone) is what protects
    against a one-off blip, per spec section 76 -- see `SourceRepository`."""
    return category not in BLOCKED_OR_THROTTLED and category != FailureCategory.NONE


def classify_http_status(status_code: int) -> FailureCategory:
    if status_code == 403:
        return FailureCategory.HTTP_403
    if status_code == 404:
        return FailureCategory.HTTP_404
    if status_code == 410:
        return FailureCategory.HTTP_410
    if status_code == 429:
        return FailureCategory.HTTP_429
    if 500 <= status_code < 600:
        return FailureCategory.HTTP_5XX
    if status_code >= 400:
        return FailureCategory.OTHER_HTTP_ERROR
    return FailureCategory.NONE


def classify_transport_error(error_message: str) -> FailureCategory:
    """Best-effort classification from the exception text our fetchers
    already capture (`PageFetchResult.error`) -- httpx/dns exception class
    names are stable enough to pattern-match without re-plumbing exception
    types through every call site."""
    lowered = error_message.lower()
    if "dns" in lowered or "nodename" in lowered or "name or service not known" in lowered:
        return FailureCategory.DNS_FAILURE
    if "ssl" in lowered or "certificate" in lowered or "tls" in lowered:
        return FailureCategory.TLS_FAILURE
    if "timeout" in lowered or "timed out" in lowered:
        return FailureCategory.TIMEOUT
    if "connect" in lowered or "refused" in lowered or "reset" in lowered or "unreachable" in lowered:
        return FailureCategory.CONNECTION_FAILURE
    return FailureCategory.CONNECTION_FAILURE
