"""In-process monitoring scheduler.

ponytail: runs as an asyncio background task inside the API process,
same rationale as instant crawl (see README "Why no separate worker
process yet") -- a `sources` table row IS the durable schedule (next_crawl_at
survives a restart), this loop is just what polls it. A production
deployment that wants the scheduler to survive an API redeploy without
gapping missed crawls should split this into `workers/scheduler.py` as its
own process; the dispatch logic below doesn't change either way.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import Settings
from app.db.repositories.crawl_repository import CrawlRepository
from app.db.repositories.source_repository import SourceRepository
from app.db.session import AsyncSessionLocal
from app.services.crawling import registry
from app.services.crawling.crawl_engine import run_crawl
from app.services.discovery.seed_discovery import discover_extra_seeds, persist_discovery_outcome
from app.services.security.url_security import URLSecurityService

logger = logging.getLogger("webintel.scheduler")


async def scheduler_loop(settings: Settings) -> None:
    while True:
        try:
            await _dispatch_due_sources(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a bad tick must not kill the scheduler
            logger.exception("scheduler tick failed")
        await asyncio.sleep(settings.scheduler_poll_interval_seconds)


async def _dispatch_due_sources(settings: Settings) -> None:
    security = URLSecurityService(settings)
    dispatched: list[tuple] = []

    async with AsyncSessionLocal() as session:
        source_repo = SourceRepository(session)
        crawl_repo = CrawlRepository(session)
        due_sources = await source_repo.list_due(now=datetime.now(timezone.utc))

        # Global dispatch cap (item 2): a due-source list larger than the
        # available slots just waits for the next tick -- next_crawl_at is
        # left untouched for whatever gets skipped, so nothing is dropped,
        # only delayed by one poll interval.
        available_slots = max(0, settings.scheduler_max_concurrent_jobs - registry.running_count())
        due_sources = due_sources[:available_slots]

        for source in due_sources:
            if await crawl_repo.get_active_job_for_source(source.id) is not None:
                continue  # previous crawl for this source is still running -- don't pile up

            policy = source.crawl_policy
            job = await crawl_repo.create_job(
                seed_url=source.base_url,
                max_pages=policy.get("max_pages", settings.crawl_default_max_pages),
                max_depth=policy.get("max_depth", settings.crawl_default_max_depth),
                same_domain_only=policy.get("same_domain_only", True),
                crawl_type="monitoring",
                source_id=source.id,
            )
            dispatched.append((job.id, source.base_url, policy))

    for job_id, base_url, policy in dispatched:
        extra_seeds: list[str] = []
        if policy.get("use_sitemap", True) or policy.get("use_rss", True):
            discovery = await discover_extra_seeds(base_url, settings, security)
            await persist_discovery_outcome(base_url, discovery)
            extra_seeds = discovery.urls

        task = asyncio.create_task(run_crawl(job_id, base_url, settings, extra_seed_urls=extra_seeds))
        registry.register(job_id, task)
        logger.info("dispatched monitoring crawl job=%s url=%s", job_id, base_url)
