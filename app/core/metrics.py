"""Prometheus metrics (spec section 68), aggregated at crawl-completion
granularity rather than per-page: `CRAWL_COMPLETED`'s event payload already
carries the full per-run stats dict (see RunStats.as_dict() in
crawl_engine.py), so one subscriber folds it into the counters below
instead of instrumenting every individual PAGE_FETCHED/DOCUMENT_CREATED
event. Coarser than per-page metrics, but avoids both duplicating
crawl_engine's own bookkeeping and label-cardinality blowup, and still
answers every counter spec section 68 asks for except a latency histogram
(no per-request timing is threaded through events yet -- add one if p95
latency dashboards are actually needed).
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

from app.services.events import Event, EventBus

registry = CollectorRegistry()

crawl_count = Counter("webintel_crawls_started_total", "Crawls started", registry=registry)
crawl_success = Counter("webintel_crawls_completed_total", "Crawls that finished (any terminal status)", registry=registry)
crawl_failure = Counter("webintel_crawls_failed_total", "Crawls that ended in status=failed", registry=registry)

pages_fetched = Counter("webintel_pages_fetched_total", "Pages successfully fetched", registry=registry)
pages_failed = Counter("webintel_pages_failed_total", "Pages that failed to fetch", registry=registry)
http_pages = Counter("webintel_http_pages_total", "Pages fetched via HTTP", registry=registry)
browser_pages = Counter("webintel_browser_pages_total", "Pages fetched via the browser fallback", registry=registry)
bytes_downloaded = Counter("webintel_bytes_downloaded_total", "Raw bytes downloaded", registry=registry)

extraction_success = Counter("webintel_extraction_success_total", "Pages successfully extracted", registry=registry)
extraction_failure = Counter("webintel_extraction_failure_total", "Fetched pages that failed extraction", registry=registry)

documents_created = Counter("webintel_documents_created_total", "New documents", registry=registry)
documents_updated = Counter("webintel_documents_updated_total", "Updated document versions", registry=registry)

preflight_count = Counter("webintel_preflight_runs_total", "Pre-flight runs", ["status"], registry=registry)

# Domain intelligence (spec Phase 6 section 31). Deliberately no per-domain
# label anywhere here -- domain names are unbounded cardinality exactly
# like the raw URLs the spec warns against; per-domain health lives at
# GET /domains/{domain}/profile instead. `strategy` is a 3-value enum, safe
# to label on.
domain_profile_updates = Counter("webintel_domain_profile_updates_total", "Domain profile rows updated", registry=registry)
adaptive_strategy_decisions = Counter(
    "webintel_adaptive_strategy_decisions_total", "Routing decisions by chosen strategy", ["strategy"], registry=registry
)
strategy_escalations = Counter(
    "webintel_strategy_escalations_total", "Pages that started HTTP and escalated to browser", registry=registry
)
domain_throttle_events = Counter("webintel_domain_throttle_events_total", "Requests skipped by an open circuit breaker", registry=registry)
domain_circuit_open = Counter("webintel_domain_circuit_open_total", "Times a domain's circuit breaker opened", registry=registry)
http_403_total = Counter("webintel_http_403_total", "403 responses observed", registry=registry)
http_429_total = Counter("webintel_http_429_total", "429 responses observed", registry=registry)
# `verdict` is SoftBlockVerdict, a fixed ~7-value enum -- bounded cardinality,
# same reasoning as `strategy` above.
soft_block_total = Counter(
    "webintel_soft_block_total", "Soft-block verdicts observed on transport-successful fetches",
    ["verdict"], registry=registry,
)

scrapling_attempts = Counter("webintel_scrapling_attempts_total", "Scrapling adaptive-extraction fallback attempts", registry=registry)
scrapling_success = Counter("webintel_scrapling_success_total", "Scrapling adaptive-extraction fallback successes", registry=registry)
forum_extraction_success = Counter("webintel_forum_extraction_success_total", "Successful forum thread extractions", registry=registry)
listing_extraction_success = Counter("webintel_listing_extraction_success_total", "Successful classified listing extractions", registry=registry)

# Change detection (spec Phase 7 section 40). `change_type`/`severity` are
# small fixed enums (ChangeType/Severity in app/services/monitoring/models.py)
# -- bounded cardinality, same reasoning as `strategy` above. Never label on
# URL/title/body: unbounded cardinality is exactly what section 40 forbids.
document_comparisons_total = Counter(
    "webintel_document_comparisons_total", "Times an existing document was compared against a fresh crawl", registry=registry
)
document_changes_total = Counter("webintel_document_changes_total", "Comparisons that found any change", registry=registry)
document_no_changes_total = Counter("webintel_document_no_changes_total", "Comparisons that found no change", registry=registry)
document_cosmetic_changes_total = Counter(
    "webintel_document_cosmetic_changes_total", "Changes classified as LOW severity (cosmetic)", registry=registry
)
document_meaningful_changes_total = Counter(
    "webintel_document_meaningful_changes_total", "Changes classified as MEDIUM severity or above", registry=registry
)
change_events_total = Counter("webintel_change_events_total", "DocumentChange rows written", registry=registry)
change_detection_duration_seconds = Histogram(
    "webintel_change_detection_duration_seconds", "detect_change() wall time", registry=registry
)
change_type_total = Counter("webintel_change_type_total", "Changes by change_type", ["change_type"], registry=registry)
change_severity_total = Counter("webintel_change_severity_total", "Changes by severity", ["severity"], registry=registry)
page_restored_total = Counter("webintel_page_restored_total", "Source URLs that went removed -> active again", registry=registry)
near_duplicate_detected_total = Counter(
    "webintel_near_duplicate_detected_total", "New documents matching another document's normalized_hash", registry=registry
)
classification_corrections_total = Counter(
    "webintel_classification_corrections_total",
    "Page-type classifications adjusted upward by a historical URL-pattern signal", registry=registry,
)

# Phase 8 entity/story intelligence (spec section 39). `extractors_used`/
# `story_decision` are small fixed enums -- bounded, same reasoning as
# `strategy` above. Never entity_type-labeled per-value (an entity TYPE
# label like "PERSON" is fine and bounded; an entity NAME never is).
entity_extraction_total = Counter("webintel_entity_extraction_total", "Documents run through entity extraction", registry=registry)
entity_extraction_duration_seconds = Histogram(
    "webintel_entity_extraction_duration_seconds", "Entity extraction + resolution + story matching wall time", registry=registry
)
entities_extracted_total = Counter("webintel_entities_extracted_total", "Entity candidates extracted (pre-resolution)", registry=registry)
entity_resolution_total = Counter(
    "webintel_entity_resolution_total", "Entity resolution outcomes", ["action"], registry=registry
)
intelligence_failures_total = Counter(
    "webintel_intelligence_failures_total", "Document intelligence runs that failed (crawl still succeeded)", registry=registry
)

story_candidates_generated_total = Counter(
    "webintel_story_candidates_generated_total", "Candidate stories generated per document", registry=registry
)
story_match_decisions_total = Counter(
    "webintel_story_match_decisions_total", "Story match decisions", ["decision"], registry=registry
)
story_attached_total = Counter("webintel_story_attached_total", "Documents attached to an existing story", registry=registry)
story_created_total = Counter("webintel_story_created_total", "New stories created", registry=registry)

# Phase 9 discovery/extraction (spec section 35). `method`/`source` are small
# fixed enums (rel_next/link_text/numbered, sitemap/feed) -- same bounded-
# cardinality reasoning as `strategy` above.
sitemap_discovered_total = Counter("webintel_sitemap_discovered_total", "URLs discovered via sitemap per seed-discovery run", registry=registry)
feed_discovered_total = Counter("webintel_feed_discovered_total", "URLs discovered via RSS/Atom feed per seed-discovery run", registry=registry)
pagination_detected_total = Counter(
    "webintel_pagination_detected_total", "Pages checked for a next-page link, by outcome", ["found"], registry=registry
)
index_items_extracted_total = Counter("webintel_index_items_extracted_total", "Listing items extracted from index/search-result pages", registry=registry)
extraction_quality_score = Histogram(
    "webintel_extraction_quality_score", "score_extraction().overall for every extracted document", registry=registry,
    buckets=(0.0, 0.1, 0.2, 0.35, 0.5, 0.6, 0.75, 0.9, 1.0),
)
contamination_detected_total = Counter(
    "webintel_contamination_detected_total", "Extractions whose contamination_ratio crossed the fallback threshold", registry=registry
)
CONTAMINATION_QUALITY_THRESHOLD = 0.6  # mirrors extraction_router.CONTAMINATION_THRESHOLD; duplicated to avoid a metrics->extraction import

provenance_fields_recorded_total = Counter(
    "webintel_provenance_fields_recorded_total", "Field-level provenance entries recorded across all extractions", registry=registry
)
domain_capability_updates_total = Counter(
    "webintel_domain_capability_updates_total", "Domain-level discovery/quality capability writes", ["kind"], registry=registry
)
url_pattern_capability_updates_total = Counter(
    "webintel_url_pattern_capability_updates_total", "URL-pattern-level capability writes with a quality or pagination observation", registry=registry
)
capability_learning_decisions_total = Counter(
    "webintel_capability_learning_decisions_total", "Routing decisions by which precedence rung decided them", ["basis"], registry=registry
)

browser_escalation_total = Counter("webintel_browser_escalation_total", "Pages where an HTTP-vs-browser comparison was made", registry=registry)
browser_improvement_total = Counter("webintel_browser_improvement_total", "Escalation comparisons where the browser result actually won", registry=registry)
index_child_urls_discovered_total = Counter(
    "webintel_index_child_urls_discovered_total", "Child URLs enqueued from an index/listing page's extracted items", registry=registry
)
extraction_completeness_score = Histogram(
    "webintel_extraction_completeness_score", "score_completeness().overall for every extracted document", registry=registry,
    buckets=(0.0, 0.25, 0.5, 0.65, 0.8, 0.9, 1.0),
)
infinite_scroll_detected_total = Counter(
    "webintel_infinite_scroll_detected_total", "Bounded infinite-scroll/load-more expansions that found new items", registry=registry
)
ssrf_rejected_discovered_urls_total = Counter(
    "webintel_ssrf_rejected_discovered_urls_total", "Discovered URLs rejected by SSRF validation before enqueue", registry=registry
)


def register_metric_subscribers(bus: EventBus) -> None:
    bus.subscribe("CRAWL_STARTED", _on_crawl_started)
    bus.subscribe("CRAWL_COMPLETED", _on_crawl_completed)
    bus.subscribe("CRAWL_FAILED", _on_crawl_failed)
    bus.subscribe("STRATEGY_DECIDED", _on_strategy_decided)
    bus.subscribe("STRATEGY_ESCALATED", lambda event: strategy_escalations.inc())
    bus.subscribe("DOMAIN_THROTTLED", lambda event: domain_throttle_events.inc())
    bus.subscribe("DOMAIN_CIRCUIT_OPENED", lambda event: domain_circuit_open.inc())
    bus.subscribe("DOMAIN_PROFILE_UPDATED", lambda event: domain_profile_updates.inc())
    bus.subscribe("SCRAPLING_ATTEMPTED", _on_scrapling_attempted)
    bus.subscribe("FORUM_EXTRACTED", lambda event: forum_extraction_success.inc())
    bus.subscribe("LISTING_EXTRACTED", lambda event: listing_extraction_success.inc())
    bus.subscribe("DOCUMENT_CHANGE_DETECTED", _on_document_change_detected)
    bus.subscribe("SOURCE_URL_RESTORED", lambda event: page_restored_total.inc())
    bus.subscribe("NEAR_DUPLICATE_DETECTED", lambda event: near_duplicate_detected_total.inc())
    bus.subscribe("CLASSIFICATION_CORRECTED", lambda event: classification_corrections_total.inc())
    bus.subscribe("ENTITY_EXTRACTION_COMPLETED", _on_entity_extraction_completed)
    bus.subscribe("INTELLIGENCE_FAILED", lambda event: intelligence_failures_total.inc())
    bus.subscribe("STORY_MATCH_DECIDED", _on_story_match_decided)
    bus.subscribe("SITEMAP_DISCOVERED", _on_sitemap_discovered)
    bus.subscribe("FEED_DISCOVERED", _on_feed_discovered)
    bus.subscribe("PATTERN_PROFILE_UPDATED", lambda event: url_pattern_capability_updates_total.inc())
    bus.subscribe("BROWSER_ESCALATION_COMPARED", _on_browser_escalation_compared)
    bus.subscribe("INDEX_CHILD_URLS_DISCOVERED", lambda event: index_child_urls_discovered_total.inc(event.payload.get("count", 0)))
    bus.subscribe("EXTRACTION_COMPLETENESS_SCORED", lambda event: extraction_completeness_score.observe(event.payload.get("overall", 0.0)))
    bus.subscribe("PAGINATION_DETECTED", lambda event: pagination_detected_total.labels(found=str(bool(event.payload.get("found")))).inc())
    bus.subscribe("INDEX_EXTRACTED", lambda event: index_items_extracted_total.inc(event.payload.get("listing_count", 0)))
    bus.subscribe("EXTRACTION_QUALITY_SCORED", _on_extraction_quality_scored)
    bus.subscribe("SOFT_BLOCK_DETECTED", lambda event: soft_block_total.labels(verdict=event.payload.get("verdict", "UNKNOWN")).inc())
    bus.subscribe("PROVENANCE_RECORDED", lambda event: provenance_fields_recorded_total.inc(event.payload.get("field_count", 0)))
    bus.subscribe("DOMAIN_PROFILE_UPDATED", lambda event: domain_capability_updates_total.labels(kind="fetch").inc())
    bus.subscribe("INFINITE_SCROLL_DETECTED", lambda event: infinite_scroll_detected_total.inc())
    bus.subscribe("URL_PATTERN_PROFILE_UPDATED", lambda event: url_pattern_capability_updates_total.inc())


def _on_entity_extraction_completed(event: Event) -> None:
    entity_extraction_total.inc()
    entity_extraction_duration_seconds.observe(event.payload.get("duration_seconds", 0.0))
    entities_extracted_total.inc(event.payload.get("entity_count", 0))
    for action, count in event.payload.get("resolution_counts", {}).items():
        entity_resolution_total.labels(action=action).inc(count)


def _on_story_match_decided(event: Event) -> None:
    decision = event.payload.get("decision", "NONE")
    story_match_decisions_total.labels(decision=decision).inc()
    story_candidates_generated_total.inc(event.payload.get("candidate_count", 0))
    if decision == "ATTACH":
        story_attached_total.inc()
    elif decision == "NEW_STORY":
        story_created_total.inc()


def _on_document_change_detected(event: Event) -> None:
    payload = event.payload
    document_comparisons_total.inc()
    change_detection_duration_seconds.observe(payload.get("duration_seconds", 0.0))

    if not payload.get("changed"):
        document_no_changes_total.inc()
        return

    document_changes_total.inc()
    change_events_total.inc()
    change_type_total.labels(change_type=payload.get("change_type", "unknown")).inc()
    severity = payload.get("severity", "NONE")
    change_severity_total.labels(severity=severity).inc()
    if severity == "LOW":
        document_cosmetic_changes_total.inc()
    elif severity in ("MEDIUM", "HIGH", "CRITICAL"):
        document_meaningful_changes_total.inc()


def _on_strategy_decided(event: Event) -> None:
    strategy = event.payload.get("strategy", "unknown")
    adaptive_strategy_decisions.labels(strategy=strategy).inc()
    capability_learning_decisions_total.labels(basis=event.payload.get("basis", "default")).inc()


def _on_scrapling_attempted(event: Event) -> None:
    scrapling_attempts.inc()
    if event.payload.get("success"):
        scrapling_success.inc()


def _on_crawl_started(event: Event) -> None:
    crawl_count.inc()


def _on_crawl_completed(event: Event) -> None:
    crawl_success.inc()
    stats = event.payload
    pages_fetched.inc(stats.get("pages_fetched", 0))
    pages_failed.inc(stats.get("pages_failed", 0))
    http_pages.inc(stats.get("http_pages", 0))
    browser_pages.inc(stats.get("browser_pages", 0))
    bytes_downloaded.inc(stats.get("bytes_downloaded", 0))
    documents_created.inc(stats.get("new_documents", 0))
    documents_updated.inc(stats.get("updated_documents", 0))

    fetched = stats.get("pages_fetched", 0)
    extracted = stats.get("pages_extracted", 0)
    extraction_success.inc(extracted)
    extraction_failure.inc(max(0, fetched - extracted))


def _on_crawl_failed(event: Event) -> None:
    crawl_failure.inc()


def _on_browser_escalation_compared(event: Event) -> None:
    browser_escalation_total.inc()
    if event.payload.get("browser_wins"):
        browser_improvement_total.inc()


def _on_sitemap_discovered(event: Event) -> None:
    sitemap_discovered_total.inc(event.payload.get("count", 0))
    domain_capability_updates_total.labels(kind="discovery").inc()


def _on_feed_discovered(event: Event) -> None:
    feed_discovered_total.inc(event.payload.get("count", 0))
    domain_capability_updates_total.labels(kind="discovery").inc()


def _on_extraction_quality_scored(event: Event) -> None:
    extraction_quality_score.observe(event.payload.get("overall", 0.0))
    if event.payload.get("contamination_ratio", 0.0) >= CONTAMINATION_QUALITY_THRESHOLD:
        contamination_detected_total.inc()


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
