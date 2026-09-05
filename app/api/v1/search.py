from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_security_service
from app.core.config import Settings, get_settings
from app.core.errors import APIError
from app.db.repositories.crawl_repository import CrawlRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.session import get_db
from app.schemas.search import (
    InstantSearchRequest,
    InstantSearchResponse,
    SearchHitResponse,
    SearchRequest,
    SearchResponse,
)
from app.services.crawling import registry
from app.services.crawling.crawl_engine import run_crawl
from app.services.search.models import SearchFilters
from app.services.search.search_repository import PostgresSearchRepository
from app.services.security.url_security import URLSecurityError, URLSecurityService

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(body: SearchRequest, db: AsyncSession = Depends(get_db)) -> SearchResponse:
    repo = PostgresSearchRepository(db)
    results = await repo.search(
        SearchFilters(
            query=body.query, domain=body.domain, source_id=body.source_id, language=body.language,
            date_from=body.date_from, date_to=body.date_to, limit=body.limit, offset=body.offset,
        )
    )
    return SearchResponse(
        total=results.total,
        results=[SearchHitResponse(**h.__dict__) for h in results.hits],
    )


@router.post("/instant", response_model=InstantSearchResponse)
async def search_instant(
    body: InstantSearchRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    security: URLSecurityService = Depends(get_security_service),
) -> InstantSearchResponse:
    """Crawl a URL and search the results, without an unbounded wait: starts
    the crawl, waits up to `wait_seconds` for it to progress, then returns
    whatever's indexed so far plus the crawl_id so the caller can fetch the
    rest once it finishes (per spec section 52: 'do not make the user wait
    indefinitely; return a job ID for larger crawls')."""
    try:
        hostname = security.validate_scheme(body.url)
        await security.resolve_and_validate(hostname)
    except URLSecurityError as exc:
        raise APIError(code="URL_BLOCKED", message=str(exc), status_code=status.HTTP_400_BAD_REQUEST) from exc

    crawl_repo = CrawlRepository(db)
    job = await crawl_repo.create_job(
        seed_url=body.url, max_pages=body.max_pages, max_depth=1, same_domain_only=True, crawl_type="instant",
    )
    task = asyncio.create_task(run_crawl(job.id, job.seed_url, settings))
    registry.register(job.id, task)

    # The background crawl task commits through its own sessions; this
    # request's session has `job` cached in its identity map from
    # create_job() and won't see those changes on repeated get_job() calls
    # without forcing a re-read (see get_job's `fresh` docstring).
    deadline = asyncio.get_event_loop().time() + body.wait_seconds
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        refreshed = await crawl_repo.get_job(job.id, fresh=True)
        if refreshed is not None and refreshed.status not in ("queued", "running"):
            break

    final_job = await crawl_repo.get_job(job.id, fresh=True)

    # Search-by-query only makes sense with the full SearchRepository, which
    # filters by domain/source_id/date but has no crawl_id filter (a crawl_id
    # is job-scoped, not document metadata) -- so scope to this crawl's own
    # documents directly and apply the query as a simple substring filter,
    # rather than stretching SearchFilters for a one-off case.
    doc_repo = DocumentRepository(db)
    docs = await doc_repo.list_documents(crawl_job_id=job.id, limit=body.max_pages)
    hits = [
        SearchHitResponse(
            document_id=str(d.id), title=d.title, url=d.url, domain=d.domain,
            snippet=(d.current_content or "")[:240], published_at=d.published_at,
            collected_at=d.collected_at, version=d.current_version, content_hash=d.content_hash,
            relevance=0.0,
        )
        for d in docs
        if not body.query or body.query.lower() in (d.current_content or "").lower()
        or body.query.lower() in (d.title or "").lower()
    ]

    status_value = final_job.status if final_job else "unknown"
    note = (
        "crawl still running; poll GET /crawls/{crawl_id} and re-search for more results"
        if status_value in ("queued", "running")
        else "crawl finished"
    )

    return InstantSearchResponse(
        crawl_id=str(job.id), crawl_status=status_value, total=len(hits), results=hits, note=note,
    )
