# Web Application Integration Guide

Integration guide for connecting a frontend / web application to the **Bluweb (Web Intelligence Collection Backend)** service.

This document is based on the **actual implemented APIs and models** in this repository (`app/api/v1/*`, `app/schemas/*`, `app/db/models/*`). Invented endpoints, auth schemes, WebSockets, or workers are explicitly marked as **not implemented**.

---

## 1. Architecture

```text
Web Application (browser / SPA / SSR)
      │
      ▼
Frontend (your app)
      │  HTTP JSON  (no auth today)
      ▼
FastAPI API  (uvicorn app.main:app :8000)
      │
      ├── /health*              liveness / readiness / metrics
      ├── /api/v1/preflight     discovery capability assessment
      ├── /api/v1/crawls        instant crawl jobs (in-process asyncio)
      ├── /api/v1/documents     extracted pages, versions, diffs, changes
      ├── /api/v1/sources       continuous monitoring registration
      ├── /api/v1/search        Postgres full-text search (+ instant crawl+search)
      ├── /api/v1/domains       domain learning / capabilities
      └── /api/v1/entities|stories   NER + story intelligence (read APIs)
             │
             ├── PostgreSQL   documents, versions, changes, entities, stories,
             │                crawls, sources, domain stats, …
             └── MinIO        raw HTML/bytes (referenced by raw_artifacts)
```

**Important implementation facts**

| Concern | Actual behavior |
|---|---|
| API process | Single uvicorn process |
| Crawl execution | `asyncio.create_task` inside the API process (not a separate worker) |
| Monitoring scheduler | In-process loop started in FastAPI lifespan (`scheduler_loop`) |
| Auth | **Not currently implemented** |
| WebSockets / SSE / webhooks | **Not currently implemented** — poll HTTP |
| OpenAPI | `GET /docs`, `GET /openapi.json` |
| Root | `GET /` redirects to `/docs` |

---

## 2. API Inventory

Base URL (local): `http://127.0.0.1:8000`

**Common headers:** `Content-Type: application/json` for POST/PATCH bodies.  
**Authentication:** none.  
**Request ID:** responses include `X-Request-ID`; errors include `error.request_id`.

**Standard error shape**

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

Validation failures (Pydantic/FastAPI) typically return **422** with FastAPI’s default `{"detail":[…]}` shape (not the `APIError` envelope).

---

### 2.1 Health

| Method | Path | Purpose | Status |
|---|---|---|---|
| GET | `/health` | Liveness (no DB) | 200 `{"status":"ok"}` |
| GET | `/health/live` | Liveness alias | 200 `{"status":"alive"}` |
| GET | `/health/ready` | Postgres `SELECT 1` | 200 ready / **503** not_ready |
| GET | `/metrics` | Prometheus text | 200 |

Example:

```bash
curl -s http://127.0.0.1:8000/health/ready
```

---

### 2.2 Preflight — ` /api/v1/preflight`

#### `POST /api/v1/preflight`

- **Purpose:** Run synchronous capability assessment (DNS, HTTP, robots, sitemap, feeds, sample extract, optional browser).
- **Body:** `{ "url": "https://example.com" }`
- **Status:** **201**
- **Response:** `PreflightReportResponse` (`preflight_id`, `status`, `capability`, `discovery`, `fetch`, `content`, `extraction`, `sample`, `limitations`, `recommendations`, `duration_ms`, `created_at`, `expires_at`, …)
- **Errors:** `400 URL_BLOCKED` (SSRF / scheme)

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/preflight \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://bluecloudsoftech.com/"}'
```

#### `GET /api/v1/preflight/{preflight_id}`

- **Purpose:** Fetch stored report
- **Status:** 200 / **404** `PREFLIGHT_NOT_FOUND`

---

### 2.3 Crawls — `/api/v1/crawls`

#### `POST /api/v1/crawls` → **202 Accepted**

- **Body:**
  ```json
  {
    "url": "https://example.com/",
    "max_pages": 10,
    "max_depth": 1,
    "same_domain_only": true
  }
  ```
- **Behavior:** Creates `crawl_jobs` row, returns immediately, runs discovery + `run_crawl` in background. Instant crawl **skips** mandatory preflight (by design).
- **Response:** `CrawlJobResponse` (`crawl_id`, `status` usually `queued`, `seed_url`, limits, timestamps, `statistics`, `error`)
- **Errors:** `400 URL_BLOCKED`, `422` validation

#### `GET /api/v1/crawls`

- List jobs → `list[CrawlJobResponse]`

#### `GET /api/v1/crawls/{crawl_id}`

- Job detail / poll status (`queued|running|completed|cancelled|failed|cancelling`)
- **404** `CRAWL_NOT_FOUND`

#### `GET /api/v1/crawls/{crawl_id}/pages`

- Per-URL attempts: `url`, `status`, `fetch_strategy`, `http_status`, `document_id`, `error`, …

#### `POST /api/v1/crawls/{crawl_id}/cancel`

- Requests cancel if `queued`/`running`; returns updated job

**Poll pattern (required — no push):**

```bash
CRAWL_ID=…
curl -s http://127.0.0.1:8000/api/v1/crawls/$CRAWL_ID
# repeat until status is completed|cancelled|failed
```

---

### 2.4 Documents — `/api/v1/documents`

| Method | Path | Purpose |
|---|---|---|
| GET | `/documents` | List; query: `crawl_id`, `domain`, `page_type` |
| GET | `/documents/{id}` | Detail + latest change + `entity_ids` + optional `story_id` |
| GET | `/documents/{id}/changes` | Stored change events; query: `severity`, `change_type`, `from`, `to` |
| GET | `/documents/{id}/versions` | Version summaries |
| GET | `/documents/{id}/versions/{n}` | Full version body |
| GET | `/documents/{id}/diff?from_version=&to_version=` | Unified line diff |

**DocumentResponse (list/detail)** includes: `document_id`, `url`, `domain`, `title`, `author`, `published_at`, `language`, `page_type`, `extraction_method`, `extraction_confidence`, `current_version`, `collected_at`, change summary fields, `entity_ids`, `story_id`, `story_match_confidence`.

**Note:** List/detail do **not** return full `current_content`. Full text is on **version** endpoints (`DocumentVersionResponse.content`). That is an integration gap for “document explorer body” UX — use `GET …/versions/{current_version}` or search snippets.

**Errors:** `404 DOCUMENT_NOT_FOUND`, `404 VERSION_NOT_FOUND`

---

### 2.5 Sources (monitoring) — `/api/v1/sources`

| Method | Path | Status | Purpose |
|---|---|---|---|
| POST | `/sources` | 201 | Register monitored source (**requires valid, non-expired preflight**, same domain) |
| GET | `/sources` | 200 | List |
| GET | `/sources/{id}` | 200/404 | Detail |
| PATCH | `/sources/{id}` | 200 | Update name/type/policy |
| DELETE | `/sources/{id}` | 204 | Delete |
| POST | `/sources/{id}/start` | 200 | Activate (`active`) — alias `/resume` |
| POST | `/sources/{id}/pause` | 200 | Pause |
| GET | `/sources/{id}/events` | 200 | Monitoring events NEW/UPDATED/UNCHANGED/REMOVED/… |
| GET | `/sources/{id}/statistics` | 200 | Interval, counters, doc/event totals |

**Create body:**

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

**Errors:** `400 URL_BLOCKED`, `400 PREFLIGHT_EXPIRED`, `400 PREFLIGHT_URL_MISMATCH`, `404 PREFLIGHT_NOT_FOUND`, `409 SOURCE_ALREADY_EXISTS`

Scheduler: in-process; picks `next_crawl_at <= now` roughly every `SCHEDULER_POLL_INTERVAL_SECONDS` (default 30).

---

### 2.6 Search — `/api/v1/search`

#### `POST /api/v1/search`

```json
{
  "query": "cybersecurity",
  "domain": "bluecloudsoftech.com",
  "source_id": null,
  "language": null,
  "date_from": null,
  "date_to": null,
  "limit": 20,
  "offset": 0
}
```

→ `{ "total": N, "results": [ { document_id, title, url, domain, snippet, … } ] }`

#### `POST /api/v1/search/instant`

Starts a crawl, waits up to `wait_seconds`, returns partial search hits + `crawl_id` / `crawl_status` / `note`. Client should continue polling crawl + search.

---

### 2.7 Domains — `/api/v1/domains`

| Method | Path | Purpose |
|---|---|---|
| GET | `/domains/{domain}/profile` | Health, preferred strategy, patterns, politeness |
| GET | `/domains/{domain}/capabilities` | Interpreted discovery/fetch/browser/extraction capability |

Domain is the hostname/domain string as stored (e.g. `bluecloudsoftech.com`).

---

### 2.8 Intelligence — entities & stories

Mounted at `/api/v1` (no extra prefix beyond path).

| Method | Path | Purpose |
|---|---|---|
| GET | `/entities` | List; query `entity_type` |
| GET | `/entities/{id}` | Detail |
| GET | `/entities/{id}/documents` | `{ entity_id, document_ids: [] }` |
| GET | `/entities/{id}/stories` | Stories linked to entity |
| GET | `/stories` | List; query `status`, `from`, `to` |
| GET | `/stories/{id}` | Summary |
| GET | `/stories/{id}/documents` | Attachment scores / evidence |
| GET | `/stories/{id}/entities` | Entities in story |
| GET | `/stories/{id}/sources` | Domains contributing |
| GET | `/stories/{id}/timeline` | Ordered by published_at / attach time |

**Errors:** `404 ENTITY_NOT_FOUND`, `404 STORY_NOT_FOUND`

**Missing (not implemented):**

- `GET /entities/{id}/mentions` or document mention detail API (mentions exist in DB; only `entity_ids` on document detail)
- Write/update/delete for entities/stories
- Streaming intelligence progress events

Intelligence runs **inside crawl** on NEW/UPDATED documents only (not on UNCHANGED). Failures are logged; crawl still succeeds.

---

## 3. End-to-End Web Workflows

### 3.1 Add / register a website (monitoring)

1. `POST /api/v1/preflight` with URL  
2. Store `preflight_id` (must be unexpired)  
3. `POST /api/v1/sources` with `preflight_id`  
4. `POST /api/v1/sources/{id}/start`  
5. Poll `GET /api/v1/sources/{id}` / `…/events` / `…/statistics`

### 3.2 Instant discovery + crawl (no source)

1. `POST /api/v1/crawls`  
2. Poll `GET /api/v1/crawls/{id}` until terminal status  
3. Optional: `GET /api/v1/crawls/{id}/pages`  
4. `GET /api/v1/documents?crawl_id=`  

### 3.3 Retrieve crawled pages / extracted content

1. List documents (`crawl_id` or `domain`)  
2. `GET /documents/{id}` for metadata + entity/story links  
3. `GET /documents/{id}/versions/{current_version}` for clean text body  

### 3.4 Detect updated content

1. Monitoring events: `GET /sources/{id}/events` (`UPDATED`, change_summary)  
2. Or `GET /documents/{id}/changes`  
3. Or `GET /documents/{id}/diff?from_version=&to_version=`  

### 3.5 Entities / NER / stories

1. After NEW/UPDATED crawl completes, `GET /entities`, `GET /stories`  
2. Drill: entity documents/stories; story documents/entities/timeline  
3. From a document: use `entity_ids` / `story_id` on detail response  

### 3.6 Search / filter

1. `POST /api/v1/search` with query + optional domain/source/date filters  
2. Or `POST /search/instant` for ad-hoc crawl+search  

### 3.7 Processing / failures

1. Crawl `status` + `error` + `statistics`  
2. Crawl pages `error` / `http_status`  
3. Source events `CRAWL_FAILED` / `REMOVED`  
4. `/metrics` for counters  
5. Server logs for `INTELLIGENCE_FAILED` (crawl still OK)

### 3.8 Retries

- **Not a first-class API.** Re-`POST /crawls` with same URL (may be UNCHANGED).  
- Monitoring reschedules via `next_crawl_at`.  
- Cancelled jobs are not auto-restarted.

---

## 4. Frontend Integration (screens → APIs)

| UI | Primary APIs |
|---|---|
| Dashboard | `/health/ready`, recent `GET /crawls`, `GET /sources`, `/metrics` |
| Website / source management | preflight + sources CRUD + start/pause |
| Crawl configuration | form → `POST /crawls` body fields |
| Crawl execution / status | poll `GET /crawls/{id}`, pages list, cancel |
| Page / document explorer | `GET /documents`, filters |
| Document detail | `GET /documents/{id}`, versions, changes, diff |
| Entity explorer | `GET /entities`, type filter |
| Entity detail | entity + documents + stories |
| Story explorer | `GET /stories` |
| Story detail | story + documents + entities + sources + timeline |
| Search | `POST /search`, optional instant |
| Domain ops | `/domains/{d}/profile`, `/capabilities` |
| Error monitoring | crawl errors, source events, metrics |

---

## 5. Authentication and Security

> **Not currently implemented** (API authn/authz).

What a web app must account for today:

- Treat the API as **trusted-network only** (localhost / private VPC / reverse proxy with your own auth).
- Do **not** expose `:8000` publicly without a gateway that adds auth, TLS, and rate limits.
- SSRF protections exist **server-side** (`URL_BLOCKED`) for crawl/preflight/source URLs — still validate user input in the UI.
- CORS is not specially configured in `app/main.py` for a separate SPA origin — you will likely need a reverse proxy or to add CORS middleware (not present now).
- Secrets (`HF_TOKEN`, DB, MinIO) stay server-side in `.env`.

---

## 6. Async Processing

| Topic | Supported approach |
|---|---|
| Long crawls | `202` + background task; **poll** job status |
| Progress | `statistics` on job + `/crawls/{id}/pages`; no % complete field |
| Workers | **None separate** — same process as API |
| WebSockets / SSE / webhooks | **Not implemented** |
| Retries | Manual re-POST; monitoring schedule |
| Failures | Terminal `failed` + `error`; page-level errors on pages list |
| Intelligence lag | After NEW/UPDATED; poll entities/stories; no job for NER alone |

**Recommended poll interval:** 1–2s while `queued`/`running`, backoff after 30s.

---

## 7. Data Model (as implemented)

```text
preflight_reports
        │ (required to create)
        ▼
     sources ──< source_urls
        │            monitoring_events
        │
        ▼
   crawl_jobs ── crawl_runs
        │         crawl_pages
        ▼
   documents ── document_versions ──► raw_artifacts ──► MinIO object
        │         document_changes
        │
        ├── entity_mentions ──► entities ── entity_aliases
        │
        └── story_documents ──► stories ── story_entities ──► entities

fetch_strategy_stats / url_pattern_stats  (per domain / pattern learning)
```

**Change lifecycle:** NEW → version 1 + intelligence; UPDATED → new version + `document_changes` + intelligence; UNCHANGED → no new version, **no** intelligence rerun.

---

## 8. Example Frontend Flow

```text
User pastes https://bluecloudsoftech.com/
        ↓
POST /api/v1/crawls  { url, max_pages: 1, max_depth: 0 }
        ↓ 202 { crawl_id, status: "queued" }
Frontend polls GET /api/v1/crawls/{crawl_id}
        ↓ status → "completed"
GET /api/v1/documents?crawl_id={crawl_id}
        ↓
GET /api/v1/documents/{document_id}
        ↓ entity_ids[], story_id?
GET /api/v1/documents/{id}/versions/{current_version}   ← body text
        ↓
GET /api/v1/entities / GET /api/v1/stories/{story_id}/…
```

Monitoring variant: preflight → create source → start → poll events.

---

## 9. Error Handling

| Situation | HTTP | Frontend action |
|---|---|---|
| Bad JSON / field validation | 422 | Show `detail` field errors |
| SSRF / blocked URL | 400 `URL_BLOCKED` | Block submit; explain policy |
| Not found | 404 `*_NOT_FOUND` | Empty state / navigate away |
| Source duplicate | 409 `SOURCE_ALREADY_EXISTS` | Link to existing source |
| Preflight expired | 400 `PREFLIGHT_EXPIRED` | Re-run preflight |
| Crawl failure | job `status=failed` | Show `error`; allow retry POST |
| Page fetch failure | page `status=failed` | Show per-URL error; rest may succeed |
| Intelligence failure | crawl still completed | Entities may be incomplete; retry crawl only if content changes |
| Ready check fail | 503 | Disable UI actions; show infra down |
| Timeouts | client-side | Keep polling crawl_id; don’t assume failure on slow sites |
| Rate limits | **Not implemented** on API | Add at gateway if public |
| Partial instant search | 200 with note | Continue poll crawl + search |
| Auth errors | **N/A** | Add when you put auth in front |

Always surface `error.request_id` / `X-Request-ID` in support UI.

---

## 10. Integration Checklist

- [ ] External Postgres has `docs/unified_schema.sql` applied (manual)  
- [ ] `.env` has real `DATABASE_URL` (asyncpg URL) + MinIO settings  
- [ ] API reachable; `GET /health` and `GET /health/ready` OK  
- [ ] MinIO up; bucket creatable by API  
- [ ] spaCy model and/or GLiNER cache if you need NER  
- [ ] Playwright Chromium if JS-heavy sites matter  
- [ ] **Do not** run `alembic upgrade head` against this database  
- [ ] Network policy: API not public without your auth layer  
- [ ] CORS / reverse proxy planned for SPA origin  
- [ ] Instant crawl UI: create + poll + pages + documents + version body  
- [ ] Monitoring UI: preflight → source → start/pause → events  
- [ ] Document change UI: changes + diff  
- [ ] Intelligence UI: entities + stories (+ accept mention API gap)  
- [ ] Search UI: `POST /search`  
- [ ] Domain ops (optional): profile/capabilities  
- [ ] Error + request_id display  
- [ ] Polling strategy documented for long crawls  
- [ ] OpenAPI (`/docs`) bookmarked for contract drift checks  

### Still missing for “complete” product-grade web integration

| Gap | Status |
|---|---|
| Authentication / authorization | Missing |
| CORS middleware | Missing |
| WebSockets/SSE/webhooks | Missing |
| Separate crawl workers / queue UI | Missing (in-process only) |
| Document body on list/detail | Partial — use versions endpoint |
| Entity mention detail API | Missing (DB has `entity_mentions`) |
| Paginated list APIs with cursors | Mostly unbounded lists |
| Multi-tenant isolation | Missing |

---

## Related repo files

- APIs: `app/api/v1/*.py`
- Schemas: `app/schemas/*.py`
- Models: `app/db/models/*.py`
- Unified schema proposal (eval only): `docs/unified_schema.sql`
- Local setup overview: `README.md` (Setup section)
