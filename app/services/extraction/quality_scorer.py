"""Extraction quality scoring (spec Phase 9 section 16-17). Deterministic
signals only -- explicitly NOT "longer body = better" (section 16's own
warning): a 50-word page with a clean title/author/date and a 5000-word
page that's 90% navigation/boilerplate should not score the same way a
naive length-based metric would rate them.

Operates on the already-built `ArticleDocument` (any extractor's output --
article/forum/listing/index/generic/scrapling), not raw HTML: extraction
already happened by the time this runs, this only judges the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Deterministic contamination signals (spec section 17): phrases that are
# near-universally boilerplate/chrome, not article content, when they show
# up as a large fraction of the extracted body. Conservative and short on
# purpose -- false positives here would wrongly downgrade genuine content
# that happens to mention "subscribe" once.
_CONTAMINATION_PHRASES = (
    "cookie", "subscribe to our newsletter", "sign up for our newsletter",
    "all rights reserved", "terms of service", "privacy policy",
    "advertisement", "sponsored content", "log in to continue",
    "please enable javascript", "javascript is disabled",
)
_CONTAMINATION_RE = re.compile("|".join(re.escape(p) for p in _CONTAMINATION_PHRASES), re.IGNORECASE)

MIN_GOOD_BODY_CHARS = 200
IDEAL_BODY_CHARS = 1_500  # beyond this, more length adds nothing to the score -- the point where "reasonably substantial" is already established


@dataclass
class QualityScore:
    overall: float
    title: float
    body: float
    metadata: float
    images: float
    contamination_ratio: float  # 0 = clean, 1 = fully contaminated -- reported separately from the body score it feeds into


def score_extraction(document) -> QualityScore:
    """`document` is an ArticleDocument (duck-typed: any object with
    .headline/.body/.author/.published_at/.tags/.images/.fields_detected/
    .confidence works -- every extractor in this codebase already returns
    that shape)."""
    title_score = _title_score(document.headline)
    body_score, contamination = _body_score(document.body)
    metadata_score = _metadata_score(document)
    images_score = 1.0 if document.images else 0.0

    overall = (
        title_score * 0.20 + body_score * 0.45 + metadata_score * 0.25 + images_score * 0.10
    )

    return QualityScore(
        overall=round(overall, 3), title=round(title_score, 3), body=round(body_score, 3),
        metadata=round(metadata_score, 3), images=round(images_score, 3),
        contamination_ratio=round(contamination, 3),
    )


def _title_score(title: str | None) -> float:
    if not title:
        return 0.0
    length = len(title.strip())
    if length < 5:
        return 0.2  # present but suspiciously short -- probably a fragment, not a real title
    if length > 200:
        return 0.5  # present but suspiciously long -- probably grabbed the wrong element
    return 1.0


def _body_score(body: str) -> tuple[float, float]:
    if not body or not body.strip():
        return 0.0, 0.0

    length_score = min(1.0, len(body) / IDEAL_BODY_CHARS) if len(body) >= MIN_GOOD_BODY_CHARS else len(body) / MIN_GOOD_BODY_CHARS * 0.5

    paragraphs = [p for p in body.split("\n\n") if p.strip()] or [body]
    density_score = min(1.0, sum(len(p) for p in paragraphs) / max(1, len(paragraphs)) / 80)  # avg paragraph length as a density proxy -- one giant unbroken blob or many one-word fragments both score lower

    contamination_hits = len(_CONTAMINATION_RE.findall(body))
    contamination_ratio = min(1.0, contamination_hits / 5)  # 5+ boilerplate phrases in one body is a strong contamination signal regardless of body length
    contamination_penalty = 1.0 - contamination_ratio

    body_score = (length_score * 0.5 + density_score * 0.5) * contamination_penalty
    return body_score, contamination_ratio


def _metadata_score(document) -> float:
    fields = ("author", "published_at", "publisher", "section", "canonical_url")
    present = sum(1 for f in fields if getattr(document, f, None))
    tag_bonus = 0.1 if getattr(document, "tags", None) else 0.0
    return min(1.0, present / len(fields) + tag_bonus)
