"""URL normalization for identity/dedup purposes.

ponytail: phase-1 scope is just enough to give preflight reports and
sources a stable identity key (lowercase scheme+host, default port and
fragment stripped). Full dedup rules (tracking-parameter stripping,
canonical-URL preference, encoding normalization) belong to the
deduplication service in a later phase -- query strings are left alone
here since some sites use them as real content identifiers.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import tldextract

_DEFAULT_PORTS = {"http": 80, "https": 443}

# tldextract ships/caches its own Public Suffix List snapshot -- offline,
# no network fetch on first use, safe in a sandboxed environment.
_tld_extract = tldextract.TLDExtract(suffix_list_urls=())


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

    return urlunsplit((scheme, netloc, path, parts.query, ""))


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
