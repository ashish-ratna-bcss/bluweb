from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PreflightRequest(BaseModel):
    url: str = Field(..., examples=["https://example-news-site.com"])


class CapabilitySchema(BaseModel):
    score: int
    confidence: str
    discovery_score: int
    fetch_score: int
    extraction_score: int
    rendering_score: int
    content_type_score: int
    # Deterministic meaning — NOT "scrape coverage".
    score_semantics: str = (
        "Weighted 0-100 capability estimate: discovery 30% + fetch 25% + "
        "extraction 25% + rendering 10% + content_type 10%. "
        "Confidence is sample-size based (HIGH≥8, MEDIUM≥3, else LOW). "
        "Not claimed percentage of pages successfully scraped."
    )

class DiscoverySchema(BaseModel):
    sitemap: bool
    rss: bool
    atom: bool
    html_links: bool
    estimated_discoverable_urls: int | None


class FetchSchema(BaseModel):
    http: bool
    browser: bool
    recommended: str


class ContentSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    html: bool
    pdf: bool
    json_: bool = Field(alias="json")
    xml: bool
    images: bool


class ExtractionSchema(BaseModel):
    title: float
    author: float
    date: float
    body: float


class SampleSchema(BaseModel):
    tested: int
    fetched: int
    extractable: int


class PreflightReportResponse(BaseModel):
    preflight_id: UUID
    url: str
    final_url: str | None
    status: str
    capability: CapabilitySchema
    discovery: DiscoverySchema
    fetch: FetchSchema
    content: ContentSchema
    extraction: ExtractionSchema
    sample: SampleSchema
    limitations: list[str]
    recommendations: list[str]
    duration_ms: float
    error: str | None
    created_at: datetime
    expires_at: datetime

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "preflight_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "url": "https://example-news-site.com",
                    "final_url": "https://example-news-site.com/",
                    "status": "completed",
                    "capability": {
                        "score": 91,
                        "confidence": "high",
                        "discovery_score": 94,
                        "fetch_score": 98,
                        "extraction_score": 91,
                        "rendering_score": 90,
                        "content_type_score": 80,
                    },
                    "discovery": {
                        "sitemap": True,
                        "rss": True,
                        "atom": False,
                        "html_links": True,
                        "estimated_discoverable_urls": 1240,
                    },
                    "fetch": {"http": True, "browser": True, "recommended": "http"},
                    "content": {"html": True, "pdf": True, "json": False, "xml": True, "images": True},
                    "extraction": {"title": 0.98, "author": 0.91, "date": 0.95, "body": 0.94},
                    "sample": {"tested": 15, "fetched": 15, "extractable": 14},
                    "limitations": [],
                    "recommendations": ["Use HTTP crawler for primary collection"],
                    "duration_ms": 4231.5,
                    "error": None,
                    "created_at": "2026-09-04T12:00:00Z",
                    "expires_at": "2026-09-05T12:00:00Z",
                }
            ]
        }
    }
