-- WI-03 / WI-14 one-shot cleanup for Bluweb `webintel_unified` STI table.
-- Apply manually against the Bluweb Postgres DB (see integration.md).
-- Safe to re-run: DELETE/UPDATE predicates are idempotent.

BEGIN;

-- WI-03: source statistics require non-null consecutive_unchanged_crawls
UPDATE webintel_unified
SET consecutive_unchanged_crawls = 0
WHERE record_kind = 'source'
  AND consecutive_unchanged_crawls IS NULL;

-- WI-14: keep the oldest domain_profile row per domain; drop extras
WITH ranked AS (
  SELECT id,
         ROW_NUMBER() OVER (PARTITION BY domain ORDER BY id) AS rn
  FROM webintel_unified
  WHERE record_kind = 'domain_profile'
    AND domain IS NOT NULL
)
DELETE FROM webintel_unified w
USING ranked r
WHERE w.id = r.id
  AND r.rn > 1;

-- WI-14: keep the oldest url_pattern row per (domain, url); drop extras
WITH ranked AS (
  SELECT id,
         ROW_NUMBER() OVER (PARTITION BY domain, url ORDER BY id) AS rn
  FROM webintel_unified
  WHERE record_kind = 'url_pattern'
    AND domain IS NOT NULL
    AND url IS NOT NULL
)
DELETE FROM webintel_unified w
USING ranked r
WHERE w.id = r.id
  AND r.rn > 1;

-- Re-assert partial unique indexes (no-op if already present)
CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_domain_profile
    ON webintel_unified (domain)
    WHERE record_kind = 'domain_profile' AND domain IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_webintel_url_pattern
    ON webintel_unified (domain, url)
    WHERE record_kind = 'url_pattern' AND domain IS NOT NULL AND url IS NOT NULL;

COMMIT;
