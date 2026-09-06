-- =============================================================================
-- Bluweb / Web Intelligence — DATABASE CONTRACT (unified table)
-- =============================================================================
-- Status: AUTHORITATIVE schema for the external PostgreSQL server.
-- Apply this file manually (psql) against the target database. The application
-- does NOT run Alembic, create_all, or Docker Postgres.
--
-- Contract pair:
--   docs/unified_schema.sql  ↔  app/db/models/unified.py + STI subclasses
--
-- Architecture:
--   Discriminated rows in `webintel_unified` via `record_kind`.
--   Dense typed columns for the common document/crawl path.
--   JSONB for 1:N history, mention lists, story links, learning bags,
--   and URL-pattern per-page_type counters under domain_learning.by_page_type.
--   Logical IDs (document_id, entity_id, story_id, …) for cross-kind joins
--   inside the same table (no FK to abolished tables).
--
-- MinIO remains separate for raw HTML/bytes; this table stores artifact
-- references only (storage_key, sha256, size, content_type).
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- optional fuzzy entity search

-- -----------------------------------------------------------------------------
-- Primary unified table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webintel_unified (
    -- Identity
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Discriminator: which logical object this row represents
    record_kind             TEXT NOT NULL
        CHECK (record_kind IN (
            'document',          -- canonical page (current extracted view)
            'crawl_job',         -- instant/monitoring job + run stats
            'crawl_page',        -- per-URL attempt within a job (optional grain)
            'entity',            -- canonical NER entity
            'story',             -- story cluster
            'source',            -- monitored website registration
            'domain_profile',    -- fetch_strategy_stats + learning snapshot
            'url_pattern',       -- url_pattern_stats row
            'preflight'          -- capability assessment snapshot
        )),

    -- Logical IDs (stable across kinds; used instead of cross-table FKs)
    document_id             UUID,          -- set for document / crawl_page / mentions embeds
    entity_id               UUID,          -- set for entity rows
    story_id                UUID,          -- set for story rows
    source_id               UUID,          -- monitored source
    crawl_job_id            UUID,          -- crawl job
    crawl_run_id            UUID,          -- optional run id
    preflight_id            UUID,          -- preflight report
    raw_artifact_id         UUID,          -- latest raw artifact logical id

    -- Website / URL scope
    domain                  TEXT,
    url                     TEXT,
    normalized_url          TEXT,
    canonical_url           TEXT,
    source_name             TEXT,          -- denormalized from sources.name when known
    source_type             TEXT,          -- sources.source_type
    source_status           TEXT,          -- paused|active|…

    -- Crawl job / run information
    crawl_type              TEXT,          -- instant|monitoring
    crawl_status            TEXT,          -- queued|running|completed|cancelled|failed|cancelling
    crawl_priority          INTEGER,
    max_pages               INTEGER,
    max_depth               INTEGER,
    same_domain_only        BOOLEAN,
    crawl_error             TEXT,
    crawl_statistics        JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- mirrors crawl_jobs.statistics / crawl_runs counters
    pages_discovered        INTEGER,
    pages_attempted         INTEGER,
    pages_fetched           INTEGER,
    pages_failed            INTEGER,
    pages_extracted         INTEGER,
    new_documents           INTEGER,
    updated_documents       INTEGER,
    unchanged_documents     INTEGER,
    http_pages              INTEGER,
    browser_pages           INTEGER,
    bytes_downloaded        BIGINT,

    -- Per-page crawl attempt (crawl_pages / fetch)
    crawl_page_status       TEXT,          -- pending|fetched|failed|skipped
    fetch_strategy          TEXT,          -- http|browser
    http_status             INTEGER,
    content_type_header     TEXT,          -- response Content-Type
    fetch_latency_ms        DOUBLE PRECISION,
    crawl_depth             INTEGER,
    fetch_error             TEXT,

    -- Raw artifact / object storage reference (NOT the HTML blob)
    storage_backend         TEXT NOT NULL DEFAULT 'minio',
    storage_bucket          TEXT,
    storage_key             TEXT,          -- MinIO object key
    raw_content_type        TEXT,
    raw_size_bytes          BIGINT,
    raw_sha256              TEXT,

    -- Dedup / hashing
    content_hash            TEXT,          -- exact bytes hash
    normalized_hash         TEXT,          -- normalized body hash
    simhash                 BIGINT,        -- signed int64 as stored today

    -- Extracted page fields (document grain)
    title                   TEXT,
    content                 TEXT,          -- clean body (= documents.current_content)
    author                  TEXT,
    published_at            TIMESTAMPTZ,
    source_updated_at       TIMESTAMPTZ,
    language                TEXT,
    page_content_type       TEXT,          -- html|… documents.content_type
    page_type               TEXT,          -- HOME|ARTICLE|… classification
    extraction_method       TEXT,          -- trafilatura|generic|…
    extraction_confidence   DOUBLE PRECISION,
    current_version         INTEGER,
    links                   JSONB NOT NULL DEFAULT '[]'::jsonb,
    images                  JSONB NOT NULL DEFAULT '[]'::jsonb,
    extracted_metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- publisher, section, tags, provenance, listings, completeness, …

    -- Change detection (latest + history)
    change_status           TEXT,
        -- NEW | UPDATED | UNCHANGED | REMOVED | RESTORED | NULL
    latest_change_type      TEXT,          -- Phase 7 taxonomy (CONTENT_CHANGED, …)
    latest_change_severity  TEXT,          -- NONE|LOW|MEDIUM|HIGH|…
    latest_change_similarity DOUBLE PRECISION,
    latest_change_confidence DOUBLE PRECISION,
    latest_changed_fields   JSONB NOT NULL DEFAULT '[]'::jsonb,
    latest_change_diff      JSONB NOT NULL DEFAULT '{}'::jsonb,
    latest_change_reasons   JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Full history embedded (1:N absorbed from document_versions / document_changes)
    versions                JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{version_number, change_type, title, content, content_hash,
        --   normalized_hash, raw_artifact_id, storage_key, extraction_metadata,
        --   created_at}, ...]
    changes                 JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{change_id, previous_version, current_version, page_type, change_type,
        --   severity, similarity, change_confidence, changed_fields, diff,
        --   reasons, created_at}, ...]

    -- NER / intelligence (document-centric embeds + entity/story kinds)
    entity_type             TEXT,          -- PERSON|ORGANIZATION|… (entity rows)
    canonical_name          TEXT,
    normalized_name         TEXT,
    entity_confidence       DOUBLE PRECISION,
    entity_language         TEXT,
    entity_aliases          JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{alias_text, normalized_alias, language, source, confidence}, ...]
    entity_mentions         JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- On document rows: mentions found on this page
        -- [{entity_id, raw_text, normalized_text, entity_type, confidence,
        --   extractor, start_offset, end_offset, context}, ...]
        -- On entity rows: optional reverse index of recent mentions

    -- Story fields
    story_status            TEXT,          -- ACTIVE|…
    story_canonical_title   TEXT,
    story_document_count    INTEGER,
    story_source_count      INTEGER,
    story_entity_count      INTEGER,
    story_scoring_version   TEXT,
    story_first_seen_at     TIMESTAMPTZ,
    story_last_activity_at  TIMESTAMPTZ,
    story_first_published_at TIMESTAMPTZ,
    story_last_published_at TIMESTAMPTZ,
    story_documents         JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{document_id, match_score, match_method, confidence, feature_scores,
        --   matching_evidence, scoring_version, attached_at}, ...]
    story_entities          JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{entity_id, mention_count, importance, first_seen_at, last_seen_at}, ...]
    -- On document rows: attachment to at most one primary story (API today)
    attached_story_id       UUID,
    story_match_score       DOUBLE PRECISION,
    story_match_method      TEXT,
    story_match_confidence  TEXT,

    -- Domain / URL-pattern learning (domain_profile / url_pattern kinds)
    preferred_strategy      TEXT,
    circuit_state           TEXT,
    crawl_delay_ms          DOUBLE PRECISION,
    recommended_concurrency INTEGER,
    domain_learning         JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- Full fetch_strategy_stats / url_pattern_stats bag:
        -- attempts, successes, failure_counts, quality, completeness,
        -- browser_superior counts, preferred extractors, …

    -- Source monitoring
    crawl_policy            JSONB NOT NULL DEFAULT '{}'::jsonb,
    min_interval_seconds    INTEGER,
    max_interval_seconds    INTEGER,
    current_interval_seconds INTEGER,
    consecutive_unchanged_crawls INTEGER,
    next_crawl_at           TIMESTAMPTZ,
    last_crawl_at           TIMESTAMPTZ,
    monitoring_events       JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{event_type, document_id, previous_version, new_version,
        --   change_summary, detected_at}, ...]
    source_urls             JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{url, normalized_url, status, consecutive_failures,
        --   last_failure_category, document_id, last_crawled_at}, ...]

    -- Preflight snapshot
    preflight_status        TEXT,
    preflight_payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- capability, discovery, fetch, content, extraction, sample,
        -- limitations, recommendations, duration_ms, …
    preflight_expires_at    TIMESTAMPTZ,

    -- Processing / ops
    processing_status       TEXT,
        -- pending|processing|ready|failed|skipped (operator-facing)
    processing_error        TEXT,
    intelligence_status     TEXT,
        -- not_run|completed|failed|skipped_unchanged
    extractors_used         JSONB NOT NULL DEFAULT '[]'::jsonb,
    extractors_unavailable  JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Timestamps
    collected_at            TIMESTAMPTZ,
    first_seen_at           TIMESTAMPTZ,
    last_seen_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Free-form extensibility without schema churn
    extras                  JSONB NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE webintel_unified IS
    'Authoritative single-table store for Bluweb. Discriminated by record_kind. '
    'Applied manually from docs/unified_schema.sql — not managed by Alembic.';

COMMENT ON COLUMN webintel_unified.record_kind IS
    'Discriminator selecting which column groups are meaningful for the row.';
COMMENT ON COLUMN webintel_unified.storage_key IS
    'Object-storage key for raw HTML/bytes in MinIO/S3; blob stays out of Postgres.';
COMMENT ON COLUMN webintel_unified.versions IS
    'Embedded document_versions history for document rows.';
COMMENT ON COLUMN webintel_unified.changes IS
    'Embedded document_changes history for document rows.';
COMMENT ON COLUMN webintel_unified.entity_mentions IS
    'NER mention list with extractor/confidence/span; replaces entity_mentions table when denormalized onto documents.';
COMMENT ON COLUMN webintel_unified.domain_learning IS
    'Denormalized fetch_strategy_stats / url_pattern_stats counters. '
    'For url_pattern rows, per-page_type detail lives under by_page_type.';

-- -----------------------------------------------------------------------------
-- Indexes & uniqueness (partial — only where the kind applies)
-- -----------------------------------------------------------------------------

-- Documents: one current row per normalized URL
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_document_normalized_url
    ON webintel_unified (normalized_url)
    WHERE record_kind = 'document' AND normalized_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_webintel_document_domain
    ON webintel_unified (domain)
    WHERE record_kind = 'document';

CREATE INDEX IF NOT EXISTS ix_webintel_document_page_type
    ON webintel_unified (page_type)
    WHERE record_kind = 'document';

CREATE INDEX IF NOT EXISTS ix_webintel_document_crawl_job
    ON webintel_unified (crawl_job_id)
    WHERE record_kind = 'document' AND crawl_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_webintel_document_hashes
    ON webintel_unified (content_hash, normalized_hash)
    WHERE record_kind = 'document';

CREATE INDEX IF NOT EXISTS ix_webintel_document_change_status
    ON webintel_unified (change_status)
    WHERE record_kind = 'document';

-- Optional FTS for document content (parity with documents.search_vector)
CREATE INDEX IF NOT EXISTS ix_webintel_document_fts
    ON webintel_unified
    USING gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, '')))
    WHERE record_kind = 'document';

-- Entities: match today's uq_entity_type_normalized_name
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_entity_type_name
    ON webintel_unified (entity_type, normalized_name)
    WHERE record_kind = 'entity' AND entity_type IS NOT NULL AND normalized_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_webintel_entity_trgm
    ON webintel_unified
    USING gin (normalized_name gin_trgm_ops)
    WHERE record_kind = 'entity' AND normalized_name IS NOT NULL;

-- Stories
CREATE INDEX IF NOT EXISTS ix_webintel_story_activity
    ON webintel_unified (story_last_activity_at DESC)
    WHERE record_kind = 'story';

CREATE INDEX IF NOT EXISTS ix_webintel_story_status
    ON webintel_unified (story_status)
    WHERE record_kind = 'story';

-- Crawl jobs
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_crawl_job_id
    ON webintel_unified (crawl_job_id)
    WHERE record_kind = 'crawl_job' AND crawl_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_webintel_crawl_status
    ON webintel_unified (crawl_status)
    WHERE record_kind = 'crawl_job';

-- Sources
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_source_normalized_url
    ON webintel_unified (normalized_url)
    WHERE record_kind = 'source' AND normalized_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_webintel_source_next_crawl
    ON webintel_unified (next_crawl_at)
    WHERE record_kind = 'source' AND source_status = 'active';

-- Domain / pattern learning
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_domain_profile
    ON webintel_unified (domain)
    WHERE record_kind = 'domain_profile' AND domain IS NOT NULL;

-- URL-pattern rows: `url` stores the clean URL *pattern* string (not a
-- page URL and never `pattern::PAGE_TYPE`). Per-page_type counters live in
-- domain_learning.by_page_type; UNIQUE(domain, url) is one row per pattern.
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_url_pattern
    ON webintel_unified (domain, url)
    WHERE record_kind = 'url_pattern' AND domain IS NOT NULL AND url IS NOT NULL;

-- Preflight
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_preflight_id
    ON webintel_unified (preflight_id)
    WHERE record_kind = 'preflight' AND preflight_id IS NOT NULL;

-- Cross-kind lookups by logical UUID
CREATE INDEX IF NOT EXISTS ix_webintel_document_id ON webintel_unified (document_id) WHERE document_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_webintel_entity_id   ON webintel_unified (entity_id)   WHERE entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_webintel_story_id    ON webintel_unified (story_id)    WHERE story_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_webintel_source_id   ON webintel_unified (source_id)   WHERE source_id IS NOT NULL;

-- JSONB containment helpers (mentions / story links)
CREATE INDEX IF NOT EXISTS ix_webintel_entity_mentions_gin
    ON webintel_unified USING gin (entity_mentions jsonb_path_ops)
    WHERE record_kind = 'document';

CREATE INDEX IF NOT EXISTS ix_webintel_story_documents_gin
    ON webintel_unified USING gin (story_documents jsonb_path_ops)
    WHERE record_kind = 'story';

-- Entity→story lookups (IntelligenceRepository.get_entity_stories /
-- find_candidate_stories containment filters).
CREATE INDEX IF NOT EXISTS ix_webintel_story_entities_gin
    ON webintel_unified USING gin (story_entities jsonb_path_ops)
    WHERE record_kind = 'story';

-- -----------------------------------------------------------------------------
-- updated_at maintenance
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION webintel_unified_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_webintel_unified_updated_at ON webintel_unified;
CREATE TRIGGER trg_webintel_unified_updated_at
    BEFORE UPDATE ON webintel_unified
    FOR EACH ROW EXECUTE FUNCTION webintel_unified_touch_updated_at();

-- =============================================================================
-- Design notes (kept in SQL so the schema file is self-contained)
-- =============================================================================
-- 1) What becomes JSONB and why
--    versions / changes          — unbounded 1:N history per document
--    links / images / metadata   — already JSONB in live schema
--    entity_mentions / aliases   — variable-length NER outputs
--    story_documents / entities  — M:N graph edges without junction tables
--    domain_learning             — wide counter bags (failure_counts, …)
--    crawl_statistics / events   — job/monitoring payloads
--    preflight_payload           — nested assessment report
--
-- 2) What cannot reasonably live ONLY as columns on document rows
--    - Global entity uniqueness + alias resolution across the corpus
--    - Stories attaching N documents with match evidence
--    - Domain learning updated on every fetch (would rewrite all page rows)
--    Hence separate record_kind rows in the SAME table.
--
-- 3) Trade-offs vs a fully normalized multi-table schema
--    + Single physical table for ops/export onto an existing DB
--    + Fewer joins for "page + latest NER + story attachment" reads
--    − Weaker integrity (no real FKs between kinds; JSONB can drift)
--    − JSONB child upserts require parent-row FOR UPDATE
--    − Larger rows; JSONB history growth; weaker analytics on nested arrays
--
-- 4) MinIO remains separate for raw HTML/blob storage.
-- 5) url_pattern.url = clean pattern string; per-page_type stats in
--    domain_learning.by_page_type (never encode page_type into url).
-- =============================================================================
