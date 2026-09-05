"""Structured-enough logging: every log line gets the current request_id
(set by main.py's middleware) when one is active, via a contextvar +
logging.Filter rather than passing request_id through every function
signature. Background tasks (crawl_engine, scheduler) have no HTTP request
and log crawl_job_id/source_id directly in their messages/event payloads
instead -- see app/services/events.py for that side of things.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [request_id=%(request_id)s] %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
