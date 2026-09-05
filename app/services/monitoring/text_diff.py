"""Paragraph-aware text diff (spec Phase 7 sections 18-19). Stdlib
`difflib.SequenceMatcher` over paragraph tokens rather than lines or
characters -- a paragraph is the smallest unit that means anything for
article/listing/forum-post text, and diffing at that granularity avoids
generating a line-by-line diff for every single body-text change (section
18: "do not generate massive diffs for every document; store compact
summaries").
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.services.deduplication.dedup import normalize_text

_PARAGRAPH_SPLIT = "\n\n"


@dataclass
class ParagraphDiff:
    paragraphs_added: int
    paragraphs_removed: int
    paragraphs_modified: int
    paragraphs_unchanged: int


def split_paragraphs(text: str) -> list[str]:
    """Whitespace-normalized per paragraph so `Hello  world` and
    `Hello world` never register as different paragraphs (spec section 19)."""
    raw = text.split(_PARAGRAPH_SPLIT) if _PARAGRAPH_SPLIT in text else text.split("\n")
    return [normalize_text(p) for p in raw if normalize_text(p)]


def diff_paragraphs(old_text: str, new_text: str) -> ParagraphDiff:
    old_paragraphs = split_paragraphs(old_text)
    new_paragraphs = split_paragraphs(new_text)

    matcher = SequenceMatcher(a=old_paragraphs, b=new_paragraphs, autojunk=False)
    added = removed = modified = unchanged = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged += i2 - i1
        elif tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            # pair up 1:1 as "modified"; any length imbalance is a genuine
            # add/remove on top of the paired replacement.
            pair_count = min(i2 - i1, j2 - j1)
            modified += pair_count
            removed += (i2 - i1) - pair_count
            added += (j2 - j1) - pair_count

    return ParagraphDiff(
        paragraphs_added=added, paragraphs_removed=removed,
        paragraphs_modified=modified, paragraphs_unchanged=unchanged,
    )
