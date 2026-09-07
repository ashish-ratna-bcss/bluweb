from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_security_service
from app.core.errors import APIError
from app.db.models.source import Source
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.preflight_repository import PreflightRepository
from app.db.repositories.source_repository import SourceRepository
from app.db.session import get_db
from app.schemas.source import (
    MonitoringEventResponse,
    SourceCreateRequest,
    SourceResponse,
    SourceStatisticsResponse,
    SourceUpdateRequest,
)
from app.services.normalization.url_normalizer import extract_domain, normalize_url
from app.services.security.url_security import URLSecurityError, URLSecurityService

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreateRequest,
    db: AsyncSession = Depends(get_db),
    security: URLSecurityService = Depends(get_security_service),
) -> SourceResponse:
    try:
        security.validate_scheme(body.url)
    except URLSecurityError as exc:
        from app.core.url_errors import url_security_to_api_error

        raise url_security_to_api_error(exc) from exc

    preflight_repo = PreflightRepository(db)
    preflight = await preflight_repo.get(body.preflight_id)
    if preflight is None:
        raise APIError(
            code="PREFLIGHT_NOT_FOUND", message=f"No pre-flight report found for id {body.preflight_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if preflight.expires_at < datetime.now(timezone.utc):
        raise APIError(
            code="PREFLIGHT_EXPIRED",
            message="This pre-flight report has expired; run a fresh pre-flight before creating a source",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if extract_domain(preflight.url) != extract_domain(body.url):
        raise APIError(
            code="PREFLIGHT_URL_MISMATCH",
            message="The pre-flight report's URL domain doesn't match the source URL",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    normalized = normalize_url(body.url)
    source_repo = SourceRepository(db)
    if await source_repo.find_by_normalized_url(normalized) is not None:
        raise APIError(
            code="SOURCE_ALREADY_EXISTS", message="A source for this URL already exists",
            status_code=status.HTTP_409_CONFLICT,
        )

    source = await source_repo.create(
        name=body.name,
        base_url=body.url,
        normalized_url=normalized,
        domain=extract_domain(body.url),
        source_type=body.source_type,
        preflight_id=body.preflight_id,
        crawl_policy=body.crawl_policy.model_dump(exclude_none=True) if body.crawl_policy else None,
        min_interval_seconds=body.interval_seconds,
        max_interval_seconds=body.max_interval_seconds,
    )
    return _to_response(source)


@router.get("", response_model=list[SourceResponse])
async def list_sources(db: AsyncSession = Depends(get_db)) -> list[SourceResponse]:
    source_repo = SourceRepository(db)
    sources = await source_repo.list()
    return [_to_response(s) for s in sources]


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> SourceResponse:
    source = await _get_or_404(source_id, db)
    return _to_response(source)


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID, body: SourceUpdateRequest, db: AsyncSession = Depends(get_db)
) -> SourceResponse:
    source = await _get_or_404(source_id, db)
    source_repo = SourceRepository(db)
    patch = {
        "name": body.name,
        "source_type": body.source_type,
        "crawl_policy": body.crawl_policy.model_dump(exclude_none=True) if body.crawl_policy else None,
    }
    source = await source_repo.update_policy(source, patch)
    return _to_response(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    source = await _get_or_404(source_id, db)
    await SourceRepository(db).delete(source)


@router.post("/{source_id}/start", response_model=SourceResponse)
@router.post("/{source_id}/resume", response_model=SourceResponse)
async def start_or_resume_source(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> SourceResponse:
    source = await _get_or_404(source_id, db)
    source = await SourceRepository(db).set_status(source, "active")
    return _to_response(source)


@router.post("/{source_id}/pause", response_model=SourceResponse)
async def pause_source(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> SourceResponse:
    source = await _get_or_404(source_id, db)
    source = await SourceRepository(db).set_status(source, "paused")
    return _to_response(source)


@router.get("/{source_id}/events", response_model=list[MonitoringEventResponse])
async def list_source_events(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[MonitoringEventResponse]:
    await _get_or_404(source_id, db)
    events = await SourceRepository(db).list_events(source_id)
    return [
        MonitoringEventResponse(
            event_id=e.id, document_id=e.document_id, event_type=e.event_type,
            previous_version=e.previous_version, new_version=e.new_version,
            detected_at=e.detected_at, change_summary=e.change_summary,
        )
        for e in events
    ]


@router.get("/{source_id}/statistics", response_model=SourceStatisticsResponse)
async def get_source_statistics(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> SourceStatisticsResponse:
    source = await _get_or_404(source_id, db)
    source_repo = SourceRepository(db)
    doc_repo = DocumentRepository(db)
    return SourceStatisticsResponse(
        source_id=source.id,
        status=source.status or "paused",
        current_interval_seconds=int(source.current_interval_seconds or source.min_interval_seconds or 900),
        consecutive_unchanged_crawls=int(source.consecutive_unchanged_crawls or 0),
        last_crawl_at=source.last_crawl_at,
        next_crawl_at=source.next_crawl_at,
        total_documents=await doc_repo.count_for_source(source_id),
        total_events=await source_repo.count_events(source_id),
    )


async def _get_or_404(source_id: uuid.UUID, db: AsyncSession) -> Source:
    source = await SourceRepository(db).get(source_id)
    if source is None:
        raise APIError(
            code="SOURCE_NOT_FOUND", message=f"No source found for id {source_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return source


def _to_response(source: Source) -> SourceResponse:
    return SourceResponse(
        source_id=source.id, name=source.name, base_url=source.base_url, domain=source.domain,
        source_type=source.source_type, status=source.status, crawl_policy=source.crawl_policy,
        min_interval_seconds=source.min_interval_seconds, max_interval_seconds=source.max_interval_seconds,
        current_interval_seconds=source.current_interval_seconds,
        created_at=source.created_at, updated_at=source.updated_at,
        last_crawl_at=source.last_crawl_at, next_crawl_at=source.next_crawl_at,
    )
