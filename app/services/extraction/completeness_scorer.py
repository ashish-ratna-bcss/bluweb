"""Extraction completeness (Phase 8.1 section 19) -- deliberately separate
from `quality_scorer.py`. Quality asks "is what we got clean?"; completeness
asks "how much of what the page actually offers did we get?" A short,
well-formed excerpt of a much longer article scores high on quality and low
on completeness -- conflating the two would hide that gap.

Deterministic, structural signals only (per Phase 8.1 section 32's ban on
ad-hoc ML): compares the extracted result against structural evidence
already computed elsewhere in this crawl (HTML analysis, pagination,
discovered links) rather than fetching anything extra to check.
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this many extra internal links beyond what got discovered as
# listings/pagination, a page "probably doesn't have much more to give" --
# above it, unclaimed links are a real signal that content was left behind
# (e.g. an index page whose repeated-item detector only caught a subset).
_MANY_UNCLAIMED_LINKS = 20


@dataclass
class CompletenessScore:
    overall: float
    text_coverage: float  # extracted body length vs. the page's own visible-text length
    pagination_uncrawled: bool  # a next page was detected but this run didn't/couldn't follow it
    unclaimed_link_ratio: float  # links on the page that ended up in neither the body nor a listings[] item


def score_completeness(
    *, extracted_body_chars: int, page_meaningful_text_chars: int,
    listing_count: int = 0, internal_link_count: int = 0, pagination_detected: bool = False,
) -> CompletenessScore:
    """All counts are already-computed structural facts from this same
    crawl (HTMLAnalysisResult, index_extractor's listing_count, pagination
    detection) -- no new parsing happens here."""
    if page_meaningful_text_chars <= 0:
        text_coverage = 1.0 if extracted_body_chars > 0 else 0.0
    else:
        # A structured extractor (forum/listing/index) legitimately
        # produces a body shorter than the page's raw visible text (that
        # text includes nav/prices/timestamps the body doesn't repeat) --
        # capped at 1.0 rather than trying to guess "expected" body size.
        text_coverage = min(1.0, extracted_body_chars / page_meaningful_text_chars)

    # A page whose extractor found listing items is judged by how much of
    # the page's discoverable links it turned into structured items, not by
    # text coverage (an index page's "body" is a rendering convenience, not
    # the real content).
    if listing_count > 0 and internal_link_count > 0:
        unclaimed_link_ratio = max(0.0, (internal_link_count - listing_count) / internal_link_count)
        text_coverage = 1.0 - min(1.0, unclaimed_link_ratio)
    else:
        unclaimed_link_ratio = 0.0

    pagination_uncrawled = pagination_detected  # this scorer runs pre-frontier-drain; a detected next page hasn't been fetched yet within this same call

    overall = text_coverage
    if pagination_uncrawled:
        overall *= 0.85  # known-incomplete: there IS more content, just not yet fetched in this pass

    return CompletenessScore(
        overall=round(overall, 3), text_coverage=round(text_coverage, 3),
        pagination_uncrawled=pagination_uncrawled, unclaimed_link_ratio=round(unclaimed_link_ratio, 3),
    )
