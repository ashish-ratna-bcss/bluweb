"""Internal evidence dataclasses produced by each preflight check module.

These are intentionally separate from the API response schemas
(app/schemas/preflight.py) -- this module is the raw evidence the scoring
step consumes; the schema module is the shaped, documented API contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecommendedFetchStrategy(StrEnum):
    HTTP = "http"
    BROWSER = "browser"


class DiscoveryStatus(StrEnum):
    """Finer-grained outcome for a discovery probe (spec Phase 9 section 7)
    than a bare `available: bool` -- "the site has no sitemap" and "the
    site's sitemap 403'd us" both used to collapse to `available=False`,
    but they mean opposite things for capability learning: one is a fact
    about the site, the other is a fact about US being blocked right now.
    Additive alongside the existing `available` bool fields below, not a
    replacement -- every existing `.available` check keeps working."""

    AVAILABLE = "available"
    NOT_PRESENT = "not_present"  # every candidate URL came back 404/absent
    FAILED = "failed"  # network/timeout/DNS error on at least one candidate, nothing else succeeded
    BLOCKED = "blocked"  # a candidate came back 403/429 -- may well exist, we're just not allowed to see it right now
    INVALID = "invalid"  # a candidate was fetched but wasn't parseable as the expected format
    UNKNOWN = "unknown"


@dataclass
class DNSCheckResult:
    resolves: bool
    ipv4_addresses: list[str] = field(default_factory=list)
    ipv6_addresses: list[str] = field(default_factory=list)
    resolution_time_ms: float | None = None
    error: str | None = None


@dataclass
class RedirectHop:
    url: str
    status_code: int


@dataclass
class HTTPCheckResult:
    reachable: bool
    status_code: int | None = None
    final_url: str | None = None
    redirect_chain: list[RedirectHop] = field(default_factory=list)
    response_time_ms: float | None = None
    content_type: str | None = None
    content_length: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None


@dataclass
class TLSCheckResult:
    used_https: bool
    valid_certificate: bool | None = None
    error: str | None = None


@dataclass
class RobotsCheckResult:
    exists: bool
    fetch_allowed: bool = True
    sitemap_urls: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SitemapCheckResult:
    available: bool
    sitemap_url: str | None = None
    is_index: bool = False
    estimated_url_count: int | None = None
    sample_urls: list[str] = field(default_factory=list)
    error: str | None = None
    status: DiscoveryStatus = DiscoveryStatus.UNKNOWN


@dataclass
class FeedCheckResult:
    available: bool
    feed_type: str | None = None  # "rss" | "atom"
    feed_url: str | None = None
    sample_item_count: int | None = None
    error: str | None = None
    status: DiscoveryStatus = DiscoveryStatus.UNKNOWN


@dataclass
class HTMLAnalysisResult:
    title: str | None = None
    meaningful_text_length: int = 0
    total_html_length: int = 0
    script_bytes: int = 0
    external_script_count: int = 0
    heading_count: int = 0
    has_json_ld: bool = False
    has_open_graph: bool = False
    canonical_url: str | None = None
    internal_links: list[str] = field(default_factory=list)
    image_count: int = 0
    document_links: list[str] = field(default_factory=list)  # pdf/doc/etc
    iframe_count: int = 0


@dataclass
class JavaScriptAssessment:
    likely_requires_browser: bool
    script_to_text_ratio: float
    empty_content_containers_detected: bool
    reasoning: str
    hydration_markers_detected: bool = False
    framework_assets_detected: bool = False
    signal_score: int = 0
    signals: list[str] = field(default_factory=list)


@dataclass
class ExtractionAttempt:
    url: str
    fetched: bool
    extracted: bool
    title_found: bool = False
    author_found: bool = False
    date_found: bool = False
    body_chars: int = 0
    used_browser: bool = False
    error: str | None = None


@dataclass
class BrowserFallbackResult:
    attempted: bool
    succeeded: bool = False
    rendered_content_chars: int = 0
    extraction_improved: bool = False
    render_duration_ms: float | None = None
    error: str | None = None


@dataclass
class CapabilityScore:
    score: int
    confidence: ConfidenceLevel
    discovery_score: int
    fetch_score: int
    extraction_score: int
    rendering_score: int
    content_type_score: int


@dataclass
class DiscoveryInfo:
    sitemap: bool
    rss: bool
    atom: bool
    html_links: bool
    estimated_discoverable_urls: int | None


@dataclass
class FetchInfo:
    http: bool
    browser: bool
    recommended: RecommendedFetchStrategy


@dataclass
class ContentInfo:
    html: bool
    pdf: bool
    json: bool
    xml: bool
    images: bool


@dataclass
class ExtractionConfidence:
    title: float
    author: float
    date: float
    body: float


@dataclass
class SampleInfo:
    tested: int
    fetched: int
    extractable: int


@dataclass
class PreflightReport:
    url: str
    final_url: str | None
    status: str  # "completed" | "failed"
    capability: CapabilityScore
    discovery: DiscoveryInfo
    fetch: FetchInfo
    content: ContentInfo
    extraction: ExtractionConfidence
    sample: SampleInfo
    limitations: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str | None = None
