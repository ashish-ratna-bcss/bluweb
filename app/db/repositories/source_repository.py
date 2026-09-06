from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
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
        source_id = uuid.uuid4()
        source = Source(
            id=source_id,
            source_id=source_id,
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
    #
    # No standalone row/table (or unique constraint) exists for these
    # anymore -- `source_urls` is a JSONB list on the parent `source` row.
    # Two different URLs of the same source now contend for that one row
    # (they didn't when each had its own row), so the upsert methods below
    # lock the source row first -- a lock the old per-row design never
    # needed.

    async def get_source_url(self, source_id: uuid.UUID, normalized_url: str) -> SourceUrl | None:
        source = await self.get(source_id)
        if source is None:
            return None
        for entry in source.source_urls or []:
            if entry.get("normalized_url") == normalized_url:
                return _source_url_from_entry(entry)
        return None

    async def _get_source_locked(self, source_id: uuid.UUID) -> Source:
        result = await self._session.execute(
            select(Source).where(Source.id == source_id).with_for_update().execution_options(populate_existing=True)
        )
        return result.scalar_one()

    async def upsert_source_url_success(
        self, source_id: uuid.UUID, url: str, normalized_url: str, document_id: uuid.UUID | None
    ) -> tuple[SourceUrl, bool]:
        """Returns (row, was_restored). was_restored is True exactly on the
        removed -> active transition (spec Phase 7 section 24) -- a
        brand-new URL's first successful crawl is NOT a restoration, and
        neither is a normal already-active crawl."""
        source = await self._get_source_locked(source_id)
        now = datetime.now(timezone.utc)
        entries = source.source_urls or []
        idx = next((i for i, e in enumerate(entries) if e.get("normalized_url") == normalized_url), None)

        if idx is None:
            entry = {
                "id": str(uuid.uuid4()), "source_id": str(source_id), "url": url, "normalized_url": normalized_url,
                "status": "active", "consecutive_failures": 0, "last_failure_category": None,
                "document_id": str(document_id) if document_id else None,
                "first_seen": now.isoformat(), "last_seen": now.isoformat(), "last_crawled_at": now.isoformat(),
            }
            source.source_urls = [*entries, entry]
            await self._session.commit()
            return _source_url_from_entry(entry), False

        entry = dict(entries[idx])
        was_removed = entry.get("status") == "removed"
        entry["status"] = "active"
        entry["consecutive_failures"] = 0
        entry["document_id"] = str(document_id) if document_id else entry.get("document_id")
        entry["last_seen"] = now.isoformat()
        entry["last_crawled_at"] = now.isoformat()
        new_entries = list(entries)
        new_entries[idx] = entry
        source.source_urls = new_entries
        await self._session.commit()
        return _source_url_from_entry(entry), was_removed

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
        source = await self._get_source_locked(source_id)
        now = datetime.now(timezone.utc)
        eligible = is_removal_eligible(category)
        entries = source.source_urls or []
        idx = next((i for i, e in enumerate(entries) if e.get("normalized_url") == normalized_url), None)

        if idx is None:
            entry = {
                "id": str(uuid.uuid4()), "source_id": str(source_id), "url": url, "normalized_url": normalized_url,
                "status": "active", "consecutive_failures": 1 if eligible else 0,
                "last_failure_category": category.value, "document_id": None,
                "first_seen": now.isoformat(), "last_seen": now.isoformat(), "last_crawled_at": now.isoformat(),
            }
            source.source_urls = [*entries, entry]
            await self._session.commit()
            return _source_url_from_entry(entry), False

        entry = dict(entries[idx])
        entry["last_failure_category"] = category.value
        entry["last_crawled_at"] = now.isoformat()
        just_removed = False
        if eligible:
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            if entry["consecutive_failures"] >= removal_threshold and entry.get("status") != "removed":
                entry["status"] = "removed"
                just_removed = True
        new_entries = list(entries)
        new_entries[idx] = entry
        source.source_urls = new_entries
        await self._session.commit()
        return _source_url_from_entry(entry), just_removed

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
        source = await self._get_source_locked(source_id)
        entry = {
            "id": str(uuid.uuid4()), "source_id": str(source_id),
            "document_id": str(document_id) if document_id else None, "event_type": event_type,
            "previous_version": previous_version, "new_version": new_version, "change_summary": change_summary,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        source.monitoring_events = [*(source.monitoring_events or []), entry]
        await self._session.commit()
        return _monitoring_event_from_entry(entry)

    async def list_events(self, source_id: uuid.UUID, *, limit: int = 100) -> list[MonitoringEvent]:
        source = await self.get(source_id)
        if source is None:
            return []
        entries = sorted(source.monitoring_events or [], key=lambda e: e.get("detected_at") or "", reverse=True)
        return [_monitoring_event_from_entry(e) for e in entries[:limit]]

    async def count_events(self, source_id: uuid.UUID) -> int:
        source = await self.get(source_id)
        return len(source.monitoring_events or []) if source is not None else 0


def _source_url_from_entry(entry: dict) -> SourceUrl:
    return SourceUrl(
        id=uuid.UUID(entry["id"]), source_id=uuid.UUID(entry["source_id"]), url=entry["url"],
        normalized_url=entry["normalized_url"], status=entry.get("status", "active"),
        consecutive_failures=entry.get("consecutive_failures", 0),
        last_failure_category=entry.get("last_failure_category"),
        document_id=uuid.UUID(entry["document_id"]) if entry.get("document_id") else None,
        first_seen=datetime.fromisoformat(entry["first_seen"]) if entry.get("first_seen") else None,
        last_seen=datetime.fromisoformat(entry["last_seen"]) if entry.get("last_seen") else None,
        last_crawled_at=datetime.fromisoformat(entry["last_crawled_at"]) if entry.get("last_crawled_at") else None,
    )


def _monitoring_event_from_entry(entry: dict) -> MonitoringEvent:
    return MonitoringEvent(
        id=uuid.UUID(entry["id"]), source_id=uuid.UUID(entry["source_id"]), event_type=entry["event_type"],
        document_id=uuid.UUID(entry["document_id"]) if entry.get("document_id") else None,
        previous_version=entry.get("previous_version"), new_version=entry.get("new_version"),
        change_summary=entry.get("change_summary"),
        detected_at=datetime.fromisoformat(entry["detected_at"]) if entry.get("detected_at") else None,
    )
