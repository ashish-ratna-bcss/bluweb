from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.crawl import CrawlJob, CrawlPage, CrawlRun, FetchStrategyStats, URLPatternStats
from app.services.crawling.domain_policy_service import PolicyState, update_policy_after_outcome
from app.services.crawling.failure_classification import FailureCategory


class CrawlRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_job(
        self,
        *,
        seed_url: str,
        max_pages: int,
        max_depth: int,
        same_domain_only: bool,
        crawl_type: str = "instant",
        source_id: uuid.UUID | None = None,
    ) -> CrawlJob:
        job = CrawlJob(
            id=uuid.uuid4(),
            source_id=source_id,
            seed_url=seed_url,
            crawl_type=crawl_type,
            status="queued",
            max_pages=max_pages,
            max_depth=max_depth,
            same_domain_only=same_domain_only,
        )
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def get_active_job_for_source(self, source_id: uuid.UUID) -> CrawlJob | None:
        result = await self._session.execute(
            select(CrawlJob).where(
                CrawlJob.source_id == source_id,
                CrawlJob.status.in_(("queued", "running", "cancelling")),
            )
        )
        return result.scalars().first()

    async def get_job(self, job_id: uuid.UUID, *, fresh: bool = False) -> CrawlJob | None:
        """`fresh=True` forces a re-read even if this session already has
        the row cached in its identity map from an earlier query -- needed
        when polling for changes another session (e.g. the background crawl
        task) is committing. `expire_all()` + re-query doesn't work here:
        SQLAlchemy's async ORM does the resulting refresh outside the
        awaited call, which crashes with MissingGreenlet. `populate_existing`
        forces the overwrite inside the same awaited execute()."""
        stmt = select(CrawlJob).where(CrawlJob.id == job_id)
        if fresh:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_jobs(self, *, limit: int = 50) -> list[CrawlJob]:
        result = await self._session.execute(
            select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def mark_started(self, job: CrawlJob) -> None:
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def mark_finished(self, job: CrawlJob, *, status: str, error: str | None, statistics: dict) -> None:
        job.status = status
        job.error = error
        job.statistics = statistics
        job.completed_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def request_cancel(self, job: CrawlJob) -> None:
        job.status = "cancelling"
        await self._session.commit()

    async def create_run(self, job_id: uuid.UUID) -> CrawlRun:
        run = CrawlRun(id=uuid.uuid4(), crawl_job_id=job_id)
        self._session.add(run)
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def get_run(self, run_id: uuid.UUID) -> CrawlRun | None:
        result = await self._session.execute(select(CrawlRun).where(CrawlRun.id == run_id))
        return result.scalar_one_or_none()

    async def finalize_run(self, run_id: uuid.UUID, stats: dict, duration_ms: float) -> None:
        run = await self.get_run(run_id)
        if run is None:
            return
        for field_name in (
            "pages_discovered", "pages_attempted", "pages_fetched", "pages_failed", "pages_extracted",
            "new_documents", "updated_documents", "unchanged_documents", "duplicate_documents",
            "http_pages", "browser_pages", "bytes_downloaded",
        ):
            if field_name in stats:
                setattr(run, field_name, stats[field_name])
        run.duration_ms = duration_ms
        run.completed_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def add_page(
        self,
        *,
        crawl_job_id: uuid.UUID,
        url: str,
        normalized_url: str,
        depth: int,
        status: str,
        fetch_strategy: str | None = None,
        http_status: int | None = None,
        content_hash: str | None = None,
        document_id: uuid.UUID | None = None,
        error: str | None = None,
    ) -> CrawlPage:
        page = CrawlPage(
            id=uuid.uuid4(),
            crawl_job_id=crawl_job_id,
            url=url,
            normalized_url=normalized_url,
            depth=depth,
            status=status,
            fetch_strategy=fetch_strategy,
            http_status=http_status,
            content_hash=content_hash,
            document_id=document_id,
            error=error,
            fetched_at=datetime.now(timezone.utc) if status in ("fetched", "failed") else None,
        )
        self._session.add(page)
        await self._session.commit()
        return page

    async def list_pages(self, crawl_job_id: uuid.UUID) -> list[CrawlPage]:
        result = await self._session.execute(
            select(CrawlPage).where(CrawlPage.crawl_job_id == crawl_job_id).order_by(CrawlPage.discovered_at)
        )
        return list(result.scalars().all())

    async def get_strategy_stats(self, domain: str) -> FetchStrategyStats | None:
        result = await self._session.execute(select(FetchStrategyStats).where(FetchStrategyStats.domain == domain))
        return result.scalar_one_or_none()

    async def _get_strategy_stats_locked(self, domain: str) -> FetchStrategyStats:
        """Every page on the same domain contends for this one row --
        `_process_page` runs several concurrently (crawl_default_concurrency),
        so a plain SELECT-then-UPDATE here is a lost-update race: two
        sessions both read count=N, both write N+1, one increment vanishes
        (confirmed live: a 15-page crawl only recorded 4 domain-level
        attempts). INSERT..ON CONFLICT DO NOTHING makes row creation
        idempotent across concurrent first-observers, then SELECT..FOR
        UPDATE serializes the read-modify-write on this domain's row
        specifically -- other domains' rows are untouched and don't block.

        `populate_existing=True` is required here too, and for a subtler
        reason than the SELECT above the lock: `_process_page` already did
        a plain (non-locking) `get_strategy_stats(domain)` earlier in this
        *same session* for the routing decision, which put this row in the
        session's identity map. Without `populate_existing`, this FOR UPDATE
        SELECT still acquires the lock at the SQL level but SQLAlchemy hands
        back the *already-cached Python object* instead of refreshing its
        attributes from the row it just locked -- so every increment below
        would silently operate on the stale pre-fetch snapshot. Confirmed
        live: this exact scenario reproduced the lost-update bug even with
        the lock in place; isolated tests without an earlier plain read in
        the same session couldn't reproduce it, which is what pointed here.
        """
        insert_stmt = pg_insert(FetchStrategyStats).values(
            domain=domain,
            http_attempts=0, http_successes=0, http_extraction_failures=0,
            browser_attempts=0, browser_successes=0,
            avg_http_latency_ms=0.0, avg_browser_latency_ms=0.0, avg_content_bytes=0.0,
            failure_counts={}, success_status_counts={},
            js_required_count=0, empty_content_count=0,
            crawl_delay_ms=0.0, recommended_concurrency=5, circuit_state="healthy",
            circuit_opened_at=None, consecutive_failures=0,
            sitemap_status="unknown", feed_status="unknown", sitemap_url_count=0, feed_url_count=0,
            avg_extraction_quality=0.0, quality_observations=0,
            browser_superior_count=0, http_superior_count=0, browser_equivalent_count=0,
            avg_completeness=0.0, completeness_observations=0, index_children_discovered_total=0,
        ).on_conflict_do_nothing(index_elements=["domain"])
        await self._session.execute(insert_stmt)

        result = await self._session.execute(
            select(FetchStrategyStats)
            .where(FetchStrategyStats.domain == domain)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one()

    async def record_fetch_outcome(
        self,
        *,
        domain: str,
        strategy: str,
        success: bool,
        extraction_ok: bool,
        latency_ms: float,
        failure_category: FailureCategory | None = None,
        content_bytes: int | None = None,
        status_code: int | None = None,
        js_required: bool = False,
        empty_content: bool = False,
        extractor_used: str | None = None,
        extraction_quality: float | None = None,
        max_concurrency: int = 5,
        completeness: float | None = None,
        browser_comparison: str | None = None,
        index_children_discovered: int = 0,
    ) -> FetchStrategyStats:
        stats = await self._get_strategy_stats_locked(domain)

        if strategy == "http":
            stats.http_attempts += 1
            if success:
                stats.http_successes += 1
            if success and not extraction_ok:
                stats.http_extraction_failures += 1
            stats.avg_http_latency_ms = _running_average(
                stats.avg_http_latency_ms, stats.http_attempts, latency_ms
            )
        else:
            stats.browser_attempts += 1
            if success:
                stats.browser_successes += 1
            stats.avg_browser_latency_ms = _running_average(
                stats.avg_browser_latency_ms, stats.browser_attempts, latency_ms
            )

        if content_bytes is not None:
            total_attempts = stats.http_attempts + stats.browser_attempts
            stats.avg_content_bytes = _running_average(stats.avg_content_bytes, total_attempts, content_bytes)

        if failure_category is not None and failure_category != FailureCategory.NONE:
            counts = dict(stats.failure_counts)
            counts[failure_category.value] = counts.get(failure_category.value, 0) + 1
            stats.failure_counts = counts

        if status_code is not None:
            bucket = f"{status_code // 100}xx"
            counts = dict(stats.success_status_counts)
            counts[bucket] = counts.get(bucket, 0) + 1
            stats.success_status_counts = counts

        if js_required:
            stats.js_required_count += 1
        if empty_content:
            stats.empty_content_count += 1
        if extractor_used and extraction_ok:
            stats.preferred_extractor = extractor_used

        if extraction_quality is not None:
            stats.quality_observations += 1
            stats.avg_extraction_quality = _running_average(
                stats.avg_extraction_quality, stats.quality_observations, extraction_quality
            )

        if completeness is not None:
            stats.completeness_observations += 1
            stats.avg_completeness = _running_average(
                stats.avg_completeness, stats.completeness_observations, completeness
            )

        if browser_comparison == "browser_superior":
            stats.browser_superior_count += 1
        elif browser_comparison == "http_superior":
            stats.http_superior_count += 1
        elif browser_comparison == "equivalent":
            stats.browser_equivalent_count += 1

        if index_children_discovered:
            stats.index_children_discovered_total += index_children_discovered

        now = datetime.now(timezone.utc)
        stats.last_observed_at = now

        policy_before = PolicyState(
            crawl_delay_ms=stats.crawl_delay_ms,
            recommended_concurrency=stats.recommended_concurrency,
            circuit_state=stats.circuit_state,
            circuit_opened_at=stats.circuit_opened_at,
            consecutive_failures=stats.consecutive_failures,
        )
        # A successful transport that nonetheless returned a real failure
        # status (e.g. HTTP 200 request but classify_http_status flagged
        # something) should still count as a failure for policy purposes.
        policy_category = failure_category if failure_category is not None else (
            FailureCategory.NONE if success else FailureCategory.OTHER_HTTP_ERROR
        )
        policy_after = update_policy_after_outcome(
            policy_before, failure_category=policy_category, now=now, max_concurrency=max_concurrency,
        )
        stats.crawl_delay_ms = policy_after.crawl_delay_ms
        stats.recommended_concurrency = policy_after.recommended_concurrency
        stats.circuit_state = policy_after.circuit_state
        stats.circuit_opened_at = policy_after.circuit_opened_at
        stats.consecutive_failures = policy_after.consecutive_failures

        await self._session.commit()
        return stats

    async def record_discovery_outcome(
        self, *, domain: str, sitemap_status: str, feed_status: str, sitemap_url_count: int = 0, feed_url_count: int = 0,
    ) -> FetchStrategyStats:
        """One discovery probe per crawl (not per page), so this shares the
        same locked domain row `record_fetch_outcome` writes rather than a
        separate table -- a domain's discovery capabilities and its fetch
        capabilities are the same "what do we know about this domain" fact,
        spec Phase 9 sections 2/7."""
        stats = await self._get_strategy_stats_locked(domain)
        stats.sitemap_status = sitemap_status
        stats.feed_status = feed_status
        stats.sitemap_url_count = sitemap_url_count
        stats.feed_url_count = feed_url_count
        stats.last_observed_at = datetime.now(timezone.utc)
        await self._session.commit()
        return stats

    # -- URL pattern stats (spec Phase 6 section 7) --

    async def list_pattern_stats(self, domain: str, *, limit: int = 100) -> list[URLPatternStats]:
        result = await self._session.execute(
            select(URLPatternStats)
            .where(URLPatternStats.domain == domain)
            .order_by(URLPatternStats.last_observed_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_pattern_stats_any_type(self, domain: str, pattern: str) -> URLPatternStats | None:
        """For the routing decision, which happens *before* the page is
        fetched/classified -- we don't know this URL's page_type yet, but a
        prior crawl of the same pattern might have. Falls back to whatever
        row exists for (domain, pattern) regardless of page_type."""
        result = await self._session.execute(
            select(URLPatternStats)
            .where(URLPatternStats.domain == domain, URLPatternStats.pattern == pattern)
            .order_by(URLPatternStats.last_observed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def record_pattern_outcome(
        self,
        *,
        domain: str,
        pattern: str,
        page_type: str | None,
        strategy: str,
        success: bool,
        extraction_ok: bool,
        latency_ms: float,
        content_bytes: int | None = None,
        failure_category: FailureCategory | None = None,
        extractor_used: str | None = None,
        extraction_quality: float | None = None,
        pagination_detected: bool = False,
        browser_comparison: str | None = None,
    ) -> None:
        # SQL NULL isn't equal to itself for uniqueness purposes -- two
        # concurrent "unknown page_type" inserts for the same (domain,
        # pattern) wouldn't conflict and would silently duplicate the row.
        # "UNKNOWN" is already a real PageType value; reuse it as the
        # not-yet-classified sentinel so the unique constraint actually
        # applies.
        effective_page_type = page_type or "UNKNOWN"

        insert_stmt = pg_insert(URLPatternStats).values(
            id=uuid.uuid4(), domain=domain, pattern=pattern, page_type=effective_page_type,
            fetch_attempts=0, successful_fetches=0, extraction_attempts=0, extraction_successes=0,
            http_attempts=0, http_successes=0, browser_attempts=0, browser_successes=0,
            avg_latency_ms=0.0, avg_content_bytes=0.0, failure_counts={},
            avg_extraction_quality=0.0, quality_observations=0, pagination_detected_count=0,
            browser_superior_count=0, http_superior_count=0, browser_equivalent_count=0,
        ).on_conflict_do_nothing(index_elements=["domain", "pattern", "page_type"])
        await self._session.execute(insert_stmt)

        # populate_existing=True: get_pattern_stats_any_type() reads this same
        # row earlier in the request for pre-fetch routing, seeding the
        # session identity map. Without this the FOR UPDATE lock is taken at
        # the SQL level but the ORM hands back the stale cached object, so
        # every += below silently operates on pre-fetch values (same class
        # of bug fixed in _get_strategy_stats_locked above).
        result = await self._session.execute(
            select(URLPatternStats)
            .where(
                URLPatternStats.domain == domain,
                URLPatternStats.pattern == pattern,
                URLPatternStats.page_type == effective_page_type,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stats = result.scalar_one()

        stats.fetch_attempts += 1
        if success:
            stats.successful_fetches += 1
        if success:
            stats.extraction_attempts += 1
            if extraction_ok:
                stats.extraction_successes += 1

        if strategy == "http":
            stats.http_attempts += 1
            if success:
                stats.http_successes += 1
        else:
            stats.browser_attempts += 1
            if success:
                stats.browser_successes += 1

        stats.avg_latency_ms = _running_average(stats.avg_latency_ms, stats.fetch_attempts, latency_ms)
        if content_bytes is not None:
            stats.avg_content_bytes = _running_average(stats.avg_content_bytes, stats.fetch_attempts, content_bytes)

        if failure_category is not None and failure_category != FailureCategory.NONE:
            counts = dict(stats.failure_counts)
            counts[failure_category.value] = counts.get(failure_category.value, 0) + 1
            stats.failure_counts = counts

        if extractor_used and extraction_ok:
            stats.preferred_extractor = extractor_used
        stats.preferred_strategy = strategy
        if extraction_quality is not None:
            stats.quality_observations += 1
            stats.avg_extraction_quality = _running_average(
                stats.avg_extraction_quality, stats.quality_observations, extraction_quality
            )
        if pagination_detected:
            stats.pagination_detected_count += 1
        if browser_comparison == "browser_superior":
            stats.browser_superior_count += 1
        elif browser_comparison == "http_superior":
            stats.http_superior_count += 1
        elif browser_comparison == "equivalent":
            stats.browser_equivalent_count += 1
        stats.last_observed_at = datetime.now(timezone.utc)

        await self._session.commit()


def _running_average(current_avg: float, count: int, new_value: float) -> float:
    if count <= 1:
        return new_value
    return current_avg + (new_value - current_avg) / count
