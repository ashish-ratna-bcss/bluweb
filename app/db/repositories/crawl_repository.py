from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.crawl import CrawlJob, CrawlPage, CrawlRun, FetchStrategyStats, URLPatternStats
from app.db.models.unified import WebIntelUnified
from app.services.crawling.domain_policy_service import PolicyState, update_policy_after_outcome
from app.services.crawling.failure_classification import FailureCategory

logger = logging.getLogger("webintel.crawl_repository")


def _scalar_one_resilient(result, *, kind: str, key: str):
    """Prefer a single row; if historical duplicates exist, keep the first
    and warn instead of raising MultipleResultsFound (WI-14).

    Cardinality must still be fixed via partial unique indexes + cleanup SQL;
    this only keeps crawls from hard-failing while cleanup is applied.
    """
    rows = list(result.scalars().all())
    if not rows:
        return None
    if len(rows) > 1:
        logger.warning(
            "Duplicate %s rows for %s (count=%s); using id=%s — run docs/wi14_cleanup_domain_stats.sql",
            kind,
            key,
            len(rows),
            getattr(rows[0], "id", None),
        )
    return rows[0]


_DOMAIN_PROFILE_DEFAULTS = {
    "http_attempts": 0, "http_successes": 0, "http_extraction_failures": 0,
    "browser_attempts": 0, "browser_successes": 0,
    "avg_http_latency_ms": 0.0, "avg_browser_latency_ms": 0.0, "avg_content_bytes": 0.0,
    "failure_counts": {}, "success_status_counts": {},
    "js_required_count": 0, "empty_content_count": 0, "preferred_extractor": None,
    "circuit_opened_at": None, "consecutive_failures": 0,
    "sitemap_status": "unknown", "feed_status": "unknown", "sitemap_url_count": 0, "feed_url_count": 0,
    "avg_extraction_quality": 0.0, "quality_observations": 0,
    "browser_superior_count": 0, "http_superior_count": 0, "browser_equivalent_count": 0,
    "avg_completeness": 0.0, "completeness_observations": 0, "index_children_discovered_total": 0,
    "last_observed_at": None,
}

_URL_PATTERN_DEFAULTS = {
    "pattern": None,
    "by_page_type": {},
    "fetch_attempts": 0, "successful_fetches": 0, "extraction_attempts": 0, "extraction_successes": 0,
    "http_attempts": 0, "http_successes": 0, "browser_attempts": 0, "browser_successes": 0,
    "preferred_extractor": None,
    "avg_latency_ms": 0.0, "avg_content_bytes": 0.0, "failure_counts": {},
    "avg_extraction_quality": 0.0, "quality_observations": 0, "pagination_detected_count": 0,
    "browser_superior_count": 0, "http_superior_count": 0, "browser_equivalent_count": 0,
    "last_observed_at": None,
}

_PAGE_TYPE_COUNTER_KEYS = (
    "fetch_attempts", "successful_fetches", "extraction_attempts", "extraction_successes",
    "http_attempts", "http_successes", "browser_attempts", "browser_successes",
    "avg_latency_ms", "avg_content_bytes", "failure_counts",
    "avg_extraction_quality", "quality_observations", "pagination_detected_count",
    "browser_superior_count", "http_superior_count", "browser_equivalent_count",
)

_RUN_STAT_FIELDS = (
    "pages_discovered", "pages_attempted", "pages_fetched", "pages_failed", "pages_extracted",
    "new_documents", "updated_documents", "unchanged_documents",
    "http_pages", "browser_pages", "bytes_downloaded",
)


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
        job_id = uuid.uuid4()
        job = CrawlJob(
            id=job_id,
            crawl_job_id=job_id,
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
        # Merge, don't replace: finalize_run() (called just before this by
        # every caller) already folded duration_ms into crawl_statistics --
        # a full replace here would silently drop it.
        job.statistics = {**(job.statistics or {}), **statistics}
        job.completed_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def request_cancel(self, job: CrawlJob) -> None:
        job.status = "cancelling"
        await self._session.commit()

    async def create_run(self, job_id: uuid.UUID) -> CrawlJob:
        """No separate `crawl_run` row exists in the unified schema (no such
        record_kind) -- one run per job invocation folds entirely onto the
        parent crawl_job row, so this just returns the job itself. Callers
        use `.id` as the run id, which is simply the job id."""
        job = await self.get_job(job_id)
        if job is None:
            raise ValueError(f"crawl job {job_id} not found")
        return job

    async def get_run(self, run_id: uuid.UUID) -> CrawlRun | None:
        job = await self.get_job(run_id)
        return CrawlRun(id=job.id, crawl_job_id=job.id) if job is not None else None

    async def finalize_run(self, run_id: uuid.UUID, stats: dict, duration_ms: float) -> None:
        job = await self.get_job(run_id)
        if job is None:
            return
        for field_name in _RUN_STAT_FIELDS:
            if field_name in stats:
                setattr(job, field_name, stats[field_name])
        job.statistics = {**(job.statistics or {}), "duration_ms": duration_ms}
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
        result = await self._session.execute(
            select(FetchStrategyStats)
            .where(FetchStrategyStats.domain == domain)
            .order_by(FetchStrategyStats.id)
        )
        return _scalar_one_resilient(result, kind="domain_profile", key=domain)

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
        would silently operate on the stale pre-fetch snapshot.

        `record_kind`/`index_where` note: `pg_insert(FetchStrategyStats)` is
        a Core statement against the shared `webintel_unified` table -- it
        does NOT auto-apply the STI polymorphic identity the ORM would on a
        `session.add()` flush, so `record_kind` must be set explicitly here.
        `uq_webintel_domain_profile` is a *partial* unique index
        (`WHERE record_kind='domain_profile'`), so `index_elements` alone
        won't match it without the matching `index_where`.
        """
        insert_stmt = pg_insert(FetchStrategyStats).values(
            id=uuid.uuid4(),
            record_kind="domain_profile",
            domain=domain,
            crawl_delay_ms=0.0,
            recommended_concurrency=5,
            circuit_state="healthy",
            domain_learning=dict(_DOMAIN_PROFILE_DEFAULTS),
        # Use a literal WHERE string matching uq_webintel_domain_profile
        # exactly. A bound `record_kind = $N` predicate does not infer that
        # partial unique index on Postgres (InvalidColumnReferenceError).
        ).on_conflict_do_nothing(
            index_elements=["domain"],
            index_where=text("record_kind = 'domain_profile' AND domain IS NOT NULL"),
        )
        await self._session.execute(insert_stmt)

        result = await self._session.execute(
            select(FetchStrategyStats)
            .where(FetchStrategyStats.domain == domain)
            .order_by(FetchStrategyStats.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stats = _scalar_one_resilient(result, kind="domain_profile", key=domain)
        if stats is None:
            raise RuntimeError(f"domain_profile row missing after upsert for {domain}")
        return stats

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
        server_retry_after_seconds: float | None = None,
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
            counts = dict(stats.failure_counts or {})
            counts[failure_category.value] = counts.get(failure_category.value, 0) + 1
            stats.failure_counts = counts

        if status_code is not None:
            bucket = f"{status_code // 100}xx"
            counts = dict(stats.success_status_counts or {})
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
            server_retry_after_seconds=server_retry_after_seconds,
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
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.sort(key=lambda r: r.last_observed_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return rows

    async def get_pattern_stats_any_type(self, domain: str, pattern: str) -> URLPatternStats | None:
        """Routing lookup before page_type is known. One row per
        (domain, pattern) with `url` holding the clean pattern string
        (UNIQUE(domain, url) WHERE url_pattern). Aggregated counters live
        on the row; per-page_type detail is in domain_learning.by_page_type."""
        result = await self._session.execute(
            select(URLPatternStats)
            .where(URLPatternStats.domain == domain, URLPatternStats.url == pattern)
            .order_by(URLPatternStats.id)
        )
        return _scalar_one_resilient(result, kind="url_pattern", key=f"{domain}|{pattern}")

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
        # One row per (domain, pattern). `url` stores the clean pattern
        # (never a composite like pattern::PAGE_TYPE). Per-page_type
        # counters nest under domain_learning["by_page_type"][page_type];
        # top-level bag counters are rolled-up aggregates for routing.
        effective_page_type = page_type or "UNKNOWN"

        insert_stmt = pg_insert(URLPatternStats).values(
            id=uuid.uuid4(), record_kind="url_pattern", domain=domain, url=pattern,
            page_type=effective_page_type,
            domain_learning={**_URL_PATTERN_DEFAULTS, "pattern": pattern, "by_page_type": {}},
        ).on_conflict_do_nothing(
            index_elements=["domain", "url"],
            index_where=text(
                "record_kind = 'url_pattern' AND domain IS NOT NULL AND url IS NOT NULL"
            ),
        )
        await self._session.execute(insert_stmt)

        result = await self._session.execute(
            select(URLPatternStats)
            .where(URLPatternStats.domain == domain, URLPatternStats.url == pattern)
            .order_by(URLPatternStats.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stats = _scalar_one_resilient(result, kind="url_pattern", key=f"{domain}|{pattern}")
        if stats is None:
            raise RuntimeError(f"url_pattern row missing after upsert for {domain}|{pattern}")

        learning = dict(stats.domain_learning or {})
        by_page_type = dict(learning.get("by_page_type") or {})
        bucket = dict(by_page_type.get(effective_page_type) or {
            k: ({} if k == "failure_counts" else (0.0 if k.startswith("avg_") else 0))
            for k in _PAGE_TYPE_COUNTER_KEYS
        })

        def _bump(key: str, amount: int = 1) -> None:
            bucket[key] = int(bucket.get(key, 0)) + amount

        _bump("fetch_attempts")
        if success:
            _bump("successful_fetches")
            _bump("extraction_attempts")
            if extraction_ok:
                _bump("extraction_successes")

        if strategy == "http":
            _bump("http_attempts")
            if success:
                _bump("http_successes")
        else:
            _bump("browser_attempts")
            if success:
                _bump("browser_successes")

        fetch_n = int(bucket["fetch_attempts"])
        bucket["avg_latency_ms"] = _running_average(float(bucket.get("avg_latency_ms") or 0.0), fetch_n, latency_ms)
        if content_bytes is not None:
            bucket["avg_content_bytes"] = _running_average(
                float(bucket.get("avg_content_bytes") or 0.0), fetch_n, content_bytes
            )

        if failure_category is not None and failure_category != FailureCategory.NONE:
            counts = dict(bucket.get("failure_counts") or {})
            counts[failure_category.value] = counts.get(failure_category.value, 0) + 1
            bucket["failure_counts"] = counts

        if extraction_quality is not None:
            qn = int(bucket.get("quality_observations") or 0) + 1
            bucket["quality_observations"] = qn
            bucket["avg_extraction_quality"] = _running_average(
                float(bucket.get("avg_extraction_quality") or 0.0), qn, extraction_quality
            )
        if pagination_detected:
            _bump("pagination_detected_count")
        if browser_comparison == "browser_superior":
            _bump("browser_superior_count")
        elif browser_comparison == "http_superior":
            _bump("http_superior_count")
        elif browser_comparison == "equivalent":
            _bump("browser_equivalent_count")

        by_page_type[effective_page_type] = bucket
        learning["by_page_type"] = by_page_type
        learning["pattern"] = pattern
        stats.domain_learning = learning

        # Roll up aggregates onto top-level bag properties (routing + profile API).
        _rollup_pattern_aggregates(stats, by_page_type)

        stats.page_type = effective_page_type
        if extractor_used and extraction_ok:
            stats.preferred_extractor = extractor_used
        stats.preferred_strategy = strategy
        stats.last_observed_at = datetime.now(timezone.utc)

        await self._session.commit()


def _rollup_pattern_aggregates(stats: URLPatternStats, by_page_type: dict) -> None:
    """Recompute top-level URLPatternStats bag counters from per-page-type buckets."""
    totals = {k: ({} if k == "failure_counts" else 0) for k in _PAGE_TYPE_COUNTER_KEYS if k != "avg_latency_ms" and k != "avg_content_bytes" and k != "avg_extraction_quality"}
    latency_weight = 0
    latency_sum = 0.0
    bytes_weight = 0
    bytes_sum = 0.0
    quality_weight = 0
    quality_sum = 0.0
    failure_counts: dict = {}

    for bucket in by_page_type.values():
        for key in (
            "fetch_attempts", "successful_fetches", "extraction_attempts", "extraction_successes",
            "http_attempts", "http_successes", "browser_attempts", "browser_successes",
            "pagination_detected_count", "browser_superior_count", "http_superior_count",
            "browser_equivalent_count", "quality_observations",
        ):
            totals[key] = int(totals.get(key, 0)) + int(bucket.get(key, 0))
        fa = int(bucket.get("fetch_attempts") or 0)
        if fa:
            latency_weight += fa
            latency_sum += float(bucket.get("avg_latency_ms") or 0.0) * fa
            bytes_weight += fa
            bytes_sum += float(bucket.get("avg_content_bytes") or 0.0) * fa
        qn = int(bucket.get("quality_observations") or 0)
        if qn:
            quality_weight += qn
            quality_sum += float(bucket.get("avg_extraction_quality") or 0.0) * qn
        for k, v in (bucket.get("failure_counts") or {}).items():
            failure_counts[k] = failure_counts.get(k, 0) + int(v)

    for key, value in totals.items():
        setattr(stats, key, value)
    stats.failure_counts = failure_counts
    stats.avg_latency_ms = (latency_sum / latency_weight) if latency_weight else 0.0
    stats.avg_content_bytes = (bytes_sum / bytes_weight) if bytes_weight else 0.0
    stats.avg_extraction_quality = (quality_sum / quality_weight) if quality_weight else 0.0


def _running_average(current_avg: float, count: int, new_value: float) -> float:
    if count <= 1:
        return new_value
    return current_avg + (new_value - current_avg) / count
