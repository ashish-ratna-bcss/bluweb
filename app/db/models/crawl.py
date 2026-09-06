"""`crawl_job` / `crawl_page` / `domain_profile` / `url_pattern` kinds.

See app/db/models/unified.py for the shared column set, the STI rationale,
and the JSONB mutation rule. Renames/column-repurposing decisions specific
to this file:

- `CrawlJob`: DDL has no dedicated `started_at`/`completed_at` columns --
  reused the generic, otherwise-idle-for-this-kind `first_seen_at`/
  `last_seen_at`. `.status` is a `hybrid_property` (not a plain property)
  because `CrawlRepository.get_active_job_for_source` filters
  `.where(CrawlJob.status.in_(...))` at the class level.
- `CrawlRun` no longer has its own row at all (no `crawl_run` record_kind
  in the DDL) -- it's folded entirely into its parent `crawl_job` row by
  `CrawlRepository` (one run per job invocation, verified 1:1 in
  crawl_engine.py). Kept here as an unused dataclass purely so
  `app/db/models/__init__.py`'s existing re-export list doesn't change.
- `CrawlPage`: no dedicated `discovered_at`/`fetched_at` columns --
  `discovered_at` aliases the generic `created_at` (`hybrid_property`,
  since `CrawlRepository.list_pages` orders by
  `CrawlPage.discovered_at` at the class level); `fetched_at` is a
  read-only computed property (old code always set both to the same
  instant on a fetched/failed page, never independently).
- `FetchStrategyStats`/`URLPatternStats`: only `crawl_delay_ms`,
  `recommended_concurrency`, `circuit_state`, `preferred_strategy`,
  `domain`, `page_type` have real dense columns in the DDL. Every other
  counter (~30 for FetchStrategyStats, ~15 for URLPatternStats) is a
  property pair backed by one shared `domain_learning` JSONB dict --
  built with the `_bag_property`/`_bag_datetime_property` factories below.
  Datetime-valued counters (`circuit_opened_at`, `last_observed_at`) are
  stored as ISO strings inside the JSONB (Postgres JSONB can't hold a raw
  Python `datetime`) and parsed back on read.
- `URLPatternStats` uniqueness: DDL `UNIQUE(domain, url)` WHERE
  `url_pattern`. The clean URL-pattern string is stored in `url` (never a
  composite like `pattern::PAGE_TYPE`). Per-page_type counters live under
  `domain_learning["by_page_type"][page_type]`; top-level bag counters are
  rolled-up aggregates used for routing. `.pattern` mirrors `url` for the
  domains API display path.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.hybrid import hybrid_property

from app.db.models.unified import WebIntelUnified


def _bag_property(key: str, default=0):
    """Instance-level get/set of one key inside `domain_learning`. Never
    used in a class-level query expression anywhere in this codebase, so a
    plain `property` (not `hybrid_property`) is correct here."""

    def getter(self):
        return self._learning().get(key, default)

    def setter(self, value) -> None:
        self._set_learning(key, value)

    return property(getter, setter)


def _bag_datetime_property(key: str):
    def getter(self) -> datetime | None:
        raw = self._learning().get(key)
        return datetime.fromisoformat(raw) if raw else None

    def setter(self, value: datetime | None) -> None:
        self._set_learning(key, value.isoformat() if value is not None else None)

    return property(getter, setter)


class _DomainLearningMixin:
    """Shared read-modify-write helpers for the `domain_learning` JSONB bag.
    Reassigns a new dict on every write per the JSONB mutation rule -- never
    mutates the existing dict in place."""

    def _learning(self) -> dict:
        return self.domain_learning or {}

    def _set_learning(self, key: str, value) -> None:
        self.domain_learning = {**self._learning(), key: value}


class CrawlJob(WebIntelUnified):
    __mapper_args__ = {"polymorphic_identity": "crawl_job"}

    @property
    def seed_url(self) -> str | None:
        return self.url

    @seed_url.setter
    def seed_url(self, value: str | None) -> None:
        self.url = value

    @property
    def priority(self) -> int | None:
        return self.crawl_priority

    @priority.setter
    def priority(self, value: int | None) -> None:
        self.crawl_priority = value

    @property
    def statistics(self) -> dict | None:
        return self.crawl_statistics

    @statistics.setter
    def statistics(self, value: dict | None) -> None:
        self.crawl_statistics = value or {}

    @property
    def error(self) -> str | None:
        return self.crawl_error

    @error.setter
    def error(self, value: str | None) -> None:
        self.crawl_error = value

    @property
    def started_at(self) -> datetime | None:
        return self.first_seen_at

    @started_at.setter
    def started_at(self, value: datetime | None) -> None:
        self.first_seen_at = value

    @property
    def completed_at(self) -> datetime | None:
        return self.last_seen_at

    @completed_at.setter
    def completed_at(self, value: datetime | None) -> None:
        self.last_seen_at = value

    @hybrid_property
    def status(self) -> str | None:
        return self.crawl_status

    @status.setter
    def status(self, value: str | None) -> None:
        self.crawl_status = value

    @status.expression
    def status(cls):
        return cls.crawl_status


@dataclass
class CrawlRun:
    """No longer a real row (no `crawl_run` record_kind in the DDL) -- its
    counters live on the parent `crawl_job` row instead. Kept only so
    `app/db/models/__init__.py` doesn't need to drop the name; never
    instantiated."""

    id: uuid.UUID
    crawl_job_id: uuid.UUID


class CrawlPage(WebIntelUnified):
    __mapper_args__ = {"polymorphic_identity": "crawl_page"}

    @property
    def depth(self) -> int | None:
        return self.crawl_depth

    @depth.setter
    def depth(self, value: int | None) -> None:
        self.crawl_depth = value

    @property
    def status(self) -> str | None:
        return self.crawl_page_status

    @status.setter
    def status(self, value: str | None) -> None:
        self.crawl_page_status = value

    @property
    def error(self) -> str | None:
        return self.fetch_error

    @error.setter
    def error(self, value: str | None) -> None:
        self.fetch_error = value

    @hybrid_property
    def discovered_at(self) -> datetime | None:
        return self.created_at

    @discovered_at.expression
    def discovered_at(cls):
        return cls.created_at

    @property
    def fetched_at(self) -> datetime | None:
        # Old code always set discovered_at == fetched_at on a fetched/failed
        # page in the same INSERT -- nothing to store separately.
        return self.created_at if self.crawl_page_status in ("fetched", "failed") else None


class FetchStrategyStats(_DomainLearningMixin, WebIntelUnified):
    """AKA the domain profile. `domain`, `crawl_delay_ms`,
    `recommended_concurrency`, `circuit_state` are real dense columns;
    everything else lives in `domain_learning` (see module docstring)."""

    __mapper_args__ = {"polymorphic_identity": "domain_profile"}

    http_attempts = _bag_property("http_attempts", 0)
    http_successes = _bag_property("http_successes", 0)
    http_extraction_failures = _bag_property("http_extraction_failures", 0)
    browser_attempts = _bag_property("browser_attempts", 0)
    browser_successes = _bag_property("browser_successes", 0)

    avg_http_latency_ms = _bag_property("avg_http_latency_ms", 0.0)
    avg_browser_latency_ms = _bag_property("avg_browser_latency_ms", 0.0)
    avg_content_bytes = _bag_property("avg_content_bytes", 0.0)

    failure_counts = _bag_property("failure_counts", None)
    success_status_counts = _bag_property("success_status_counts", None)

    js_required_count = _bag_property("js_required_count", 0)
    empty_content_count = _bag_property("empty_content_count", 0)
    preferred_extractor = _bag_property("preferred_extractor", None)

    circuit_opened_at = _bag_datetime_property("circuit_opened_at")
    consecutive_failures = _bag_property("consecutive_failures", 0)
    last_observed_at = _bag_datetime_property("last_observed_at")

    sitemap_status = _bag_property("sitemap_status", "unknown")
    feed_status = _bag_property("feed_status", "unknown")
    sitemap_url_count = _bag_property("sitemap_url_count", 0)
    feed_url_count = _bag_property("feed_url_count", 0)
    avg_extraction_quality = _bag_property("avg_extraction_quality", 0.0)
    quality_observations = _bag_property("quality_observations", 0)

    browser_superior_count = _bag_property("browser_superior_count", 0)
    http_superior_count = _bag_property("http_superior_count", 0)
    browser_equivalent_count = _bag_property("browser_equivalent_count", 0)
    avg_completeness = _bag_property("avg_completeness", 0.0)
    completeness_observations = _bag_property("completeness_observations", 0)
    index_children_discovered_total = _bag_property("index_children_discovered_total", 0)


class URLPatternStats(_DomainLearningMixin, WebIntelUnified):
    """Per (domain, pattern) crawl history. `url` holds the clean pattern
    string (UNIQUE with domain). `page_type` is the last-observed type.
    Aggregated counters are bag properties; per-type detail is in
    `domain_learning["by_page_type"]`."""

    __mapper_args__ = {"polymorphic_identity": "url_pattern"}

    @property
    def pattern(self) -> str | None:
        # Prefer the dense `url` column (authoritative unique key); fall back
        # to the JSONB mirror for any legacy rows.
        return self.url or self._learning().get("pattern")

    @pattern.setter
    def pattern(self, value: str | None) -> None:
        self.url = value
        self._set_learning("pattern", value)

    def page_type_buckets(self) -> dict:
        return dict((self.domain_learning or {}).get("by_page_type") or {})

    fetch_attempts = _bag_property("fetch_attempts", 0)
    successful_fetches = _bag_property("successful_fetches", 0)
    extraction_attempts = _bag_property("extraction_attempts", 0)
    extraction_successes = _bag_property("extraction_successes", 0)

    http_attempts = _bag_property("http_attempts", 0)
    http_successes = _bag_property("http_successes", 0)
    browser_attempts = _bag_property("browser_attempts", 0)
    browser_successes = _bag_property("browser_successes", 0)

    preferred_extractor = _bag_property("preferred_extractor", None)

    avg_latency_ms = _bag_property("avg_latency_ms", 0.0)
    avg_content_bytes = _bag_property("avg_content_bytes", 0.0)
    failure_counts = _bag_property("failure_counts", None)

    avg_extraction_quality = _bag_property("avg_extraction_quality", 0.0)
    quality_observations = _bag_property("quality_observations", 0)
    pagination_detected_count = _bag_property("pagination_detected_count", 0)
    browser_superior_count = _bag_property("browser_superior_count", 0)
    http_superior_count = _bag_property("http_superior_count", 0)
    browser_equivalent_count = _bag_property("browser_equivalent_count", 0)

    last_observed_at = _bag_datetime_property("last_observed_at")
