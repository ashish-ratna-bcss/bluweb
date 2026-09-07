from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_security_service
from app.core.config import Settings, get_settings
from app.core.errors import APIError
from app.db.models.crawl import CrawlJob
from app.db.repositories.crawl_repository import CrawlRepository
from app.db.session import get_db
from app.schemas.crawl import CrawlJobResponse, CrawlPageResponse, CrawlRequest
from app.services.crawling import registry
from app.services.crawling.crawl_engine import run_crawl
from app.services.discovery.seed_discovery import discover_extra_seeds, persist_discovery_outcome
from app.services.security.url_security import URLSecurityError, URLSecurityService

router = APIRouter(prefix="/crawls", tags=["crawls"])


@router.post("", response_model=CrawlJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_crawl(
    body: CrawlRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    security: URLSecurityService = Depends(get_security_service),
) -> CrawlJobResponse:
    try:
        hostname = security.validate_scheme(body.url)
        await security.resolve_and_validate(hostname)
    except URLSecurityError as exc:
        from app.core.url_errors import url_security_to_api_error

        raise url_security_to_api_error(exc) from exc

    repo = CrawlRepository(db)
    job = await repo.create_job(
        seed_url=body.url,
        max_pages=body.max_pages or settings.crawl_default_max_pages,
        max_depth=body.max_depth if body.max_depth is not None else settings.crawl_default_max_depth,
        same_domain_only=body.same_domain_only,
        crawl_type="instant",
    )

    task = asyncio.create_task(_run_instant_crawl(job.id, job.seed_url, settings, security))
    registry.register(job.id, task)

    return _job_to_response(job)


async def _run_instant_crawl(job_id: uuid.UUID, seed_url: str, settings: Settings, security: URLSecurityService) -> None:
    """Instant crawls get the same sitemap/feed seed discovery monitored
    recrawls already use (spec Phase 9 sections 27/28: "reuse run_crawl",
    not a separate discovery path) -- run inline in the background task, not
    before the 202 response, so discovery latency never delays the API
    reply. Best-effort: discover_extra_seeds already swallows its own
    failures and falls back to an empty list."""
    discovery = await discover_extra_seeds(seed_url, settings, security)
    await persist_discovery_outcome(seed_url, discovery)
    await run_crawl(job_id, seed_url, settings, extra_seed_urls=discovery.urls)


@router.get("", response_model=list[CrawlJobResponse])
async def list_crawls(db: AsyncSession = Depends(get_db)) -> list[CrawlJobResponse]:
    repo = CrawlRepository(db)
    jobs = await repo.list_jobs()
    return [_job_to_response(job) for job in jobs]


@router.get("/{crawl_id}", response_model=CrawlJobResponse)
async def get_crawl(crawl_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> CrawlJobResponse:
    repo = CrawlRepository(db)
    job = await repo.get_job(crawl_id)
    if job is None:
        raise APIError(
            code="CRAWL_NOT_FOUND", message=f"No crawl job found for id {crawl_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _job_to_response(job)


@router.get("/{crawl_id}/pages", response_model=list[CrawlPageResponse])
async def list_crawl_pages(crawl_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[CrawlPageResponse]:
    repo = CrawlRepository(db)
    job = await repo.get_job(crawl_id)
    if job is None:
        raise APIError(
            code="CRAWL_NOT_FOUND", message=f"No crawl job found for id {crawl_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    pages = await repo.list_pages(crawl_id)
    return [CrawlPageResponse.model_validate(p) for p in pages]


@router.post("/{crawl_id}/cancel", response_model=CrawlJobResponse)
async def cancel_crawl(crawl_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> CrawlJobResponse:
    repo = CrawlRepository(db)
    job = await repo.get_job(crawl_id)
    if job is None:
        raise APIError(
            code="CRAWL_NOT_FOUND", message=f"No crawl job found for id {crawl_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if job.status in ("queued", "running"):
        await repo.request_cancel(job)
        registry.cancel(crawl_id)
    return _job_to_response(job)


def _job_to_response(job: CrawlJob) -> CrawlJobResponse:
    return CrawlJobResponse(
        crawl_id=job.id,
        status=job.status,
        seed_url=job.seed_url,
        max_pages=job.max_pages,
        max_depth=job.max_depth,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=job.error,
        statistics=job.statistics,
    )
