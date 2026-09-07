"""Orchestrates one instant-crawl run: pop URLs from a bounded frontier,
fetch (HTTP first, browser on JS-dependency or domain-history signal),
extract, dedupe, version, store, and enqueue same-domain links -- all
within max_pages/max_depth/max_response_bytes limits.

Same pipeline instant crawl and future monitoring crawls share (per spec
section 3): this function takes a crawl_job_id and doesn't care whether
that job was created by the instant-crawl API or (later) a scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from crawlee import Request
from crawlee.storages import RequestQueue
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.repositories.crawl_repository import CrawlRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.source_repository import SourceRepository
from app.db.session import AsyncSessionLocal
from app.core.metrics import http_403_total, http_429_total
from app.services.classification.url_pattern import normalize_url_pattern
from app.services.crawling.domain_policy_service import PolicyState, should_allow_request
from app.services.crawling.domain_profile_service import decide_routing
from app.services.crawling.fetch_router import FetchStrategy
from app.services.crawling.fetchers import PageFetchResult, browser_fetch_page, http_fetch_page
from app.services.crawling.failure_classification import (
    FailureCategory,
    classify_http_status,
    classify_transport_error,
)
from app.services.deduplication.dedup import exact_hash, normalized_hash, simhash64
from app.services.discovery.pagination import detect_pagination
from app.services.events import Event, default_bus
from app.services.extraction.completeness_scorer import score_completeness
from app.services.extraction.extraction_router import INDEX_TYPES, PageExtractionResult, extract_for_page
from app.services.extraction.provenance import build_provenance
from app.services.extraction.soft_block_detector import PAGE_TYPE_BY_VERDICT, SoftBlockResult, detect_soft_block
from app.services.intelligence.story_service import process_document_intelligence
from app.services.monitoring.change_detection import detect_change
from app.services.monitoring.fingerprints import from_signed_int64, to_signed_int64
from app.services.monitoring.models import DocumentSnapshot
from app.services.normalization.url_normalizer import extract_domain, normalize_url, registrable_domain
from app.services.preflight.html import analyze_html
from app.services.preflight.javascript import assess_javascript_dependency
from app.services.preflight.models import HTMLAnalysisResult
from app.services.storage.artifact_store import ArtifactStore
from app.services.security.url_security import URLSecurityService

logger = logging.getLogger("webintel.crawl_engine")

_MAX_CHILD_URLS_PER_INDEX_PAGE = 50  # bounded -- one index page can't blow the frontier open
# Index-like page types that may need scroll/load-more on browser fetch.
_SCROLL_PAGE_TYPES = {t.value for t in INDEX_TYPES} | {"HOME", "UNKNOWN"}

_RQ_RETRY_ATTEMPTS = 3
_RQ_RETRY_DELAY_SECONDS = 0.1


async def _rq_call(func, *args, **kwargs):
    """Crawlee's file-based RequestQueue storage has no built-in retry: a
    concurrent writer to the same on-disk queue (crawl_default_concurrency
    in-flight tasks all touching one queue) can transiently see
    FileNotFoundError on a request/metadata file another task is mid-write
    on (item 4). Short bounded retry, not a queue reimplementation --
    ponytail: this is the actual bug (a missing retry around an already-
    correct storage client), not a reason to swap storage backends."""
    for attempt in range(_RQ_RETRY_ATTEMPTS):
        try:
            return await func(*args, **kwargs)
        except FileNotFoundError:
            if attempt == _RQ_RETRY_ATTEMPTS - 1:
                raise
            await asyncio.sleep(_RQ_RETRY_DELAY_SECONDS)


@dataclass
class RunStats:
    pages_discovered: int = 0
    pages_attempted: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    pages_extracted: int = 0
    new_documents: int = 0
    updated_documents: int = 0
    unchanged_documents: int = 0
    duplicate_documents: int = 0
    http_pages: int = 0
    browser_pages: int = 0
    bytes_downloaded: int = 0
    pagination_pages_enqueued: int = 0
    ssrf_rejected_urls: int = 0

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


async def _safe_enqueue(
    rq: RequestQueue,
    url: str,
    *,
    security: URLSecurityService,
    user_data: dict,
    stats: RunStats,
    max_discovered: int,
) -> bool:
    """Normalize → SSRF validate → budget check → frontier. Never bypass SSRF
    for discovered URLs (pagination, index children, HTML links)."""
    if stats.pages_discovered >= max_discovered:
        return False
    try:
        hostname = security.validate_scheme(url)
        await security.resolve_and_validate(hostname)
    except Exception:  # noqa: BLE001 - URLSecurityError and DNS failures both reject
        stats.ssrf_rejected_urls += 1
        return False
    await _rq_call(rq.add_request, Request.from_url(url, user_data=user_data))
    stats.pages_discovered += 1
    return True


async def run_crawl(
    job_id: uuid.UUID, seed_url: str, settings: Settings, *, extra_seed_urls: list[str] | None = None
) -> None:
    """Runs one crawl job to completion. `extra_seed_urls` (e.g. sitemap or
    RSS entries a monitoring scheduler discovered) are enqueued alongside
    the seed URL for more efficient coverage than a homepage-only crawl."""
    import time

    security = URLSecurityService(settings)
    artifact_store = ArtifactStore(settings)
    stats = RunStats()
    run_start = time.monotonic()

    async with AsyncSessionLocal() as session:
        crawl_repo = CrawlRepository(session)
        job = await crawl_repo.get_job(job_id)
        if job is None:
            logger.error("crawl job %s vanished before start", job_id)
            return
        await crawl_repo.mark_started(job)
        run = await crawl_repo.create_run(job_id)
        run_id = run.id
        source_id = job.source_id
        max_pages, max_depth, same_domain_only = job.max_pages, job.max_depth, job.same_domain_only

    await artifact_store.ensure_bucket()
    await default_bus.publish(Event("CRAWL_STARTED", {"crawl_job_id": str(job_id), "url": seed_url}))

    # Registrable domain (eTLD+1), not exact hostname: same_domain_only is a
    # crawl-SCOPE decision ("stay on this site"), and real sites routinely
    # split across subdomains (a region search subdomain linking to a
    # canonical www listing host, real Craigslist behavior -- see
    # `registrable_domain`'s docstring). Per-subdomain fetch-strategy
    # learning below still keys off the exact hostname; unaffected.
    seed_domain = registrable_domain(seed_url)
    queue_name = f"crawl-{job_id}"
    rq = await RequestQueue.open(name=queue_name)

    try:
        await _rq_call(rq.add_request, Request.from_url(seed_url, user_data={"depth": 0}))
        stats.pages_discovered += 1
        for extra_url in dict.fromkeys(extra_seed_urls or []):
            if extra_url == seed_url:
                continue
            await _rq_call(rq.add_request, Request.from_url(extra_url, user_data={"depth": 0}))
            stats.pages_discovered += 1

        in_flight: set[asyncio.Task] = set()
        cancelled = False

        while stats.pages_attempted < max_pages:
            async with AsyncSessionLocal() as check_session:
                current_job = await CrawlRepository(check_session).get_job(job_id)
                if current_job is not None and current_job.status == "cancelling":
                    cancelled = True
                    break

            while len(in_flight) < settings.crawl_default_concurrency and stats.pages_attempted < max_pages:
                request = await _rq_call(rq.fetch_next_request)
                if request is None:
                    break
                stats.pages_attempted += 1
                task = asyncio.create_task(
                    _process_page(
                        request=request,
                        job_id=job_id,
                        source_id=source_id,
                        seed_domain=seed_domain,
                        same_domain_only=same_domain_only,
                        max_depth=max_depth,
                        settings=settings,
                        security=security,
                        artifact_store=artifact_store,
                        rq=rq,
                        stats=stats,
                    )
                )
                in_flight.add(task)

            if not in_flight:
                if await _rq_call(rq.is_finished):
                    break
                await asyncio.sleep(0.2)
                continue

            done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # re-raise any unexpected exception

        if in_flight:
            await asyncio.wait(in_flight)

        final_status = "cancelled" if cancelled else "completed"

    except asyncio.CancelledError:
        final_status = "cancelled"
        duration_ms = (time.monotonic() - run_start) * 1000
        async with AsyncSessionLocal() as session:
            crawl_repo = CrawlRepository(session)
            await crawl_repo.finalize_run(run_id, stats.as_dict(), duration_ms)
            job = await crawl_repo.get_job(job_id)
            await crawl_repo.mark_finished(job, status="cancelled", error=None, statistics=stats.as_dict())
        await _rq_call(rq.drop)
        await default_bus.publish(Event("CRAWL_CANCELLED", {"crawl_job_id": str(job_id)}))
        raise
    except Exception as exc:  # noqa: BLE001 - must still record the failure
        logger.exception("crawl %s failed", job_id)
        duration_ms = (time.monotonic() - run_start) * 1000
        async with AsyncSessionLocal() as session:
            crawl_repo = CrawlRepository(session)
            await crawl_repo.finalize_run(run_id, stats.as_dict(), duration_ms)
            job = await crawl_repo.get_job(job_id)
            await crawl_repo.mark_finished(job, status="failed", error=str(exc), statistics=stats.as_dict())
        await _rq_call(rq.drop)
        await default_bus.publish(Event("CRAWL_FAILED", {"crawl_job_id": str(job_id), "error": str(exc)}))
        return
    else:
        await _rq_call(rq.drop)

    duration_ms = (time.monotonic() - run_start) * 1000
    async with AsyncSessionLocal() as session:
        crawl_repo = CrawlRepository(session)
        await crawl_repo.finalize_run(run_id, stats.as_dict(), duration_ms)
        job = await crawl_repo.get_job(job_id)
        await crawl_repo.mark_finished(job, status=final_status, error=None, statistics=stats.as_dict())

        if source_id is not None and final_status == "completed":
            source_repo = SourceRepository(session)
            source = await source_repo.get(source_id)
            if source is not None:
                had_changes = (stats.new_documents + stats.updated_documents) > 0
                await source_repo.mark_crawled_and_reschedule(source, had_changes=had_changes)

    await default_bus.publish(
        Event("CRAWL_COMPLETED", {"crawl_job_id": str(job_id), "status": final_status, **stats.as_dict()})
    )


async def _process_page(
    *,
    request: Request,
    job_id: uuid.UUID,
    source_id: uuid.UUID | None,
    seed_domain: str,
    same_domain_only: bool,
    max_depth: int,
    settings: Settings,
    security: URLSecurityService,
    artifact_store: ArtifactStore,
    rq: RequestQueue,
    stats: RunStats,
) -> None:
    url = request.url
    depth = request.user_data.depth or 0
    normalized = normalize_url(url)
    domain = extract_domain(url)

    # --- Phase 1: reads only (item 1 fix) ---------------------------------
    # A session is opened here just long enough for the scope checks +
    # domain/pattern history reads + routing decision, then closed BEFORE
    # any network I/O (politeness sleep, HTTP fetch, Playwright fetch,
    # extraction). The old code held one session open across all of that --
    # for crawl_default_concurrency tasks at once, for the whole duration of
    # a fetch that could include a second Playwright round trip -- which was
    # the actual QueuePool-exhaustion mechanism, not merely "the pool is too
    # small". Splitting into short, sequential read/none/write session
    # blocks means a session is only ever open for the DB round trip it's
    # doing right then.
    async with AsyncSessionLocal() as session:
        crawl_repo = CrawlRepository(session)

        if same_domain_only and registrable_domain(url) != seed_domain:
            await crawl_repo.add_page(
                crawl_job_id=job_id, url=url, normalized_url=normalized, depth=depth, status="skipped"
            )
            await _rq_call(rq.mark_request_as_handled, request)
            return

        if depth > max_depth:
            await crawl_repo.add_page(
                crawl_job_id=job_id, url=url, normalized_url=normalized, depth=depth, status="skipped"
            )
            await _rq_call(rq.mark_request_as_handled, request)
            return

        domain_stats = await crawl_repo.get_strategy_stats(domain)

        # Circuit breaker (spec Phase 6 section 27): a domain that's been
        # failing hard doesn't get hammered while "open" -- skip and let a
        # later crawl re-probe it once the backoff window elapses.
        crawl_delay_ms = 0
        if domain_stats is not None:
            policy_state = PolicyState(
                crawl_delay_ms=domain_stats.crawl_delay_ms,
                recommended_concurrency=domain_stats.recommended_concurrency,
                circuit_state=domain_stats.circuit_state,
                circuit_opened_at=domain_stats.circuit_opened_at,
                consecutive_failures=domain_stats.consecutive_failures,
            )
            if not should_allow_request(policy_state, now=datetime.now(timezone.utc)):
                await crawl_repo.add_page(
                    crawl_job_id=job_id, url=url, normalized_url=normalized, depth=depth,
                    status="skipped", error="circuit breaker open for this domain",
                )
                await default_bus.publish(Event("DOMAIN_THROTTLED", {"domain": domain, "url": url}))
                await _rq_call(rq.mark_request_as_handled, request)
                return
            crawl_delay_ms = domain_stats.crawl_delay_ms

        pattern = normalize_url_pattern(url)
        pattern_stats = await crawl_repo.get_pattern_stats_any_type(domain, pattern)
        domain_js_rate = None
        if domain_stats is not None and domain_stats.http_attempts:
            domain_js_rate = domain_stats.js_required_count / domain_stats.http_attempts
        routing = decide_routing(
            requested=FetchStrategy.AUTO,
            domain_stats=domain_stats,
            pattern_stats=pattern_stats,
            min_observations=settings.domain_learning_min_observations,
        )
        strategy = routing.strategy
        # Browser page budget: once exhausted, force HTTP even if learned browser.
        if strategy == FetchStrategy.BROWSER and stats.browser_pages >= settings.crawl_max_browser_pages:
            strategy = FetchStrategy.HTTP
            routing = type(routing)(
                strategy=strategy,
                reasons=[*routing.reasons, "max_browser_pages budget exhausted"],
                extractor=routing.extractor,
                basis=routing.basis,
            )
    # --- session closed: everything below until Phase 3 does no DB I/O ----

    logger.debug("routing url=%s strategy=%s reasons=%s", url, strategy.value, routing.reasons)
    await default_bus.publish(Event("FETCH_STRATEGY_SELECTED", {"strategy": strategy.value, "reasons": routing.reasons, "basis": routing.basis}))
    await default_bus.publish(Event("STRATEGY_DECIDED", {"strategy": strategy.value, "reasons": routing.reasons, "basis": routing.basis}))

    if crawl_delay_ms > 0:
        await asyncio.sleep(crawl_delay_ms / 1000)

    enable_scroll = strategy == FetchStrategy.BROWSER
    fetch_result = await (
        browser_fetch_page(url, settings, security, enable_scroll=enable_scroll)
        if strategy == FetchStrategy.BROWSER
        else http_fetch_page(url, settings, security, max_bytes=settings.crawl_max_response_bytes)
    )

    if not fetch_result.success:
        stats.pages_failed += 1
        failure_category = classify_transport_error(fetch_result.error or "")
        # --- Phase 3 (failure path): writes only, short session ----------
        async with AsyncSessionLocal() as session:
            crawl_repo = CrawlRepository(session)
            updated_stats = await crawl_repo.record_fetch_outcome(
                domain=domain, strategy=strategy.value, success=False, extraction_ok=False,
                latency_ms=fetch_result.latency_ms, failure_category=failure_category,
                max_concurrency=settings.crawl_default_concurrency,
            )
            await _publish_circuit_transition(domain_stats, updated_stats)
            await default_bus.publish(Event("DOMAIN_PROFILE_UPDATED", {"domain": domain}))
            await crawl_repo.record_pattern_outcome(
                domain=domain, pattern=pattern, page_type=None, strategy=strategy.value,
                success=False, extraction_ok=False, latency_ms=fetch_result.latency_ms,
                failure_category=failure_category,
            )
            await crawl_repo.add_page(
                crawl_job_id=job_id, url=url, normalized_url=normalized, depth=depth,
                status="failed", fetch_strategy=strategy.value, error=fetch_result.error,
            )
            await default_bus.publish(Event("PAGE_FAILED", {"url": url, "error": fetch_result.error}))

            if source_id is not None:
                source_repo = SourceRepository(session)
                source_url, just_removed = await source_repo.record_source_url_failure(
                    source_id, url, normalized,
                    removal_threshold=settings.monitoring_removal_failure_threshold,
                    category=failure_category,
                )
                if just_removed:
                    await source_repo.add_event(
                        source_id=source_id,
                        document_id=source_url.document_id,
                        event_type="REMOVED",
                        change_summary={"reason": "consecutive_fetch_failures", "url": url},
                    )
                    await default_bus.publish(Event("SOURCE_URL_REMOVED", {"source_id": str(source_id), "url": url}))

        await _rq_call(rq.mark_request_as_handled, request)
        return

    stats.pages_fetched += 1
    stats.bytes_downloaded += len(fetch_result.raw_bytes or b"")
    await default_bus.publish(Event("PAGE_FETCHED", {"url": url, "status": fetch_result.status_code}))

    fetch_result, html_analysis, page_extraction, browser_comparison = await _extract_with_browser_escalation(
        fetch_result, strategy, settings, security, stats, is_seed_homepage=(depth == 0),
        pattern_hint=(pattern_stats.page_type if pattern_stats else None),
        domain_js_required_rate=domain_js_rate,
    )
    extracted = page_extraction.document if page_extraction else None
    page_type = page_extraction.classification.page_type.value if page_extraction else None
    extraction_quality = page_extraction.quality.overall if page_extraction and page_extraction.quality else None

    # If browser scroll found infinite-scroll growth, record it.
    if fetch_result.scroll and fetch_result.scroll.new_items_estimate > 0:
        await default_bus.publish(Event("INFINITE_SCROLL_DETECTED", {
            "url": url,
            "scrolls": fetch_result.scroll.scrolls_performed,
            "load_more_clicks": fetch_result.scroll.load_more_clicks,
            "new_items": fetch_result.scroll.new_items_estimate,
            "stopped_reason": fetch_result.scroll.stopped_reason,
        }))

    # Soft-block / fake-200 detection (Universal Adaptive Web Intelligence
    # Phase B, item 1): a transport-successful fetch whose body is a login/
    # captcha/consent/error interstitial rather than real content.
    # `page_type` is overridden so it's visible in stored metadata and
    # domain-learning buckets; the actual "never overwrite good content"
    # behavior is the write-gate further down (item 8).
    soft_block: SoftBlockResult | None = None
    if extracted is not None:
        soft_block = detect_soft_block(
            html=fetch_result.html, extracted_body=extracted.body, extraction_quality=extraction_quality,
        )
        if soft_block.is_blocked:
            page_type = PAGE_TYPE_BY_VERDICT[soft_block.verdict]
            await default_bus.publish(Event("SOFT_BLOCK_DETECTED", {
                "url": url, "verdict": soft_block.verdict.value, "confidence": soft_block.confidence,
                "signals": soft_block.signals,
            }))
    blocked = soft_block is not None and soft_block.is_blocked

    # Pagination (spec Phase 9 sections 6/29): detected here (right after
    # extraction, while fetch_result.html/final_url are on hand) so the
    # outcome can feed record_pattern_outcome below in the same call --
    # avoids a second write to the same URLPatternStats row just to
    # attach one boolean. Enqueued at the SAME depth, not depth+1: a
    # "next page" is more of the same listing, not a deeper link, so a
    # tight max_depth shouldn't cut off page 2 of a search-results list.
    # RequestQueue already dedups by URL, so a `rel=next` link already
    # present in internal_links (the common case) is a harmless no-op
    # re-add here, not a double-crawl.
    pagination_detected = False
    if fetch_result.html:
        pagination = detect_pagination(fetch_result.html, fetch_result.final_url or url)
        pagination_detected = pagination.next_url is not None
        await default_bus.publish(Event("PAGINATION_DETECTED", {
            "url": url, "found": pagination_detected, "method": pagination.method,
            "page_size": pagination.page_size,
        }))
        if (
            pagination.next_url
            and stats.pagination_pages_enqueued < settings.crawl_max_pagination_pages
            and not (same_domain_only and registrable_domain(pagination.next_url) != seed_domain)
        ):
            enqueued = await _safe_enqueue(
                rq, pagination.next_url, security=security, user_data={"depth": depth},
                stats=stats, max_discovered=settings.crawl_max_discovered_urls,
            )
            if enqueued:
                stats.pagination_pages_enqueued += 1

    # Completeness (Phase 8.1 section 19): distinct from quality -- a
    # clean short excerpt of a much longer page is high-quality,
    # low-completeness. Reuses signals already computed above (HTML
    # analysis, listing count, pagination) rather than re-parsing.
    completeness = None
    if extracted is not None and html_analysis is not None:
        listing_count = len((extracted.raw_metadata or {}).get("listings") or [])
        completeness = score_completeness(
            extracted_body_chars=len(extracted.body), page_meaningful_text_chars=html_analysis.meaningful_text_length,
            listing_count=listing_count, internal_link_count=len(html_analysis.internal_links),
            pagination_detected=pagination_detected,
        )
        extracted.raw_metadata = {
            **(extracted.raw_metadata or {}),
            "completeness": completeness.overall,
        }
        await default_bus.publish(Event("EXTRACTION_COMPLETENESS_SCORED", {"url": url, "overall": completeness.overall}))

    # Provenance (item 7 fix): built HERE, after completeness is attached to
    # raw_metadata above -- extraction_router.py used to build this itself,
    # before completeness was ever computed (completeness needs the final,
    # browser-escalation-resolved html_analysis/pagination result, which
    # isn't known until this point), so provenance["completeness"]
    # (provenance.py reads raw_metadata["completeness"]) was always None.
    if extracted is not None:
        provenance = build_provenance(extracted, page_extraction.structured if page_extraction else None)
        extracted.raw_metadata = {**extracted.raw_metadata, "provenance": provenance}
        await default_bus.publish(Event("PROVENANCE_RECORDED", {
            "url": url, "field_count": len(provenance) if "_note" not in provenance else 0,
        }))

    # Discovery -> extraction feedback (Phase 8.1 sections 9/20): a
    # collection/index page's extracted `listings[]` (index_extractor.py,
    # built in Phase 9) are candidate child URLs, not just metadata to
    # store -- without this, /news, /forum, /classifieds indexes were
    # extracted but their items were never actually crawled. Enqueued
    # one level deeper (this IS a real link into a distinct page, unlike
    # pagination above), same same_domain_only/max_depth rules as
    # ordinary link discovery, capped so one index page can't blow the
    # frontier open.
    child_url_count = 0
    if extracted is not None and depth < max_depth:
        child_urls = _extract_child_urls_from_listings(
            extracted.raw_metadata, same_domain_only=same_domain_only, seed_domain=seed_domain,
            max_items=settings.crawl_max_items_per_index,
        )
        for child_url in child_urls:
            if await _safe_enqueue(
                rq, child_url, security=security, user_data={"depth": depth + 1},
                stats=stats, max_discovered=settings.crawl_max_discovered_urls,
            ):
                child_url_count += 1
        if child_url_count:
            await default_bus.publish(Event("INDEX_CHILD_URLS_DISCOVERED", {"url": url, "count": child_url_count}))

    # Write-gate (item 8): a blocked verdict means this is NOT treated as a
    # usable extraction for storage purposes, however plausible the
    # ArticleDocument object the extractor built from the interstitial's own
    # markup looks. `extraction_ok=False` routes it through the existing
    # extraction-failure counters; `FailureCategory.SOFT_BLOCKED` (added to
    # BLOCKED_OR_THROTTLED, same as 403/429) makes it visible in domain
    # learning without a bespoke counter and correctly never trips REMOVED.
    # The document create/update block below is skipped entirely: an
    # existing good document is never overwritten with challenge-page
    # content, and a brand-new blocked URL doesn't get a garbage document
    # created for it on first crawl.
    extraction_ok = extracted is not None and not blocked
    status_failure_category = (
        FailureCategory.SOFT_BLOCKED if blocked
        else (classify_http_status(fetch_result.status_code) if fetch_result.status_code is not None else None)
    )
    if status_failure_category == FailureCategory.HTTP_403:
        http_403_total.inc()
    elif status_failure_category == FailureCategory.HTTP_429:
        http_429_total.inc()
    js_required = strategy == FetchStrategy.HTTP and fetch_result.used_browser
    empty_content = not fetch_result.html
    extractor_used = extracted.extractor if extracted else None
    final_strategy = "browser" if fetch_result.used_browser else "http"

    raw_bytes = (fetch_result.html or "").encode("utf-8") if fetch_result.html else (fetch_result.raw_bytes or b"")
    extension = "html" if fetch_result.html else "bin"
    page_id = uuid.uuid4()
    stored = await artifact_store.put_bytes(
        ArtifactStore.build_key(
            source_id=str(source_id) if source_id else "adhoc",
            crawl_id=str(job_id), page_id=str(page_id), extension=extension,
        ),
        raw_bytes,
        fetch_result.content_type or "application/octet-stream",
    )
    document_id = None
    change_type = None

    # --- Phase 3: writes only, fresh short session ------------------------
    # Everything above this point (fetch, extraction, soft-block detection,
    # pagination, completeness, provenance, artifact upload) did no DB I/O.
    async with AsyncSessionLocal() as session:
        crawl_repo = CrawlRepository(session)
        doc_repo = DocumentRepository(session)

        updated_stats = await crawl_repo.record_fetch_outcome(
            domain=domain,
            strategy=final_strategy,
            success=True,
            extraction_ok=extraction_ok,
            latency_ms=fetch_result.latency_ms,
            failure_category=status_failure_category,
            content_bytes=len(fetch_result.raw_bytes or b""),
            status_code=fetch_result.status_code,
            js_required=js_required,
            empty_content=empty_content,
            extractor_used=extractor_used,
            extraction_quality=extraction_quality,
            max_concurrency=settings.crawl_default_concurrency,
            completeness=completeness.overall if completeness else None,
            browser_comparison=browser_comparison,
            index_children_discovered=child_url_count,
        )
        if js_required:
            await default_bus.publish(Event("STRATEGY_ESCALATED", {"url": url, "domain": domain}))
        await _publish_circuit_transition(domain_stats, updated_stats)
        await default_bus.publish(Event("DOMAIN_PROFILE_UPDATED", {"domain": domain}))
        await crawl_repo.record_pattern_outcome(
            domain=domain, pattern=pattern, page_type=page_type, strategy=final_strategy,
            success=True, extraction_ok=extraction_ok, latency_ms=fetch_result.latency_ms,
            content_bytes=len(fetch_result.raw_bytes or b""), failure_category=status_failure_category,
            extractor_used=extractor_used, extraction_quality=extraction_quality,
            pagination_detected=pagination_detected, browser_comparison=browser_comparison,
        )
        await default_bus.publish(Event("URL_PATTERN_PROFILE_UPDATED", {"domain": domain, "pattern": pattern}))
        await default_bus.publish(Event("PATTERN_PROFILE_UPDATED", {"domain": domain, "pattern": pattern}))

        if fetch_result.used_browser:
            stats.browser_pages += 1
        else:
            stats.http_pages += 1

        if extracted is not None and not blocked:
            stats.pages_extracted += 1
            content_hash = exact_hash(raw_bytes)
            norm_hash = normalized_hash(extracted.body)
            body_simhash = simhash64(extracted.body)
            extra_metadata = {
                **extracted.raw_metadata,
                "publisher": extracted.publisher,
                "section": extracted.section,
                "tags": extracted.tags,
                "fields_detected": extracted.fields_detected,
                "classification_signals": page_extraction.classification.signals if page_extraction else [],
                "completeness": completeness.overall if completeness else None,
            }

            # Item 3 fix: `find_by_normalized_url` is a `FOR UPDATE` read,
            # but that only locks a row that already EXISTS -- it can't stop
            # two concurrent crawls of the same URL (e.g. an instant crawl
            # racing a scheduled monitoring crawl) from both seeing `None`
            # and both attempting to create. The DDL's partial unique index
            # on normalized_url is what actually prevents the duplicate: the
            # loser's INSERT blocks until the winner commits, then raises
            # IntegrityError. Retried once as an update instead of letting
            # it propagate up and fail the entire crawl job over one URL.
            for attempt in range(2):
                existing = await doc_repo.find_by_normalized_url(normalized)
                if existing is not None:
                    break

                # Cross-source duplicate foundation (spec Phase 7 section
                # 38): a different URL landing on identical normalized body
                # text -- syndicated/reposted content. Flagged, not merged
                # -- no story-clustering decision is made here.
                near_dup = await doc_repo.find_by_normalized_hash(norm_hash)
                if near_dup is not None:
                    extra_metadata["near_duplicate_of"] = str(near_dup.id)
                    await default_bus.publish(
                        Event("NEAR_DUPLICATE_DETECTED", {"document_id": str(near_dup.id), "url": url})
                    )

                try:
                    document = await doc_repo.create(
                        source_id=source_id,
                        crawl_job_id=job_id,
                        url=url,
                        normalized_url=normalized,
                        canonical_url=extracted.canonical_url,
                        domain=domain,
                        title=extracted.headline,
                        author=extracted.author,
                        published_at=extracted.published_at,
                        source_updated_at=None,
                        language=extracted.language,
                        content_type="html",
                        page_type=page_type,
                        current_content=extracted.body,
                        extracted_metadata=extra_metadata,
                        links=(html_analysis.internal_links[:200] if html_analysis else []),
                        images=extracted.images,
                        content_hash=content_hash,
                        normalized_hash=norm_hash,
                        simhash=to_signed_int64(body_simhash),
                        extraction_method=extracted.extractor,
                        extraction_confidence=extracted.confidence,
                        current_version=1,
                    )
                except IntegrityError:
                    if attempt == 1:
                        raise  # not the expected race -- surface it
                    await session.rollback()
                    continue

                await doc_repo.add_version(
                    document,
                    version_number=1,
                    change_type="NEW",
                    title=extracted.headline,
                    content=extracted.body,
                    content_hash=content_hash,
                    normalized_hash=norm_hash,
                    storage_key=stored.storage_key,
                    raw_content_type=stored.content_type,
                    raw_size_bytes=stored.size,
                    raw_sha256=stored.sha256,
                    extraction_metadata=extra_metadata,
                )
                stats.new_documents += 1
                change_type = "NEW"
                document_id = document.id
                await default_bus.publish(Event("DOCUMENT_CREATED", {"document_id": str(document.id), "url": url}))
                if source_id is not None:
                    await SourceRepository(session).add_event(
                        source_id=source_id, document_id=document.id, event_type="NEW", new_version=1,
                    )
                await _run_intelligence(
                    session, document_id=document.id, title=extracted.headline, body=extracted.body,
                    simhash_signed=document.simhash, page_type=page_type, domain=domain,
                    published_at=document.published_at, updated_at=None,
                )
                existing = None
                break

            if existing is not None:
                # Phase 7 integration point: this is where a freshly
                # extracted document meets the previously stored one. The
                # old gate here was a bare `existing.normalized_hash !=
                # norm_hash` (body text only) -- too coarse for e.g. a
                # classified listing whose price changed but description
                # didn't (body hash identical, nothing would ever fire).
                # ChangeDetectionService now makes that call, page-type-
                # aware, using a snapshot of `existing`'s fields captured
                # BEFORE any mutation below.
                previous_snapshot = DocumentSnapshot(
                    title=existing.title, author=existing.author, published_at=existing.published_at,
                    body=existing.current_content, images=existing.images or [],
                    metadata=existing.extracted_metadata or {},
                    simhash=(from_signed_int64(existing.simhash) if existing.simhash is not None else None),
                )
                current_snapshot = DocumentSnapshot(
                    title=extracted.headline, author=extracted.author, published_at=extracted.published_at,
                    body=extracted.body, images=extracted.images, metadata=extra_metadata, simhash=body_simhash,
                )
                detection_started = time.monotonic()
                change_result = detect_change(previous=previous_snapshot, current=current_snapshot, page_type=page_type)
                detection_seconds = time.monotonic() - detection_started

                logger.info(
                    "document_change_detected document_id=%s page_type=%s change_type=%s severity=%s "
                    "similarity=%.3f changed_fields=%s",
                    existing.id, page_type, change_result.change_type.value, change_result.severity.value,
                    change_result.similarity, change_result.changed_fields,
                )
                await default_bus.publish(Event("DOCUMENT_CHANGE_DETECTED", {
                    "document_id": str(existing.id), "page_type": page_type, "changed": change_result.changed,
                    "change_type": change_result.change_type.value, "severity": change_result.severity.value,
                    "duration_seconds": detection_seconds,
                }))

                if change_result.changed:
                    previous_version = await doc_repo.get_version(existing.id, existing.current_version)

                    if source_id is not None and existing.source_id is None:
                        existing.source_id = source_id
                    existing.crawl_job_id = job_id
                    existing.title = extracted.headline
                    existing.author = extracted.author
                    existing.published_at = extracted.published_at
                    existing.page_type = page_type
                    existing.extraction_method = extracted.extractor
                    existing.extraction_confidence = extracted.confidence
                    existing.extracted_metadata = extra_metadata
                    existing.images = extracted.images
                    existing.content_hash = content_hash
                    existing.normalized_hash = norm_hash
                    existing.current_content = extracted.body
                    existing.simhash = to_signed_int64(body_simhash)
                    existing.current_version += 1
                    current_version_row = await doc_repo.add_version(
                        existing,
                        version_number=existing.current_version,
                        change_type="UPDATED",
                        title=extracted.headline,
                        content=extracted.body,
                        content_hash=content_hash,
                        normalized_hash=norm_hash,
                        storage_key=stored.storage_key,
                        raw_content_type=stored.content_type,
                        raw_size_bytes=stored.size,
                        raw_sha256=stored.sha256,
                        extraction_metadata=extra_metadata,
                    )
                    await doc_repo.add_change(
                        document_id=existing.id,
                        previous_version_id=(previous_version.id if previous_version else None),
                        current_version_id=current_version_row.id,
                        page_type=page_type,
                        change_type=change_result.change_type.value,
                        severity=change_result.severity.value,
                        similarity=change_result.similarity,
                        change_confidence=change_result.change_confidence,
                        changed_fields=change_result.changed_fields,
                        diff=change_result.diff,
                        reasons=change_result.reasons,
                    )
                    stats.updated_documents += 1
                    change_type = "UPDATED"
                    document_id = existing.id
                    await default_bus.publish(Event("DOCUMENT_UPDATED", {"document_id": str(existing.id), "url": url}))
                    if source_id is not None:
                        await SourceRepository(session).add_event(
                            source_id=source_id, document_id=existing.id, event_type="UPDATED",
                            previous_version=existing.current_version - 1, new_version=existing.current_version,
                            change_summary={
                                "change_type": change_result.change_type.value,
                                "severity": change_result.severity.value,
                            },
                        )
                    await _run_intelligence(
                        session, document_id=existing.id, title=extracted.headline, body=extracted.body,
                        simhash_signed=existing.simhash, page_type=page_type, domain=domain,
                        published_at=existing.published_at, updated_at=None,
                    )
                else:
                    if source_id is not None and existing.source_id is None:
                        existing.source_id = source_id
                    existing.crawl_job_id = job_id
                    existing.change_status = "UNCHANGED"
                    stats.unchanged_documents += 1
                    change_type = "UNCHANGED"
                    document_id = existing.id
                    await default_bus.publish(Event("DOCUMENT_UNCHANGED", {"document_id": str(existing.id), "url": url}))
                    if source_id is not None:
                        await SourceRepository(session).add_event(
                            source_id=source_id, document_id=existing.id, event_type="UNCHANGED",
                            new_version=existing.current_version,
                        )

        if source_id is not None:
            source_repo = SourceRepository(session)
            # A transport-level "success" (we got an HTTP response) does not
            # mean the page is still there -- a 4xx/5xx status is a signal
            # REMOVED detection cares about (spec section 76), routed through
            # the same consecutive-failure counter as a hard fetch failure.
            # But NOT every 4xx means "gone": classify_http_status + the
            # removal-eligibility check inside record_source_url_failure
            # keeps a 403/429 from ever triggering REMOVED (spec Phase L/M)
            # -- being blocked says nothing about whether the content exists.
            # Checked unconditionally (not just when extraction succeeded)
            # since a 404 page usually has nothing to extract.
            status_category = (
                classify_http_status(fetch_result.status_code) if fetch_result.status_code is not None
                else FailureCategory.NONE
            )
            page_unavailable = status_category != FailureCategory.NONE
            if page_unavailable:
                _, just_removed = await source_repo.record_source_url_failure(
                    source_id, url, normalized, removal_threshold=settings.monitoring_removal_failure_threshold,
                    category=status_category,
                )
                if just_removed:
                    await source_repo.add_event(
                        source_id=source_id, document_id=document_id, event_type="REMOVED",
                        change_summary={"reason": "http_error_status", "status_code": fetch_result.status_code, "url": url},
                    )
                    await default_bus.publish(Event("SOURCE_URL_REMOVED", {"source_id": str(source_id), "url": url}))
            else:
                _, was_restored = await source_repo.upsert_source_url_success(source_id, url, normalized, document_id)
                if was_restored:
                    await source_repo.add_event(
                        source_id=source_id, document_id=document_id, event_type="RESTORED",
                        change_summary={"reason": "successful_crawl_after_removal", "url": url},
                    )
                    await default_bus.publish(Event("SOURCE_URL_RESTORED", {"source_id": str(source_id), "url": url}))

        await crawl_repo.add_page(
            crawl_job_id=job_id, url=url, normalized_url=normalized, depth=depth,
            status="fetched", fetch_strategy="browser" if fetch_result.used_browser else "http",
            http_status=fetch_result.status_code, content_hash=exact_hash(raw_bytes), document_id=document_id,
        )
        await default_bus.publish(Event("PAGE_PARSED", {"url": url, "change_type": change_type}))
        await doc_repo.commit()
    # --- session closed ----------------------------------------------------

    if html_analysis and depth < max_depth:
        for link in html_analysis.internal_links:
            if same_domain_only and registrable_domain(link) != seed_domain:
                continue
            await _safe_enqueue(
                rq, link, security=security, user_data={"depth": depth + 1},
                stats=stats, max_discovered=settings.crawl_max_discovered_urls,
            )

    await _rq_call(rq.mark_request_as_handled, request)


def _extract_child_urls_from_listings(
    raw_metadata: dict | None, *, same_domain_only: bool, seed_domain: str,
    max_items: int = _MAX_CHILD_URLS_PER_INDEX_PAGE,
) -> list[str]:
    """Pure (no I/O) so it's unit-testable without a DB session -- the
    enqueue side effect stays in `_process_page`, this just decides WHICH
    URLs qualify. `raw_metadata["listings"]` is `_index_to_document`'s own
    shape (Phase 9's index_extractor.py): a list of dicts with a "url" key,
    possibly None when an item had no discoverable link.

    `seed_domain` is a REGISTRABLE domain (`registrable_domain(seed_url)`,
    eTLD+1), not an exact hostname -- real listing pages routinely link
    cross-subdomain (confirmed against the real Craigslist fixture: a
    `sfbay.craigslist.org` search page's items all link to
    `www.craigslist.org`), which an exact-hostname comparison would wrongly
    reject as "off-site" and silently drop every single listing."""
    listings = (raw_metadata or {}).get("listings") or []
    urls: list[str] = []
    seen: set[str] = set()
    for item in listings[:max_items]:
        child_url = item.get("url") if isinstance(item, dict) else None
        if not child_url:
            continue
        if same_domain_only and registrable_domain(child_url) != seed_domain:
            continue
        if child_url in seen:
            continue
        seen.add(child_url)
        urls.append(child_url)
    return urls


async def _extract_with_browser_escalation(
    fetch_result: PageFetchResult,
    strategy: FetchStrategy,
    settings: Settings,
    security: URLSecurityService,
    stats: RunStats,
    *,
    is_seed_homepage: bool,
    pattern_hint: str | None = None,
    domain_js_required_rate: float | None = None,
) -> tuple[PageFetchResult, HTMLAnalysisResult | None, PageExtractionResult | None, str | None]:
    """Given an HTTP fetch result, decide whether the page needs a browser
    re-fetch and return whichever result actually extracted usable content.
    Fourth return value is the browser comparison label for learning:
    browser_superior | http_superior | equivalent | None (no compare).
    """
    if not fetch_result.html:
        return fetch_result, None, None, None

    html_analysis = analyze_html(fetch_result.html, fetch_result.final_url or "")
    extraction = await extract_for_page(
        fetch_result.final_url or "", fetch_result.html,
        html_analysis=html_analysis, is_seed_homepage=is_seed_homepage, pattern_hint=pattern_hint,
    )

    if strategy == FetchStrategy.BROWSER:
        return fetch_result, html_analysis, extraction, None

    if stats.browser_pages >= settings.crawl_max_browser_pages:
        return fetch_result, html_analysis, extraction, None

    quality_overall = extraction.quality.overall if extraction.quality else None
    js_assessment = assess_javascript_dependency(
        fetch_result.html,
        html_analysis,
        extraction_quality=quality_overall,
        discovered_link_count=len(html_analysis.internal_links),
        domain_js_required_rate=domain_js_required_rate,
    )
    # Also escalate when HTTP extraction quality is very low even without
    # classic SPA markers (adaptive quality gate).
    quality_escalation = quality_overall is not None and quality_overall < settings.domain_low_quality_http_threshold
    if not js_assessment.likely_requires_browser and not quality_escalation:
        return fetch_result, html_analysis, extraction, None

    page_type = extraction.classification.page_type.value if extraction else None
    enable_scroll = page_type in _SCROLL_PAGE_TYPES if page_type else True
    browser_result = await browser_fetch_page(
        fetch_result.final_url or "", settings, security, enable_scroll=enable_scroll,
    )
    if not browser_result.success or not browser_result.html:
        return fetch_result, html_analysis, extraction, None

    if browser_result.scroll and browser_result.scroll.new_items_estimate > 0:
        await default_bus.publish(Event("INFINITE_SCROLL_DETECTED", {
            "url": browser_result.final_url,
            "scrolls": browser_result.scroll.scrolls_performed,
            "load_more_clicks": browser_result.scroll.load_more_clicks,
            "new_items": browser_result.scroll.new_items_estimate,
            "stopped_reason": browser_result.scroll.stopped_reason,
        }))

    browser_html_analysis = analyze_html(browser_result.html, browser_result.final_url or "")
    browser_extraction = await extract_for_page(
        browser_result.final_url or "", browser_result.html,
        html_analysis=browser_html_analysis, is_seed_homepage=is_seed_homepage, pattern_hint=pattern_hint,
    )

    original_quality = extraction.quality.overall if extraction.quality else None
    browser_quality = browser_extraction.quality.overall if browser_extraction.quality else None

    if original_quality is not None and browser_quality is not None:
        if browser_quality > original_quality + 0.02:
            comparison = "browser_superior"
            browser_wins = True
        elif original_quality > browser_quality + 0.02:
            comparison = "http_superior"
            browser_wins = False
        else:
            comparison = "equivalent"
            browser_wins = False
    else:
        browser_len = len(browser_extraction.document.body) if browser_extraction.document else 0
        original_len = len(extraction.document.body) if extraction.document else 0
        browser_wins = browser_len > original_len
        comparison = "browser_superior" if browser_wins else ("http_superior" if original_len > browser_len else "equivalent")

    await default_bus.publish(Event("BROWSER_ESCALATION_COMPARED", {
        "url": fetch_result.final_url, "original_quality": original_quality, "browser_quality": browser_quality,
        "browser_wins": browser_wins, "comparison": comparison,
    }))

    if not browser_wins:
        return fetch_result, html_analysis, extraction, comparison

    return browser_result, browser_html_analysis, browser_extraction, comparison


async def _publish_circuit_transition(domain_stats_before, updated_stats) -> None:
    """Fires DOMAIN_CIRCUIT_OPENED exactly on the healthy/degraded -> open
    edge, not on every subsequent failed observation while already open
    (which would just spam the counter)."""
    was_open = domain_stats_before is not None and domain_stats_before.circuit_state == "open"
    if updated_stats.circuit_state == "open" and not was_open:
        await default_bus.publish(Event("DOMAIN_CIRCUIT_OPENED", {"domain": updated_stats.domain}))


async def _run_intelligence(
    session, *, document_id: uuid.UUID, title: str | None, body: str, simhash_signed: int | None,
    page_type: str | None, domain: str, published_at, updated_at,
) -> None:
    """Phase 8 entry point (spec section 37): only called for NEW/UPDATED
    documents, never UNCHANGED -- Phase 7's ChangeDetectionService already
    proved nothing meaningful changed, so re-running NER/story-matching
    against the same content would be exactly the unnecessary reprocessing
    the spec asks to avoid. NLP failure must never fail the crawl (spec
    section 46) -- caught and logged, not re-raised."""
    start = time.monotonic()
    try:
        outcome = await process_document_intelligence(
            session, document_id=document_id, title=title, body=body, simhash_signed=simhash_signed,
            page_type=page_type, domain=domain, published_at=published_at, updated_at=updated_at,
        )
    except Exception:  # noqa: BLE001 - intelligence is optional; the crawl must succeed regardless
        logger.warning("document intelligence processing failed for document_id=%s", document_id, exc_info=True)
        await default_bus.publish(Event("INTELLIGENCE_FAILED", {}))
        return

    duration = time.monotonic() - start
    await default_bus.publish(Event("ENTITY_EXTRACTION_COMPLETED", {
        "entity_count": len(outcome.entities), "duration_seconds": duration,
        "extractors_used": outcome.extractors_used, "extractors_unavailable": outcome.extractors_unavailable,
        "resolution_counts": outcome.resolution_counts,
    }))
    await default_bus.publish(Event("STORY_MATCH_DECIDED", {
        "decision": outcome.story_decision, "confidence": outcome.confidence,
        "candidate_count": outcome.candidate_count,
    }))
