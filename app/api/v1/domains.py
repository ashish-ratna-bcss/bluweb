from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import APIError
from app.db.repositories.crawl_repository import CrawlRepository
from app.db.session import get_db
from app.schemas.domain import (
    BrowserCapability,
    DiscoveryCapabilities,
    DomainCapabilitiesResponse,
    DomainHealthSignals,
    DomainPolicyResponse,
    DomainProfileResponse,
    ExtractionCapability,
    FetchCapabilities,
    RoutingRecommendation,
    URLPatternSummary,
)
from app.services.crawling.domain_profile_service import compute_health_score, decide_routing
from app.services.crawling.fetch_router import FetchStrategy

router = APIRouter(prefix="/domains", tags=["domains"])


@router.get("/{domain}/profile", response_model=DomainProfileResponse)
async def get_domain_profile(
    domain: str, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> DomainProfileResponse:
    crawl_repo = CrawlRepository(db)
    stats = await crawl_repo.get_strategy_stats(domain)
    if stats is None:
        raise APIError(
            code="DOMAIN_NOT_FOUND", message=f"No crawl history for domain {domain}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    health = compute_health_score(stats, min_observations=settings.domain_learning_min_observations)
    routing = decide_routing(
        requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=settings.domain_learning_min_observations,
    )

    patterns = await crawl_repo.list_pattern_stats(domain)
    preferred_extractors = {
        p.page_type: p.preferred_extractor
        for p in patterns
        if p.page_type and p.preferred_extractor
    }
    if stats.preferred_extractor:
        preferred_extractors.setdefault("_domain_default", stats.preferred_extractor)

    return DomainProfileResponse(
        domain=stats.domain,
        health_score=health.score,
        insufficient_data=health.insufficient_data,
        signals=DomainHealthSignals(
            http_success_rate=health.signals.get("http_success_rate", 0.0),
            extraction_success_rate=health.signals.get("extraction_success_rate", 0.0),
            block_rate=health.signals.get("block_rate", 0.0),
            avg_latency_ms=health.signals.get("avg_latency_ms", 0.0),
        ),
        preferred_strategy=routing.strategy.value,
        preferred_extractors=preferred_extractors,
        statistics={
            "http_attempts": stats.http_attempts,
            "http_successes": stats.http_successes,
            "http_extraction_failures": stats.http_extraction_failures,
            "browser_attempts": stats.browser_attempts,
            "browser_successes": stats.browser_successes,
            "avg_http_latency_ms": stats.avg_http_latency_ms,
            "avg_browser_latency_ms": stats.avg_browser_latency_ms,
            "avg_content_bytes": stats.avg_content_bytes,
            "js_required_count": stats.js_required_count,
            "empty_content_count": stats.empty_content_count,
            "success_status_counts": stats.success_status_counts,
        },
        url_patterns=[
            URLPatternSummary(
                pattern=p.pattern, page_type=p.page_type, fetch_attempts=p.fetch_attempts,
                successful_fetches=p.successful_fetches, extraction_successes=p.extraction_successes,
                preferred_strategy=p.preferred_strategy, preferred_extractor=p.preferred_extractor,
            )
            for p in patterns
        ],
        politeness=DomainPolicyResponse(
            crawl_delay_ms=stats.crawl_delay_ms,
            recommended_concurrency=stats.recommended_concurrency,
            circuit_state=stats.circuit_state,
        ),
        recent_failures=stats.failure_counts,
        routing_recommendation=RoutingRecommendation(
            strategy=routing.strategy.value, reasons=routing.reasons, extractor=routing.extractor, basis=routing.basis,
        ),
    )


_CONFIDENCE_HIGH = 80.0
_CONFIDENCE_MEDIUM = 50.0


@router.get("/{domain}/capabilities", response_model=DomainCapabilitiesResponse)
async def get_domain_capabilities(
    domain: str, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> DomainCapabilitiesResponse:
    """Interpreted capability intelligence (spec Phase 9 section 6) built
    from the same `FetchStrategyStats`/`URLPatternStats` rows `/profile`
    already reads -- a second view over existing capability data, not a
    parallel intelligence system. `extraction` aggregates pattern-level rows
    up to page_type, since a domain can (and usually does) serve several
    page types under different URL patterns."""
    crawl_repo = CrawlRepository(db)
    stats = await crawl_repo.get_strategy_stats(domain)
    if stats is None:
        raise APIError(
            code="DOMAIN_NOT_FOUND", message=f"No crawl history for domain {domain}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    min_obs = settings.domain_learning_min_observations
    health = compute_health_score(stats, min_observations=min_obs)
    routing = decide_routing(requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=min_obs)
    patterns = await crawl_repo.list_pattern_stats(domain)

    by_page_type: dict[str, dict] = {}
    pagination_observations = 0
    for p in patterns:
        pagination_observations += p.pagination_detected_count
        buckets = p.page_type_buckets()
        if buckets:
            for page_type, bucket in buckets.items():
                agg = by_page_type.setdefault(
                    page_type, {"observations": 0, "successes": 0, "quality_sum": 0.0, "quality_n": 0}
                )
                agg["observations"] += int(bucket.get("extraction_attempts") or 0)
                agg["successes"] += int(bucket.get("extraction_successes") or 0)
                qn = int(bucket.get("quality_observations") or 0)
                if qn:
                    agg["quality_sum"] += float(bucket.get("avg_extraction_quality") or 0.0) * qn
                    agg["quality_n"] += qn
        elif p.page_type:
            # Legacy / single-type rows without by_page_type nesting.
            agg = by_page_type.setdefault(
                p.page_type, {"observations": 0, "successes": 0, "quality_sum": 0.0, "quality_n": 0}
            )
            agg["observations"] += p.extraction_attempts
            agg["successes"] += p.extraction_successes
            if p.quality_observations:
                agg["quality_sum"] += p.avg_extraction_quality * p.quality_observations
                agg["quality_n"] += p.quality_observations

    total_observations = stats.http_attempts + stats.browser_attempts
    if health.insufficient_data:
        confidence = "insufficient_data"
    elif health.score >= _CONFIDENCE_HIGH:
        confidence = "high"
    elif health.score >= _CONFIDENCE_MEDIUM:
        confidence = "medium"
    else:
        confidence = "low"

    compare_total = (
        stats.browser_superior_count + stats.http_superior_count + stats.browser_equivalent_count
    )
    if compare_total == 0:
        browser_comparison = "unknown"
    elif stats.browser_superior_count >= stats.http_superior_count and stats.browser_superior_count >= stats.browser_equivalent_count:
        browser_comparison = "browser_superior"
    elif stats.http_superior_count >= stats.browser_equivalent_count:
        browser_comparison = "http_superior"
    else:
        browser_comparison = "equivalent"

    return DomainCapabilitiesResponse(
        domain=domain,
        observations=total_observations,
        confidence=confidence,
        discovery=DiscoveryCapabilities(
            sitemap=stats.sitemap_status, feed=stats.feed_status,
            sitemap_url_count=stats.sitemap_url_count, feed_url_count=stats.feed_url_count,
            pagination_detected=pagination_observations > 0, pagination_observations=pagination_observations,
        ),
        fetch=FetchCapabilities(
            http_success_rate=round(stats.http_successes / stats.http_attempts, 4) if stats.http_attempts else 0.0,
            browser_success_rate=round(stats.browser_successes / stats.browser_attempts, 4) if stats.browser_attempts else 0.0,
            browser_required_rate=round(stats.js_required_count / stats.http_attempts, 4) if stats.http_attempts else 0.0,
            browser_superior_rate=round(stats.browser_superior_count / compare_total, 4) if compare_total else None,
            avg_completeness=round(stats.avg_completeness, 4) if stats.completeness_observations else None,
            index_children_discovered_total=stats.index_children_discovered_total,
        ),
        browser=BrowserCapability(
            superior_count=stats.browser_superior_count,
            http_superior_count=stats.http_superior_count,
            equivalent_count=stats.browser_equivalent_count,
            comparison=browser_comparison,
        ),
        extraction=[
            ExtractionCapability(
                page_type=page_type,
                observations=agg["observations"],
                success_rate=round(agg["successes"] / agg["observations"], 4) if agg["observations"] else 0.0,
                avg_quality=round(agg["quality_sum"] / agg["quality_n"], 4) if agg["quality_n"] else None,
            )
            for page_type, agg in sorted(by_page_type.items())
        ],
        recommendation=RoutingRecommendation(
            strategy=routing.strategy.value, reasons=routing.reasons, extractor=routing.extractor, basis=routing.basis,
        ),
        last_observed_at=stats.last_observed_at,
    )
