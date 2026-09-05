from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DomainHealthSignals(BaseModel):
    http_success_rate: float
    extraction_success_rate: float
    block_rate: float
    avg_latency_ms: float


class DomainPolicyResponse(BaseModel):
    crawl_delay_ms: float
    recommended_concurrency: int
    circuit_state: str


class URLPatternSummary(BaseModel):
    pattern: str
    page_type: str | None
    fetch_attempts: int
    successful_fetches: int
    extraction_successes: int
    preferred_strategy: str | None
    preferred_extractor: str | None


class RoutingRecommendation(BaseModel):
    strategy: str
    reasons: list[str]
    extractor: str | None
    basis: str = "default"


class DomainProfileResponse(BaseModel):
    domain: str
    health_score: float
    insufficient_data: bool
    signals: DomainHealthSignals
    preferred_strategy: str
    preferred_extractors: dict[str, str]
    statistics: dict
    url_patterns: list[URLPatternSummary]
    politeness: DomainPolicyResponse
    recent_failures: dict[str, int]
    routing_recommendation: RoutingRecommendation


class DiscoveryCapabilities(BaseModel):
    sitemap: str  # DiscoveryStatus value: available|not_present|failed|blocked|invalid|unknown
    feed: str
    sitemap_url_count: int
    feed_url_count: int
    pagination_detected: bool
    pagination_observations: int


class FetchCapabilities(BaseModel):
    http_success_rate: float
    browser_success_rate: float
    browser_required_rate: float
    browser_superior_rate: float | None = None
    avg_completeness: float | None = None
    index_children_discovered_total: int = 0


class ExtractionCapability(BaseModel):
    page_type: str
    observations: int
    success_rate: float
    avg_quality: float | None  # None, not 0.0, when no quality observation exists yet -- don't fabricate a score


class BrowserCapability(BaseModel):
    superior_count: int
    http_superior_count: int
    equivalent_count: int
    comparison: str  # "browser_superior" | "http_superior" | "equivalent" | "unknown"


class DomainCapabilitiesResponse(BaseModel):
    """GET /domains/{domain}/capabilities: interpreted capability intelligence."""

    domain: str
    observations: int
    confidence: str  # "insufficient_data" | "low" | "medium" | "high"
    discovery: DiscoveryCapabilities
    fetch: FetchCapabilities
    browser: BrowserCapability
    extraction: list[ExtractionCapability]
    recommendation: RoutingRecommendation
    last_observed_at: datetime | None
    # Deterministic meaning of any percentage fields on this response:
    # rates are observation ratios (successes/attempts), never "scrape coverage".
    score_semantics: str = (
        "Rates are successes/attempts from observed crawls. "
        "avg_quality is mean score_extraction() 0-1. "
        "avg_completeness is mean score_completeness() 0-1. "
        "Not claimed scrape coverage."
    )