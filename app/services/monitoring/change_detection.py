"""ChangeDetectionService (spec Phase 7 section 5): the single place a
previously-stored document is compared against a freshly extracted one.
Pure functions, DB-independent -- same pattern as domain_profile_service.py
and domain_policy_service.py -- so it's unit-testable without a database
and crawl_engine.py stays the only caller that knows about ORM rows.

`similarity` vs `change_confidence` (spec section 34): `similarity` is a
fingerprint-derived closeness score (SimHash Hamming distance for body
text, or 1.0/0.0 for exact scalar-field comparisons) -- how close the two
documents are. `change_confidence` is how certain THIS deterministic
ruleset is that its own `change_type` classification is correct, based on
how strong the signal it used was (an exact string diff on a scalar field
is more certain than a SimHash-tier-only generic-page fallback, or a forum
post matched by content hash instead of a real post_id). Neither is an ML
probability -- both are computed from fixed, documented rules.
"""

from __future__ import annotations

from app.services.classification.page_classifier import PageType
from app.services.deduplication.dedup import normalize_text
from app.services.monitoring.field_diff import diff_list, diff_number, diff_scalar
from app.services.monitoring.fingerprints import metadata_hash, simhash_distance_tier, simhash_similarity
from app.services.monitoring.models import ChangeResult, ChangeType, DocumentSnapshot, Severity
from app.services.monitoring.text_diff import diff_paragraphs

_FORUM_TYPES = frozenset({PageType.FORUM_THREAD.value, PageType.FORUM_INDEX.value, PageType.DISCUSSION_THREAD.value})
_NEWS_TYPES = frozenset({PageType.NEWS_ARTICLE.value, PageType.BLOG_POST.value})


def detect_change(*, previous: DocumentSnapshot, current: DocumentSnapshot, page_type: str | None) -> ChangeResult:
    """Cheapest-first per spec section 39: an exact scalar+body match short
    circuits before any page-type-specific work runs."""
    if _is_exactly_equal(previous, current):
        return _no_change(previous, current)

    if page_type in _NEWS_TYPES:
        return _detect_news_change(previous, current)
    if page_type in _FORUM_TYPES:
        return _detect_forum_change(previous, current)
    if page_type == PageType.CLASSIFIED_LISTING.value:
        return _detect_listing_change(previous, current)
    if page_type == PageType.CLASSIFIED_INDEX.value:
        return _detect_index_change(previous, current)
    return _detect_generic_change(previous, current)


def _is_exactly_equal(previous: DocumentSnapshot, current: DocumentSnapshot) -> bool:
    return (
        normalize_text(previous.title or "") == normalize_text(current.title or "")
        and normalize_text(previous.body) == normalize_text(current.body)
        and (previous.author or "") == (current.author or "")
        and previous.published_at == current.published_at
        and metadata_hash(previous.metadata) == metadata_hash(current.metadata)
        and previous.images == current.images
    )


def _no_change(previous: DocumentSnapshot, current: DocumentSnapshot) -> ChangeResult:
    similarity = simhash_similarity(previous.simhash, current.simhash) if previous.simhash and current.simhash else 1.0
    return ChangeResult(
        changed=False, change_type=ChangeType.NO_CHANGE, severity=Severity.NONE,
        similarity=similarity, change_confidence=1.0, reasons=["no meaningful difference detected"],
    )


# -- news / blog --------------------------------------------------------

def _detect_news_change(previous: DocumentSnapshot, current: DocumentSnapshot) -> ChangeResult:
    changed_fields: list[str] = []
    diff: dict = {}
    reasons: list[str] = []

    title_diff = diff_scalar(previous.title, current.title)
    if title_diff:
        changed_fields.append("title")
        diff["title"] = title_diff
        reasons.append("headline changed")

    author_diff = diff_scalar(previous.author, current.author)
    if author_diff:
        changed_fields.append("author")
        diff["author"] = author_diff
        reasons.append("author changed")

    pub_diff = diff_scalar(_iso(previous.published_at), _iso(current.published_at))
    if pub_diff:
        changed_fields.append("published_at")
        diff["published_at"] = pub_diff
        reasons.append("publication date changed")

    body_changed = normalize_text(previous.body) != normalize_text(current.body)
    para_diff = None
    if body_changed:
        para_diff = diff_paragraphs(previous.body, current.body)
        changed_fields.append("body")
        diff["body"] = {
            "paragraphs_added": para_diff.paragraphs_added,
            "paragraphs_removed": para_diff.paragraphs_removed,
            "paragraphs_modified": para_diff.paragraphs_modified,
        }
        reasons.append("article body changed")

    images_diff = diff_list(previous.images, current.images)
    if images_diff["added"] or images_diff["removed"]:
        changed_fields.append("images")
        diff["images"] = images_diff

    tags_diff = diff_list(previous.metadata.get("tags", []), current.metadata.get("tags", []))
    if tags_diff["added"] or tags_diff["removed"]:
        changed_fields.append("tags")
        diff["tags"] = tags_diff

    if not changed_fields:
        return _no_change(previous, current)

    title_changed = "title" in changed_fields
    if title_changed and body_changed:
        change_type = ChangeType.ARTICLE_UPDATED
    elif title_changed:
        change_type = ChangeType.TITLE_CHANGED
    elif body_changed:
        change_type = ChangeType.CONTENT_CHANGED
    else:
        change_type = ChangeType.METADATA_CHANGED

    severity = _news_severity(title_changed, body_changed, para_diff)
    similarity = simhash_similarity(previous.simhash, current.simhash) if body_changed else 1.0

    return ChangeResult(
        changed=True, change_type=change_type, severity=severity, similarity=similarity,
        change_confidence=0.9,  # scalar-field diffs are exact string comparisons
        changed_fields=changed_fields, modified_fields=list(changed_fields), reasons=reasons, diff=diff,
    )


def _news_severity(title_changed: bool, body_changed: bool, para_diff) -> Severity:
    """Deterministic rules (spec section 20): headline+body together is the
    HIGH case explicitly required by section 58; a lone headline change is
    MEDIUM (significant but not a rewrite); body-only severity scales with
    how many paragraphs actually moved; pure metadata (author/date/images/
    tags with no title or body change) is LOW."""
    if title_changed and body_changed:
        return Severity.HIGH
    if title_changed:
        return Severity.MEDIUM
    if body_changed and para_diff is not None:
        total = para_diff.paragraphs_added + para_diff.paragraphs_removed + para_diff.paragraphs_modified
        if total >= 3:
            return Severity.HIGH
        if total >= 1:
            return Severity.MEDIUM
        return Severity.LOW
    return Severity.LOW


# -- forum ----------------------------------------------------------------

def _detect_forum_change(previous: DocumentSnapshot, current: DocumentSnapshot) -> ChangeResult:
    prev_posts = previous.metadata.get("forum_posts", []) or []
    curr_posts = current.metadata.get("forum_posts", []) or []

    prev_by_id = {_post_identity(p): p for p in prev_posts}
    curr_by_id = {_post_identity(p): p for p in curr_posts}
    real_ids_available = any(p.get("post_id") for p in prev_posts + curr_posts)

    new_ids = [pid for pid in curr_by_id if pid not in prev_by_id]
    removed_ids = [pid for pid in prev_by_id if pid not in curr_by_id]
    edited_ids = [
        pid for pid in curr_by_id
        if pid in prev_by_id
        and normalize_text(curr_by_id[pid].get("body", "")) != normalize_text(prev_by_id[pid].get("body", ""))
    ]
    prev_authors = {p.get("author") for p in prev_posts if p.get("author")}
    new_participants = {curr_by_id[pid].get("author") for pid in new_ids if curr_by_id[pid].get("author")} - prev_authors

    changed_fields: list[str] = []
    diff: dict = {}
    reasons: list[str] = []

    title_diff = diff_scalar(previous.title, current.title)
    if title_diff:
        changed_fields.append("title")
        diff["title"] = title_diff
        reasons.append("thread title changed")

    if new_ids:
        changed_fields.append("posts")
        diff["new_posts"] = len(new_ids)
        reasons.append(f"{len(new_ids)} new post(s)")
    if removed_ids:
        changed_fields.append("posts")
        diff["removed_posts"] = len(removed_ids)
        reasons.append(f"{len(removed_ids)} post(s) removed")
    if edited_ids:
        changed_fields.append("posts")
        diff["edited_posts"] = len(edited_ids)
        reasons.append(f"{len(edited_ids)} post(s) edited")
    if new_participants:
        diff["new_participants"] = len(new_participants)

    if not changed_fields:
        return _no_change(previous, current)

    if removed_ids:
        change_type = ChangeType.POST_REMOVED
        severity = Severity.HIGH
    elif edited_ids:
        change_type = ChangeType.POST_EDITED
        severity = Severity.MEDIUM
    elif new_ids:
        change_type = ChangeType.NEW_POSTS
        severity = Severity.MEDIUM if len(new_ids) < 10 else Severity.HIGH
    else:
        change_type = ChangeType.TITLE_CHANGED
        severity = Severity.LOW

    matched = len(set(prev_by_id) & set(curr_by_id))
    total = len(set(prev_by_id) | set(curr_by_id)) or 1
    similarity = matched / total
    confidence = 0.9 if real_ids_available else 0.6  # content-hash identity is a weaker signal than a real post_id

    return ChangeResult(
        changed=True, change_type=change_type, severity=severity, similarity=similarity,
        change_confidence=confidence, changed_fields=changed_fields,
        added_fields=["posts"] if new_ids else [], removed_fields=["posts"] if removed_ids else [],
        modified_fields=["posts"] if edited_ids else [], reasons=reasons, diff=diff,
    )


def _post_identity(post: dict) -> str:
    if post.get("post_id"):
        return f"id:{post['post_id']}"
    return f"content:{normalize_text((post.get('author') or '') + '|' + (post.get('body') or ''))}"


# -- classified listing (single page) --------------------------------------

def _detect_listing_change(previous: DocumentSnapshot, current: DocumentSnapshot) -> ChangeResult:
    prev_meta, curr_meta = previous.metadata, current.metadata
    changed_fields: list[str] = []
    diff: dict = {}
    reasons: list[str] = []

    price_diff = diff_number(_to_number(prev_meta.get("price")), _to_number(curr_meta.get("price")))
    if price_diff:
        changed_fields.append("price")
        diff["price"] = price_diff
        reasons.append("price changed")

    status_diff = diff_scalar(prev_meta.get("status"), curr_meta.get("status"))
    if status_diff:
        changed_fields.append("status")
        diff["status"] = status_diff
        reasons.append("status changed")

    location_diff = diff_scalar(prev_meta.get("location"), curr_meta.get("location"))
    if location_diff:
        changed_fields.append("location")
        diff["location"] = location_diff

    title_diff = diff_scalar(previous.title, current.title)
    if title_diff:
        changed_fields.append("title")
        diff["title"] = title_diff

    desc_changed = normalize_text(previous.body) != normalize_text(current.body)
    if desc_changed:
        changed_fields.append("description")
        para_diff = diff_paragraphs(previous.body, current.body)
        diff["description"] = {
            "paragraphs_added": para_diff.paragraphs_added,
            "paragraphs_removed": para_diff.paragraphs_removed,
            "paragraphs_modified": para_diff.paragraphs_modified,
        }
        reasons.append("description changed")

    images_diff = diff_list(previous.images, current.images)
    if images_diff["added"] or images_diff["removed"]:
        changed_fields.append("images")
        diff["images"] = images_diff

    attrs_diff = diff_list(
        list((prev_meta.get("attributes") or {}).keys()), list((curr_meta.get("attributes") or {}).keys())
    )
    if prev_meta.get("attributes") != curr_meta.get("attributes"):
        changed_fields.append("attributes")
        diff["attributes"] = {"old": prev_meta.get("attributes"), "new": curr_meta.get("attributes")}

    if not changed_fields:
        return _no_change(previous, current)

    if price_diff:
        change_type = ChangeType.PRICE_CHANGED
        severity = Severity.HIGH
    elif status_diff:
        change_type = ChangeType.STATUS_CHANGED
        severity = Severity.HIGH
    elif desc_changed:
        change_type = ChangeType.DESCRIPTION_CHANGED
        severity = Severity.MEDIUM
    else:
        change_type = ChangeType.METADATA_CHANGED
        severity = Severity.LOW

    similarity = simhash_similarity(previous.simhash, current.simhash) if desc_changed else 1.0

    return ChangeResult(
        changed=True, change_type=change_type, severity=severity, similarity=similarity,
        change_confidence=0.9, changed_fields=changed_fields, modified_fields=list(changed_fields),
        reasons=reasons, diff=diff,
    )


def _to_number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


# -- classified index (collection of listings) -----------------------------

def _detect_index_change(previous: DocumentSnapshot, current: DocumentSnapshot) -> ChangeResult:
    """Compares listing collections by stable id (spec section 14).
    `metadata["listings"]` is now populated for real by
    `extraction_router.py::_index_to_document` (spec Phase 9 section 18,
    `index_extractor.py`'s generic repeated-item detection) -- this was a
    documented gap through Phase 7/8 (comparator built and tested against
    a synthetic shape with no real producer); it's closed as of Phase 9."""
    prev_listings = {l["id"]: l for l in (previous.metadata.get("listings") or []) if l.get("id")}
    curr_listings = {l["id"]: l for l in (current.metadata.get("listings") or []) if l.get("id")}

    if not prev_listings and not curr_listings:
        return _detect_generic_change(previous, current)

    added = [lid for lid in curr_listings if lid not in prev_listings]
    removed = [lid for lid in prev_listings if lid not in curr_listings]
    price_changed = [
        lid for lid in curr_listings if lid in prev_listings and curr_listings[lid].get("price") != prev_listings[lid].get("price")
    ]
    status_changed = [
        lid for lid in curr_listings if lid in prev_listings and curr_listings[lid].get("status") != prev_listings[lid].get("status")
    ]

    changed_fields: list[str] = []
    diff: dict = {}
    reasons: list[str] = []
    if added:
        changed_fields.append("listings")
        diff["listings_added"] = len(added)
        reasons.append(f"{len(added)} listing(s) added")
    if removed:
        changed_fields.append("listings")
        diff["listings_removed"] = len(removed)
        reasons.append(f"{len(removed)} listing(s) removed")
    if price_changed:
        changed_fields.append("listings")
        diff["listings_price_changed"] = len(price_changed)
    if status_changed:
        changed_fields.append("listings")
        diff["listings_status_changed"] = len(status_changed)

    if not changed_fields:
        return _no_change(previous, current)

    if added and not removed:
        change_type = ChangeType.LISTING_ADDED
    elif removed and not added:
        change_type = ChangeType.LISTING_REMOVED
    elif price_changed:
        change_type = ChangeType.PRICE_CHANGED
    elif status_changed:
        change_type = ChangeType.STATUS_CHANGED
    else:
        change_type = ChangeType.STRUCTURE_CHANGED

    matched = len(set(prev_listings) & set(curr_listings))
    total = len(set(prev_listings) | set(curr_listings)) or 1
    similarity = matched / total
    severity = Severity.HIGH if (added or removed or price_changed) else Severity.MEDIUM

    return ChangeResult(
        changed=True, change_type=change_type, severity=severity, similarity=similarity,
        change_confidence=0.85, changed_fields=changed_fields,
        added_fields=["listings"] if added else [], removed_fields=["listings"] if removed else [],
        reasons=reasons, diff=diff,
    )


# -- generic fallback -------------------------------------------------------

def _detect_generic_change(previous: DocumentSnapshot, current: DocumentSnapshot) -> ChangeResult:
    title_diff = diff_scalar(previous.title, current.title)
    body_changed = normalize_text(previous.body) != normalize_text(current.body)

    if not title_diff and not body_changed:
        return _no_change(previous, current)

    tier = simhash_distance_tier(previous.simhash, current.simhash)
    similarity = simhash_similarity(previous.simhash, current.simhash)

    changed_fields: list[str] = []
    diff: dict = {}
    reasons = [f"simhash distance tier: {tier}"]
    if title_diff:
        changed_fields.append("title")
        diff["title"] = title_diff
    if body_changed:
        changed_fields.append("body")
        para_diff = diff_paragraphs(previous.body, current.body)
        diff["body"] = {
            "paragraphs_added": para_diff.paragraphs_added,
            "paragraphs_removed": para_diff.paragraphs_removed,
            "paragraphs_modified": para_diff.paragraphs_modified,
        }

    # Metadata/title-only changes must not escalate to HIGH via a missing
    # simhash (treated as near_identical upstream). Body rewrites can still
    # reach MEDIUM/HIGH from a real distance tier.
    if body_changed:
        change_type = ChangeType.CONTENT_CHANGED
        if tier == "near_identical":
            severity = Severity.LOW
        elif tier == "moderate":
            severity = Severity.MEDIUM
        else:
            severity = Severity.HIGH
    else:
        change_type = ChangeType.METADATA_CHANGED
        severity = Severity.LOW

    return ChangeResult(
        changed=True, change_type=change_type, severity=severity, similarity=similarity,
        change_confidence=0.5,  # least page-type-specific signal of any strategy here
        changed_fields=changed_fields, modified_fields=list(changed_fields), reasons=reasons, diff=diff,
    )


def _iso(value) -> str | None:
    return value.isoformat() if value else None
