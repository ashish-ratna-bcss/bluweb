# Web Application Integration Guide

Integration guide for connecting a frontend / another backend to the **Bluweb (Web Intelligence Collection Backend)** service.

Based on the **implemented** routes and Pydantic schemas in this repo (`app/api/v1/*`, `app/schemas/*`). Anything not listed here is **not implemented** (no auth, no WebSockets/SSE/webhooks, no separate crawl workers).

Live contract drift check: open **`GET /docs`** or **`GET /openapi.json`** on a running instance.

---

## 0. Quick start for integrators

| Item | Value |
|---|---|
| Base URL (local) | `http://127.0.0.1:8000` |
| Base URL (deployed on server `1930`) | On-host `http://127.0.0.1:8000`; from a laptop use `ssh -L 8000:127.0.0.1:8000 1930` then the local URL (TCP 8000 is typically not public) |
| Content type | `application/json` on POST/PATCH bodies |
| Auth | **None** — treat as trusted-network only |
| Interactive docs | `GET /` → redirects to `/docs` |
| Request tracing | Every response has `X-Request-ID`; API errors also put it under `error.request_id` |

**Minimal happy path (instant crawl → read page body):**

```bash
BASE=http://127.0.0.1:8000

# 1) Start crawl (async)
curl -s -X POST "$BASE/api/v1/crawls" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/","max_pages":5,"max_depth":1}'
# → 202 { "crawl_id": "...", "status": "queued", ... }

# 2) Poll until completed|failed|cancelled
curl -s "$BASE/api/v1/crawls/<crawl_id>"

# 3) List documents from that crawl (max 50 returned)
curl -s "$BASE/api/v1/documents?crawl_id=<crawl_id>"

# 4) Metadata + entity/story links
curl -s "$BASE/api/v1/documents/<document_id>"

# 5) Full extracted text (list/detail do NOT include body)
curl -s "$BASE/api/v1/documents/<document_id>/versions/<current_version>"
```

**Minimal monitoring path:**

```bash
# Preflight (synchronous, can take tens of seconds)
curl -s -X POST "$BASE/api/v1/preflight" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/"}'
# → 201 { "preflight_id": "...", "status": "completed"|"failed", ... }

# Create source (starts as paused) then activate
curl -s -X POST "$BASE/api/v1/sources" -H 'Content-Type: application/json' -d '{
  "name": "Example",
  "url": "https://example.com/",
  "preflight_id": "<uuid>",
  "interval_seconds": 900
}'
curl -s -X POST "$BASE/api/v1/sources/<source_id>/start"
```

---

## 1. Architecture

```text
Your app (SPA / SSR / another service)
      │  HTTP JSON  (no auth)
      ▼
FastAPI  (uvicorn app.main:app :8000)
      │
      ├── /health*                         liveness / readiness / metrics
      ├── /api/v1/preflight                capability assessment
      ├── /api/v1/crawls                   instant crawl jobs (in-process asyncio)
      ├── /api/v1/documents                pages, versions, diffs, changes
      ├── /api/v1/sources                  continuous monitoring registration
      ├── /api/v1/search                   Postgres FTS (+ instant crawl+search)
      ├── /api/v1/domains                  domain learning / capabilities
      └── /api/v1/entities|stories         NER + story intelligence (read-only)
             │
             ├── PostgreSQL   single STI table `webintel_unified`
             │                (schema contract: docs/unified_schema.sql)
             └── MinIO        raw HTML/bytes (object refs on document versions)
```

| Concern | Actual behavior |
|---|---|
| API process | Single uvicorn process |
| Crawl execution | `asyncio.create_task` inside the API process (not a separate worker) |
| Monitoring scheduler | In-process loop in FastAPI lifespan (`scheduler_loop`) |
| Auth | **Not implemented** |
| WebSockets / SSE / webhooks | **Not implemented** — **poll HTTP** |
| CORS | **Not configured** in `app.main` — SPA on another origin needs a reverse proxy or CORS middleware you add |
| OpenAPI | `GET /docs`, `GET /openapi.json` |

---

## 2. Conventions

### 2.1 IDs

Path and JSON fields use these names consistently:

| Resource | ID field in JSON | Path param |
|---|---|---|
| Preflight | `preflight_id` | `{preflight_id}` |
| Crawl job | `crawl_id` | `{crawl_id}` |
| Document | `document_id` | `{document_id}` |
| Source | `source_id` | `{source_id}` |
| Entity | `entity_id` | `{entity_id}` |
| Story | `story_id` | `{story_id}` |

All of the above are UUIDs in path params. **Search hits** return `document_id` as a **string** (same UUID, different JSON type).

### 2.2 Errors

`APIError` responses:

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "No document found for id …",
    "details": {},
    "request_id": "uuid"
  }
}
```

Validation failures (Pydantic) return **422** with FastAPI’s default `{"detail":[…]}` — not the `error` envelope.

Unhandled exceptions return **500** `INTERNAL_ERROR`.

### 2.3 List pagination

Most list endpoints are **unbounded** (or only lightly bounded in the repository). Exception:

- `GET /api/v1/documents` silently returns at most **50** rows (`DocumentRepository.list_documents(limit=50)`). There is **no** `limit`/`offset` query param on that route today.

---

## 3. API inventory

Unless noted, paths below are absolute from the base URL.

### 3.1 Health

| Method | Path | Status | Body |
|---|---|---|---|
| GET | `/health` | 200 | `{"status":"ok"}` |
| GET | `/health/live` | 200 | `{"status":"alive"}` |
| GET | `/health/ready` | 200 / **503** | `{"status":"ready","database":"ok"}` or `{"status":"not_ready","database":"error: …"}` |
| GET | `/metrics` | 200 | Prometheus text |

```bash
curl -s http://127.0.0.1:8000/health/ready
```

---

### 3.2 Preflight — `/api/v1/preflight`

#### `POST /api/v1/preflight` → **201**

Synchronous capability assessment (DNS, HTTP, robots, sitemap, feeds, sample extract, optional browser). Can take **tens of seconds**.

**Request**

```json
{ "url": "https://example.com" }
```

**Response (`PreflightReportResponse`)** — important fields:

| Field | Type | Notes |
|---|---|---|
| `preflight_id` | UUID | Required later for `POST /sources` |
| `url` / `final_url` | string | Input vs post-redirect |
| `status` | string | e.g. `completed`, `failed` |
| `capability` | object | `score` 0–100, `confidence`, component scores, `score_semantics` |
| `discovery` | object | `sitemap`, `rss`, `atom`, `html_links`, `estimated_discoverable_urls` |
| `fetch` | object | `http`, `browser`, `recommended` (`http`\|`browser`\|…) |
| `content` | object | `html`, `pdf`, `json`, `xml`, `images` |
| `extraction` | object | `title`/`author`/`date`/`body` floats 0–1 |
| `sample` | object | `tested`, `fetched`, `extractable` |
| `limitations` | string[] | |
| `recommendations` | string[] | |
| `duration_ms` | float | |
| `error` | string\|null | Set when assessment failed early |
| `created_at` / `expires_at` | datetime | Source create rejects expired reports |

**Blocked / bad URLs:** scheme/SSRF failures do **not** return HTTP 400. The handler still returns **201** with a **failed** report (`status`/`error` populated). Compare with crawls/sources, which raise `400 URL_BLOCKED`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/preflight \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://bluecloudsoftech.com/"}'
```

#### `GET /api/v1/preflight/{preflight_id}` → 200 / **404** `PREFLIGHT_NOT_FOUND`

---

### 3.3 Crawls — `/api/v1/crawls`

#### `POST /api/v1/crawls` → **202 Accepted**

Creates a job and returns immediately; discovery + crawl run in a background asyncio task. Instant crawl does **not** require a prior preflight.

**Request (`CrawlRequest`)**

| Field | Type | Default | Constraints |
|---|---|---|---|
| `url` | string | required | Validated for scheme/SSRF → else `400 URL_BLOCKED` |
| `max_pages` | int\|null | settings `CRAWL_DEFAULT_MAX_PAGES` (100) | 1–1000 |
| `max_depth` | int\|null | settings `CRAWL_DEFAULT_MAX_DEPTH` (3) | 0–10 |
| `same_domain_only` | bool | `true` | |

```json
{
  "url": "https://example.com/",
  "max_pages": 10,
  "max_depth": 1,
  "same_domain_only": true
}
```

**Response (`CrawlJobResponse`)**

| Field | Type |
|---|---|
| `crawl_id` | UUID |
| `status` | `queued`\|`running`\|`cancelling`\|`completed`\|`cancelled`\|`failed` |
| `seed_url` | string |
| `max_pages` / `max_depth` | int |
| `created_at` / `started_at` / `completed_at` | datetime\|null |
| `error` | string\|null |
| `statistics` | object\|null | Counters filled as the job progresses |

#### Other crawl routes

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/crawls` | All jobs → `CrawlJobResponse[]` |
| GET | `/api/v1/crawls/{crawl_id}` | Poll this; **404** `CRAWL_NOT_FOUND` |
| GET | `/api/v1/crawls/{crawl_id}/pages` | Per-URL attempts (`CrawlPageResponse`) |
| POST | `/api/v1/crawls/{crawl_id}/cancel` | If `queued`/`running` → status `cancelling`, then terminal `cancelled` |

**`CrawlPageResponse` fields:** `url`, `normalized_url`, `depth`, `status`, `fetch_strategy`, `http_status`, `document_id`, `error`.

**Poll pattern (required — no push):**

```bash
CRAWL_ID=…
curl -s http://127.0.0.1:8000/api/v1/crawls/$CRAWL_ID
# repeat until status is completed|cancelled|failed
# recommended: 1–2s while queued/running, then backoff
```

---

### 3.4 Documents — `/api/v1/documents`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/documents` | List; query: `crawl_id`, `domain`, `page_type` (**max 50**, no pagination params) |
| GET | `/api/v1/documents/{document_id}` | Detail + latest change + `entity_ids` + optional `story_id` |
| GET | `/api/v1/documents/{document_id}/changes` | Stored change events; query: `severity`, `change_type`, `from`, `to` (ISO datetimes) |
| GET | `/api/v1/documents/{document_id}/versions` | Version summaries (no body text) |
| GET | `/api/v1/documents/{document_id}/versions/{n}` | Full version including `content` |
| GET | `/api/v1/documents/{document_id}/diff?from_version=&to_version=` | Unified line diff (`from_version` & `to_version` required) |

**`DocumentResponse`**

| Field | List | Detail |
|---|---|---|
| `document_id`, `url`, `canonical_url`, `domain` | yes | yes |
| `title`, `author`, `published_at`, `language`, `content_type`, `page_type` | yes | yes |
| `extraction_method`, `extraction_confidence`, `current_version`, `collected_at` | yes | yes |
| `latest_change_type` / `latest_change_severity` / `latest_change_at` | usually null | filled from latest stored change |
| `entity_ids` | always `[]` | UUIDs from mentions |
| `story_id` / `story_match_confidence` | null | set when attached (`confidence` is a string tier, e.g. HIGH/MEDIUM/LOW) |

**Body text is never on list/detail.** Use:

```bash
curl -s "http://127.0.0.1:8000/api/v1/documents/$DOC_ID/versions/$CURRENT_VERSION"
# → DocumentVersionResponse: version_number, change_type, title, content, content_hash, created_at
```

**`ChangeEventResponse`:** `change_id`, `document_id`, `previous_version_id`, `current_version_id`, `page_type`, `change_type`, `severity`, `similarity`, `change_confidence`, `changed_fields`, `diff` (object), `reasons`, `created_at`.

**`DiffResponse`:** `document_id`, `from_version`, `to_version`, `changed`, `summary.{added_lines,removed_lines}`, `diff` (string[] unified diff lines).

**Errors:** `404 DOCUMENT_NOT_FOUND`, `404 VERSION_NOT_FOUND`.

---

### 3.5 Sources (monitoring) — `/api/v1/sources`

| Method | Path | Status | Purpose |
|---|---|---|---|
| POST | `/api/v1/sources` | 201 | Register source (**requires valid, non-expired preflight**, same registrable domain) |
| GET | `/api/v1/sources` | 200 | List |
| GET | `/api/v1/sources/{source_id}` | 200 | Detail; **404** `SOURCE_NOT_FOUND` |
| PATCH | `/api/v1/sources/{source_id}` | 200 | Update `name` / `source_type` / `crawl_policy` only (not intervals) |
| DELETE | `/api/v1/sources/{source_id}` | 204 | Delete |
| POST | `/api/v1/sources/{source_id}/start` | 200 | Set `active` (alias: `/resume`) |
| POST | `/api/v1/sources/{source_id}/pause` | 200 | Set `paused` |
| GET | `/api/v1/sources/{source_id}/events` | 200 | Monitoring events |
| GET | `/api/v1/sources/{source_id}/statistics` | 200 | Interval + counters |

**Create body (`SourceCreateRequest`)**

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | string | required | |
| `url` | string | required | `400 URL_BLOCKED` on scheme/SSRF |
| `preflight_id` | UUID | required | Must exist, unexpired, same domain |
| `source_type` | string | `"unknown"` | Free string (e.g. `corporate`) |
| `interval_seconds` | int | 900 | Min interval; 60–86400 |
| `max_interval_seconds` | int | 86400 | 60–604800 |
| `crawl_policy` | object\|null | null | See below |

```json
{
  "name": "Blue Cloud",
  "url": "https://bluecloudsoftech.com/",
  "preflight_id": "<uuid from preflight>",
  "source_type": "corporate",
  "interval_seconds": 900,
  "max_interval_seconds": 86400,
  "crawl_policy": {
    "max_depth": 2,
    "max_pages": 50,
    "same_domain_only": true,
    "use_sitemap": true,
    "use_rss": true,
    "browser_mode": "auto"
  }
}
```

**Initial status after create:** `paused`. Call `/start` (or `/resume`) before the scheduler will crawl it.

**`SourceResponse`:** `source_id`, `name`, `base_url`, `domain`, `source_type`, `status`, `crawl_policy`, `min_interval_seconds`, `max_interval_seconds`, `current_interval_seconds`, `created_at`, `updated_at`, `last_crawl_at`, `next_crawl_at`.

**Create errors:** `400 URL_BLOCKED`, `400 PREFLIGHT_EXPIRED`, `400 PREFLIGHT_URL_MISMATCH`, `404 PREFLIGHT_NOT_FOUND`, `409 SOURCE_ALREADY_EXISTS`.

Scheduler picks `next_crawl_at <= now` about every `SCHEDULER_POLL_INTERVAL_SECONDS` (default 30).

---

### 3.6 Search — `/api/v1/search`

#### `POST /api/v1/search`

Postgres full-text search over stored documents.

**Request**

| Field | Type | Default |
|---|---|---|
| `query` | string\|null | null |
| `domain` | string\|null | null |
| `source_id` | string\|null | null |
| `language` | string\|null | null |
| `date_from` / `date_to` | datetime\|null | null |
| `limit` | int | 20 (1–100) |
| `offset` | int | 0 |

```json
{
  "query": "cybersecurity",
  "domain": "bluecloudsoftech.com",
  "limit": 20,
  "offset": 0
}
```

**Response:** `{ "total": N, "results": [ SearchHitResponse, … ] }`

**`SearchHitResponse`:** `document_id` (**string**), `title`, `url`, `domain`, `snippet`, `published_at`, `collected_at`, `version`, `content_hash`, `relevance`.

#### `POST /api/v1/search/instant`

Starts a crawl, waits up to `wait_seconds`, returns whatever is indexed so far plus `crawl_id` so you can keep polling.

**Request**

| Field | Type | Default | Constraints |
|---|---|---|---|
| `url` | string | required | `400 URL_BLOCKED` possible |
| `query` | string\|null | null | Substring filter on title/content for this crawl’s docs |
| `max_pages` | int | 5 | 1–50 |
| `wait_seconds` | float | 8.0 | 0–30 |

**Response (`InstantSearchResponse`):** `crawl_id` (string), `crawl_status`, `total`, `results`, `note` (tells you if crawl still running).

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/search/instant \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/","query":"example","max_pages":5,"wait_seconds":8}'
```

---

### 3.7 Domains — `/api/v1/domains`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/domains/{domain}/profile` | Health, preferred strategy, patterns, politeness |
| GET | `/api/v1/domains/{domain}/capabilities` | Interpreted discovery/fetch/browser/extraction capability |

`{domain}` is the hostname as stored (e.g. `bluecloudsoftech.com`).

Both return **404** `DOMAIN_NOT_FOUND` if that domain has never been crawled (no learning row yet).

---

### 3.8 Intelligence — entities & stories

Mounted at `/api/v1` (paths below are full).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/entities` | List; query `entity_type` |
| GET | `/api/v1/entities/{entity_id}` | Detail |
| GET | `/api/v1/entities/{entity_id}/documents` | `{ "entity_id", "document_ids": [] }` |
| GET | `/api/v1/entities/{entity_id}/stories` | Stories linked to entity |
| GET | `/api/v1/stories` | List; query `status`, `from`, `to` |
| GET | `/api/v1/stories/{story_id}` | Summary |
| GET | `/api/v1/stories/{story_id}/documents` | Attachment scores / evidence |
| GET | `/api/v1/stories/{story_id}/entities` | Entities in story |
| GET | `/api/v1/stories/{story_id}/sources` | Domains contributing |
| GET | `/api/v1/stories/{story_id}/timeline` | Ordered by `published_at` (else attach time) |

**`EntityResponse`:** `entity_id`, `entity_type`, `canonical_name`, `normalized_name`, `language`, `confidence`, `first_seen_at`, `last_seen_at`.

**`StorySummaryResponse`:** `story_id`, `canonical_title`, `status`, `first_seen_at`, `last_activity_at`, `document_count`, `source_count`, `entity_count`.

**Errors:** `404 ENTITY_NOT_FOUND`, `404 STORY_NOT_FOUND`.

**Not implemented:**

- Mention detail API (`entity_mentions` exist in storage; only `entity_ids` on document detail)
- Write/update/delete for entities/stories
- Streaming intelligence progress

Intelligence runs **inside crawl** on **NEW/UPDATED** documents only (not UNCHANGED). Failures are logged; the crawl can still complete.

---

## 4. End-to-end workflows

### 4.1 Register a website for monitoring

1. `POST /api/v1/preflight` → keep `preflight_id` while unexpired  
2. `POST /api/v1/sources` with that id (source is **paused**)  
3. `POST /api/v1/sources/{id}/start`  
4. Poll `GET …/sources/{id}`, `…/events`, `…/statistics`

### 4.2 Instant crawl (no source)

1. `POST /api/v1/crawls` → `crawl_id`  
2. Poll `GET /api/v1/crawls/{id}` until terminal  
3. Optional: `GET …/pages`  
4. `GET /api/v1/documents?crawl_id=`  
5. `GET /api/v1/documents/{id}/versions/{current_version}` for body  

### 4.3 Detect updates

1. `GET /api/v1/sources/{id}/events` (`UPDATED`, etc.)  
2. and/or `GET /api/v1/documents/{id}/changes`  
3. and/or `GET /api/v1/documents/{id}/diff?from_version=&to_version=`  

### 4.4 Entities / stories

After a NEW/UPDATED crawl finishes: `GET /api/v1/entities`, `GET /api/v1/stories`, or follow `entity_ids` / `story_id` from document detail.

### 4.5 Search

1. `POST /api/v1/search`  
2. or `POST /api/v1/search/instant` then poll crawl + search again  

### 4.6 Retries

- No first-class retry API. Re-`POST /crawls` (may be UNCHANGED).  
- Monitoring reschedules via `next_crawl_at`.  
- Cancelled jobs are not auto-restarted.

---

## 5. Frontend map (screens → APIs)

| UI | Primary APIs |
|---|---|
| Dashboard | `/health/ready`, `GET /api/v1/crawls`, `GET /api/v1/sources`, `/metrics` |
| Website / source management | preflight + sources CRUD + start/pause |
| Crawl run | `POST /api/v1/crawls` → poll job + pages |
| Document explorer | `GET /api/v1/documents` (+ version body endpoint) |
| Document detail | document + versions + changes + diff |
| Entity / story explorers | `/api/v1/entities*`, `/api/v1/stories*` |
| Search | `POST /api/v1/search`, optional `/instant` |
| Domain ops | `/api/v1/domains/{d}/profile`, `/capabilities` |
| Errors | crawl `error`, page errors, source events, `X-Request-ID` |

---

## 6. Auth and security (integrator obligations)

> API authn/authz is **not implemented**.

- Keep the API on localhost / private VPC / gateway with **your** auth + TLS + rate limits.  
- Server-side SSRF checks emit `URL_BLOCKED` on crawl/source (and fail preflight reports); still validate URLs in the UI.  
- No CORS middleware — plan a reverse proxy or add CORS before a browser SPA on another origin talks to `:8000`.  
- Secrets (`DATABASE_URL`, MinIO, `HF_TOKEN`) stay in server `.env` only.

---

## 7. Async processing

| Topic | Approach |
|---|---|
| Long crawls | `202` + background task; **poll** job status |
| Progress | `statistics` on job + `/crawls/{id}/pages`; no percent-complete field |
| Workers | None separate — same process as API |
| Push | Not implemented |
| Intelligence | After NEW/UPDATED; poll entities/stories |

**Poll interval:** 1–2s while `queued`/`running`, backoff after ~30s.

---

## 8. Persistence model (API-facing)

Logical resources (what your app thinks about):

```text
preflight  ──required──►  source  ──scheduler──►  crawl job ──► documents
                                                              │
                                                              ├── versions (+ MinIO raw bytes)
                                                              ├── changes
                                                              ├── entity mentions ──► entities
                                                              └── story links ──► stories
domain_profile / url_pattern   (learning; powers /domains/*)
```

Physically, these are rows in **`webintel_unified`** (STI by `record_kind`) plus MinIO objects. Apply `docs/unified_schema.sql` manually once. **Do not** run `alembic upgrade head` against this database.

**Change lifecycle:** NEW → version 1 + intelligence; UPDATED → new version + change event + intelligence; UNCHANGED → no new version, **no** intelligence rerun.

---

## 9. Error handling cheat sheet

| Situation | HTTP | Code / shape | Frontend action |
|---|---|---|---|
| Bad JSON / fields | 422 | FastAPI `detail` | Field errors |
| SSRF / blocked URL (crawl/source/instant search) | 400 | `URL_BLOCKED` | Block submit |
| Blocked URL (preflight) | 201 | report `status=failed` | Show `error` on report |
| Not found | 404 | `*_NOT_FOUND` | Empty state |
| Source duplicate | 409 | `SOURCE_ALREADY_EXISTS` | Open existing |
| Preflight expired / domain mismatch | 400 | `PREFLIGHT_EXPIRED` / `PREFLIGHT_URL_MISMATCH` | Re-run preflight |
| Domain never crawled | 404 | `DOMAIN_NOT_FOUND` | Prompt first crawl |
| Crawl failure | 200 on poll | job `status=failed` | Show `error`; allow re-POST |
| Ready fail | 503 | `not_ready` | Disable write actions |
| Unexpected | 500 | `INTERNAL_ERROR` | Show `request_id` |
| Rate limits | — | **not implemented** | Add at gateway |

Always surface `error.request_id` / `X-Request-ID` in support UI.

---

## 10. Integration checklist

- [ ] Postgres has `docs/unified_schema.sql` applied (manual)  
- [ ] `.env`: real `DATABASE_URL` (asyncpg; URL-encode `@` in passwords) + MinIO  
- [ ] `GET /health` and `GET /health/ready` OK  
- [ ] MinIO reachable; API can create bucket  
- [ ] Do **not** run Alembic against this DB  
- [ ] Network: not public without your auth layer; CORS/proxy if browser SPA  
- [ ] Instant crawl UI: create → poll → pages → documents → **version body**  
- [ ] Monitoring UI: preflight → source → start/pause → events  
- [ ] Document changes: `/changes` + `/diff`  
- [ ] Intelligence UI: entities + stories (accept no mention API)  
- [ ] Search: `POST /search` (+ optional instant)  
- [ ] Display `X-Request-ID` on failures  
- [ ] Bookmark `/docs` for contract drift  

### Known product gaps

| Gap | Status |
|---|---|
| Authentication / authorization | Missing |
| CORS middleware | Missing |
| WebSockets / SSE / webhooks | Missing |
| Separate crawl workers | Missing (in-process only) |
| Document body on list/detail | Use versions endpoint |
| Entity mention detail API | Missing |
| Cursor / limit on most lists | Documents hard-capped at 50; others mostly unbounded |
| Multi-tenant isolation | Missing |

---

## Related repo files

- APIs: `app/api/v1/*.py`
- Request/response schemas: `app/schemas/*.py`
- ORM / STI models: `app/db/models/*.py`
- DB contract (apply manually): `docs/unified_schema.sql`
- Local / ops overview: `README.md`
