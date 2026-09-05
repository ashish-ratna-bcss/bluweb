from app.core import metrics as metrics_module
from app.services.events import Event, EventBus

# These tests assert before/after deltas on the module's real global
# counters (Prometheus Counters are monotonic and process-wide by design)
# rather than swapping in a fresh registry, so they're safe to run
# alongside anything else that publishes crawl events in the same process.


async def test_crawl_started_increments_crawl_count():
    counter = metrics_module.crawl_count
    before = counter._value.get()

    bus = EventBus()
    metrics_module.register_metric_subscribers(bus)
    await bus.publish(Event("CRAWL_STARTED", {"crawl_job_id": "x", "url": "https://example.com/"}))

    assert counter._value.get() == before + 1


async def test_crawl_completed_folds_run_stats_into_counters():
    bus = EventBus()
    metrics_module.register_metric_subscribers(bus)

    before_fetched = metrics_module.pages_fetched._value.get()
    before_new_docs = metrics_module.documents_created._value.get()
    before_extraction_ok = metrics_module.extraction_success._value.get()
    before_extraction_fail = metrics_module.extraction_failure._value.get()

    await bus.publish(Event("CRAWL_COMPLETED", {
        "crawl_job_id": "x", "status": "completed",
        "pages_fetched": 10, "pages_failed": 2, "pages_extracted": 7,
        "http_pages": 8, "browser_pages": 2, "bytes_downloaded": 12345,
        "new_documents": 5, "updated_documents": 1,
        "pages_discovered": 12, "pages_attempted": 12,
        "unchanged_documents": 1, "duplicate_documents": 0,
    }))

    assert metrics_module.pages_fetched._value.get() == before_fetched + 10
    assert metrics_module.documents_created._value.get() == before_new_docs + 5
    assert metrics_module.extraction_success._value.get() == before_extraction_ok + 7
    assert metrics_module.extraction_failure._value.get() == before_extraction_fail + 3  # 10 fetched - 7 extracted


async def test_crawl_failed_increments_failure_counter():
    bus = EventBus()
    metrics_module.register_metric_subscribers(bus)
    before = metrics_module.crawl_failure._value.get()

    await bus.publish(Event("CRAWL_FAILED", {"crawl_job_id": "x", "error": "boom"}))

    assert metrics_module.crawl_failure._value.get() == before + 1
