"""Minimal in-process event bus.

ponytail: local/synchronous pub-sub only -- section 97 explicitly asks for
an EventBus interface that starts local/Redis-backed and can grow into
Kafka later without touching call sites. This is that seam: `publish` is
the only thing crawl_engine.py calls, so swapping the backend later is a
one-file change. No subscribers exist yet beyond structured logging; add
them as real consumers show up instead of pre-building handlers for
imagined ones.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("webintel.events")


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Subscriber = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        self._subscribers[event_type].append(handler)

    async def publish(self, event: Event) -> None:
        logger.info("event=%s payload=%s", event.type, event.payload)
        for handler in self._subscribers.get(event.type, []):
            result = handler(event)
            if result is not None:
                await result


# Process-wide default bus. Fine for a single-process deployment; a
# multi-process worker split (phase 3) would inject a Redis- or
# Kafka-backed implementation of the same publish() signature instead.
default_bus = EventBus()
