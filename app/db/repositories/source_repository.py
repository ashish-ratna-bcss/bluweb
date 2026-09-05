from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.source import DEFAULT_CRAWL_POLICY, MonitoringEvent, Source, SourceUrl
from app.services.crawling.failure_classification import FailureCategory, is_removal_eligible


def compute_next_interval(
    *, current_interval_seconds: int, min_interval_seconds: int, max_interval_seconds: int, had_changes: bool
) -> int:
    """Pure function so the adaptive-scheduling math is testable without a
    database: any change snaps the interval back to the floor; an unchanged
    crawl doubles it, capped at the ceiling."""
    if had_changes:
        return min_interval_seconds
    return min(current_interval_seconds * 2, max_interval_seconds)


class SourceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        normalized_url: str,
        domain: str,
        source_type: str,
        preflight_id: uuid.UUID | None,
        crawl_policy: dict | None,
        min_interval_seconds: int,
        max_interval_seconds: int,
    ) -> Source:
        source = Source(
            id=uuid.uuid4(),
            name=name,
            base_url=base_url,
            normalized_url=normalized_url,
            domain=domain,
            source_type=source_type,
            status="paused",
            preflight_id=preflight_id,
            crawl_policy={**DEFAULT_CRAWL_POLICY, **(crawl_policy or {})},
            min_interval_seconds=min_interval_seconds,
            max_interval_seconds=max_interval_seconds,
            current_interval_seconds=min_interval_seconds,
        )
        self._session.add(source)
        await self._session.commit()
        await self._session.refresh(source)
        return source

    async def get(self, source_id: uuid.UUID) -> Source | None:
        result = await self._session.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one_or_none()

    async def find_by_normalized_url(self, normalized_url: str) -> Source | None:
        result = await self._session.execute(select(Source).where(Source.normalized_url == normalized_url))
        return result.scalar_one_or_none()

    async def list(self, *, status_filter: str | None = None, limit: int = 100) -> list[Source]:
        stmt = select(Source).order_by(Source.created_at.desc()).limit(limit)
        if status_filter:
            stmt = stmt.where(Source.status == status_filter)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_due(self, *, now: datetime, limit: int = 50) -> list[Source]:
        result = await self._session.execute(
            select(Source)
            .where(Source.status == "active")
            .where((Source.next_crawl_at.is_(None)) | (Source.next_crawl_at <= now))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_policy(self, source: Source, patch: dict) -> Source:
        for key in ("name", "status", "source_type"):
            if key in patch and patch[key] is not None:
                setattr(source, key, patch[key])
        if "crawl_policy" in patch and patch["crawl_policy"] is not None:
            source.crawl_policy = {**source.crawl_policy, **patch["crawl_policy"]}
        await self._session.commit()
        await self._session.refresh(source)
        return source

    async def set_status(self, source: Source, status_value: str) -> Source:
        source.status = status_value
        if status_value == "active" and source.next_crawl_at is None:
            source.next_crawl_at = datetime.now(timezone.utc)
        await self._session.commit()
        await self._session.refresh(source)
        return source

    async def delete(self, source: Source) -> None:
        await self._session.delete(source)
        await self._session.commit()

    async def mark_crawled_and_reschedule(self, source: Source, *, had_changes: bool) -> None:
        """Adaptive re-crawl interval (spec section 42): unchanged crawls
        stretch the interval toward max_interval_seconds; any change snaps
        it back to min_interval_seconds. Deterministic, no learned model."""
        now = datetime.now(timezone.utc)
        source.last_crawl_at = now
        source.consecutive_unchanged_crawls = (
            0 if had_changes else source.consecutive_unchanged_crawls + 1
        )
        source.current_interval_seconds = compute_next_interval(
            current_interval_seconds=source.current_interval_seconds,
            min_interval_seconds=source.min_interval_seconds,
            max_interval_seconds=source.max_interval_seconds,
            had_changes=had_changes,
        )
        source.next_crawl_at = now + timedelta(seconds=source.current_interval_seconds)
        await self._session.commit()

    # -- source_urls (per-URL state for REMOVED detection) --

    async def get_source_url(self, source_id: uuid.UUID, normalized_url: str) -> SourceUrl | None:
        result = await self._session.execute(
            select(SourceUrl).where(SourceUrl.source_id == source_id, SourceUrl.normalized_url == normalized_url)
        )
        return result.scalar_one_or_none()

    async def upsert_source_url_success(
        self, source_id: uuid.UUID, url: str, normalized_url: str, document_id: uuid.UUID | None
    ) -> tuple[SourceUrl, bool]:
        """Returns (row, was_restored). was_restored is True exactly on the
        removed -> active transition (spec Phase 7 section 24) -- a
        brand-new URL's first successful crawl is NOT a restoration, and
        neither is a normal already-active crawl."""
        existing = await self.get_source_url(source_id, normalized_url)
        now = datetime.now(timezone.utc)
        if existing is None:
            existing = SourceUrl(
                id=uuid.uuid4(), source_id=source_id, url=url, normalized_url=normalized_url,
                status="active", consecutive_failures=0, document_id=document_id,
                last_crawled_at=now,
            )
            self._session.add(existing)
            await self._session.commit()
            return existing, False

        was_removed = existing.status == "removed"
        existing.status = "active"
        existing.consecutive_failures = 0
        existing.document_id = document_id or existing.document_id
        existing.last_crawled_at = now
        await self._session.commit()
        return existing, was_removed

    async def record_source_url_failure(
        self,
        source_id: uuid.UUID,
        url: str,
        normalized_url: str,
        *,
        removal_threshold: int,
        category: FailureCategory = FailureCategory.OTHER_HTTP_ERROR,
    ) -> tuple[SourceUrl, bool]:
        """Returns (row, just_marked_removed).

        A blocked/throttled failure (403/429/robots) is recorded -- callers
        may still want it for observability -- but never advances the
        consecutive-failure counter or triggers REMOVED: being blocked says
        nothing about whether the content is still there (spec Phase L/M).
        """
        existing = await self.get_source_url(source_id, normalized_url)
        now = datetime.now(timezone.utc)
        eligible = is_removal_eligible(category)

        if existing is None:
            existing = SourceUrl(
                id=uuid.uuid4(), source_id=source_id, url=url, normalized_url=normalized_url,
                status="active", consecutive_failures=1 if eligible else 0,
                last_failure_category=category.value, last_crawled_at=now,
            )
            self._session.add(existing)
            await self._session.commit()
            return existing, False

        existing.last_failure_category = category.value
        existing.last_crawled_at = now
        just_removed = False
        if eligible:
            existing.consecutive_failures += 1
            if existing.consecutive_failures >= removal_threshold and existing.status != "removed":
                existing.status = "removed"
                just_removed = True
        await self._session.commit()
        return existing, just_removed

    # -- monitoring events --

    async def add_event(
        self,
        *,
        source_id: uuid.UUID,
        document_id: uuid.UUID | None,
        event_type: str,
        previous_version: int | None = None,
        new_version: int | None = None,
        change_summary: dict | None = None,
    ) -> MonitoringEvent:
        event = MonitoringEvent(
            id=uuid.uuid4(), source_id=source_id, document_id=document_id, event_type=event_type,
            previous_version=previous_version, new_version=new_version, change_summary=change_summary,
        )
        self._session.add(event)
        await self._session.commit()
        return event

    async def list_events(self, source_id: uuid.UUID, *, limit: int = 100) -> list[MonitoringEvent]:
        result = await self._session.execute(
            select(MonitoringEvent)
            .where(MonitoringEvent.source_id == source_id)
            .order_by(MonitoringEvent.detected_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_events(self, source_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(MonitoringEvent).where(MonitoringEvent.source_id == source_id)
        )
        return result.scalar_one()
