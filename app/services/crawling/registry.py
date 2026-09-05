"""In-process registry of running crawl tasks, keyed by crawl_job_id.

ponytail: a plain dict works because phase-2 crawls run inline in the API
process (see main.py docstring). The moment crawls run in separate worker
processes (phase 3's scheduler), cancellation needs to go through a shared
signal -- a `cancel_requested` row/flag in Postgres that the worker polls,
which CrawlJob.status="cancelling" already models -- this registry becomes
just the local fast-path for same-process cancellation.
"""

from __future__ import annotations

import asyncio
import uuid

_running_tasks: dict[uuid.UUID, asyncio.Task] = {}


def register(job_id: uuid.UUID, task: asyncio.Task) -> None:
    _running_tasks[job_id] = task
    task.add_done_callback(lambda _: _running_tasks.pop(job_id, None))


def cancel(job_id: uuid.UUID) -> bool:
    task = _running_tasks.get(job_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def is_running(job_id: uuid.UUID) -> bool:
    task = _running_tasks.get(job_id)
    return task is not None and not task.done()
