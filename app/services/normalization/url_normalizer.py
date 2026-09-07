"""URL normalization for identity/dedup purposes.

ponytail: phase-1 scope is just enough to give preflight reports and
sources a stable identity key (lowercase scheme+host, default port and
fragment stripped, tracking-parameter stripping for frontier identity).
Canonical-URL preference and encoding normalization still belong to the
deduplication service in a later phase -- content-significant query
params (id, page, q, etc.) are preserved.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Exact-name tracking params (case-insensitive). Also drop any name
# starting with ``utm_``. Content-significant params (id, page, q, p, sort)
# are intentionally not listed.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_reader",
        "gclid",
        "gbraid",
        "wbraid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "igshid",
        "mno",
        "ref",
        "source",
        "campaign",
    }
)

# tldextract ships/caches its own Public Suffix List snapshot -- offline,
# no network fetch on first use, safe in a sandboxed environment.
_tld_extract = tldextract.TLDExtract(suffix_list_urls=())


def _is_tracking_param(name: str) -> bool:
    lower = name.lower()
    if lower.startswith("utm_"):
        return True
    return lower in _TRACKING_PARAMS


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()

    port = parts.port
    netloc = hostname
    if port and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{hostname}:{port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking_param(k)]
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))


def canonicalize_for_frontier(url: str) -> str:
    """normalize_url + identity for RequestQueue dedup."""
    return normalize_url(url)


def extract_domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def registrable_domain(url: str) -> str:
    """The eTLD+1 (e.g. "craigslist.org" for both "sfbay.craigslist.org" and
    "www.craigslist.org") -- Public-Suffix-List aware via `tldextract`, so
    "example.co.uk" isn't mistaken for the two-label "co.uk". Used ONLY for
    same-site crawl-scope decisions (Phase 8.1 real bug fix: Craigslist's
    own search-results items link to a different subdomain than the search
    page, which an exact-hostname `same_domain_only` comparison wrongly
    treated as "leaving the site" and silently dropped every listing).

    Deliberately NOT used for the SSRF/security layer or for
    `FetchStrategyStats`/`URLPatternStats` domain keys -- those need the
    exact hostname (DNS-pinning and per-subdomain learning granularity are
    both intentional there, see `URLSecurityService` and
    `crawl_engine.py::_process_page`'s own `domain = extract_domain(url)`).
    """
    result = _tld_extract(url)
    return result.top_domain_under_public_suffix.lower() or extract_domain(url)
