from app.services.deduplication.dedup import simhash64
from app.services.monitoring.change_detection import detect_change
from app.services.monitoring.models import ChangeType, DocumentSnapshot, Severity

_BASE_BODY = "Paragraph A text here with real content words.\n\nParagraph B text here also with content."


def _snap(**kwargs) -> DocumentSnapshot:
    defaults = dict(title="T", author=None, published_at=None, body=_BASE_BODY, images=[], metadata={}, simhash=None)
    defaults.update(kwargs)
    return DocumentSnapshot(**defaults)


# -- news / blog ------------------------------------------------------------

def test_news_headline_changed_only():
    prev = _snap(title="Old headline")
    curr = _snap(title="New headline")
    r = detect_change(previous=prev, current=curr, page_type="NEWS_ARTICLE")
    assert r.changed
    assert r.change_type == ChangeType.TITLE_CHANGED
    assert r.severity == Severity.MEDIUM
    assert r.changed_fields == ["title"]


def test_news_body_changed_only():
    prev = _snap(simhash=simhash64(_BASE_BODY))
    new_body = _BASE_BODY + "\n\nParagraph C is brand new."
    curr = _snap(body=new_body, simhash=simhash64(new_body))
    r = detect_change(previous=prev, current=curr, page_type="NEWS_ARTICLE")
    assert r.changed
    assert r.change_type == ChangeType.CONTENT_CHANGED
    assert r.diff["body"]["paragraphs_added"] == 1


def test_news_author_changed():
    prev = _snap(author="Jane Doe")
    curr = _snap(author="John Smith")
    r = detect_change(previous=prev, current=curr, page_type="NEWS_ARTICLE")
    assert r.changed
    assert "author" in r.changed_fields
    assert r.change_type == ChangeType.METADATA_CHANGED


def test_news_published_at_changed():
    from datetime import datetime, timezone
    prev = _snap(published_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    curr = _snap(published_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    r = detect_change(previous=prev, current=curr, page_type="NEWS_ARTICLE")
    assert r.changed
    assert "published_at" in r.changed_fields


def test_news_title_and_body_together_is_high_severity():
    prev = _snap(title="Police announce operation", simhash=simhash64(_BASE_BODY))
    new_body = _BASE_BODY + "\n\nParagraph C is brand new content."
    curr = _snap(title="Police announce major operation", body=new_body, simhash=simhash64(new_body))
    r = detect_change(previous=prev, current=curr, page_type="NEWS_ARTICLE")
    assert r.change_type == ChangeType.ARTICLE_UPDATED
    assert r.severity == Severity.HIGH


# -- forum --------------------------------------------------------------

def _posts(n, start=0):
    return [{"post_id": str(i), "author": f"user{i}", "body": f"post body number {i}"} for i in range(start, start + n)]


def test_forum_new_posts_detected_by_stable_id_not_position():
    prev = _snap(metadata={"forum_posts": _posts(10)})
    curr = _snap(metadata={"forum_posts": _posts(10) + _posts(3, start=10)})
    r = detect_change(previous=prev, current=curr, page_type="FORUM_THREAD")
    assert r.change_type == ChangeType.NEW_POSTS
    assert r.diff["new_posts"] == 3


def test_forum_edited_post_detected():
    posts = _posts(3)
    prev = _snap(metadata={"forum_posts": posts})
    edited = [dict(p) for p in posts]
    edited[1]["body"] = "this post was edited with new content"
    curr = _snap(metadata={"forum_posts": edited})
    r = detect_change(previous=prev, current=curr, page_type="FORUM_THREAD")
    assert r.change_type == ChangeType.POST_EDITED
    assert r.diff["edited_posts"] == 1


def test_forum_deleted_post_detected():
    posts = _posts(5)
    prev = _snap(metadata={"forum_posts": posts})
    curr = _snap(metadata={"forum_posts": posts[:-1]})
    r = detect_change(previous=prev, current=curr, page_type="FORUM_THREAD")
    assert r.change_type == ChangeType.POST_REMOVED
    assert r.diff["removed_posts"] == 1
    assert r.severity == Severity.HIGH


def test_forum_new_participant_tracked():
    posts = _posts(2)
    prev = _snap(metadata={"forum_posts": posts})
    curr = _snap(metadata={"forum_posts": posts + [{"post_id": "99", "author": "brand_new_user", "body": "hello everyone"}]})
    r = detect_change(previous=prev, current=curr, page_type="FORUM_THREAD")
    assert r.diff["new_participants"] == 1


def test_forum_no_change_when_posts_identical():
    posts = _posts(5)
    prev = _snap(metadata={"forum_posts": posts})
    curr = _snap(metadata={"forum_posts": list(posts)})
    r = detect_change(previous=prev, current=curr, page_type="FORUM_THREAD")
    assert not r.changed


def test_forum_reordered_posts_not_treated_as_modified():
    posts = _posts(5)
    prev = _snap(metadata={"forum_posts": posts})
    curr = _snap(metadata={"forum_posts": list(reversed(posts))})
    r = detect_change(previous=prev, current=curr, page_type="FORUM_THREAD")
    assert not r.changed  # same set of ids, only order differs -- must not register as edits


# -- classified listing ---------------------------------------------------

def test_listing_price_change():
    prev = _snap(metadata={"price": "2100.00", "currency": "USD"})
    curr = _snap(metadata={"price": "1900.00", "currency": "USD"})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert r.change_type == ChangeType.PRICE_CHANGED
    assert r.severity == Severity.HIGH
    assert r.diff["price"]["delta"] == -200.0
    assert r.diff["price"]["percentage"] == -9.52


def test_listing_status_change():
    prev = _snap(metadata={"status": "ACTIVE"})
    curr = _snap(metadata={"status": "SOLD"})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert r.change_type == ChangeType.STATUS_CHANGED
    assert r.severity == Severity.HIGH


def test_listing_description_change():
    prev = _snap(body="Original description text for this item.", simhash=simhash64("Original description text for this item."))
    new_body = "Completely rewritten description with different details about this item entirely."
    curr = _snap(body=new_body, simhash=simhash64(new_body))
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert r.change_type == ChangeType.DESCRIPTION_CHANGED


def test_listing_image_added():
    prev = _snap(images=["https://x/1.jpg"])
    curr = _snap(images=["https://x/1.jpg", "https://x/2.jpg"])
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert "images" in r.changed_fields
    assert r.diff["images"]["added"] == ["https://x/2.jpg"]


def test_listing_location_change():
    prev = _snap(metadata={"location": "Scotts Valley"})
    curr = _snap(metadata={"location": "San Jose"})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert "location" in r.changed_fields


def test_listing_attribute_change():
    prev = _snap(metadata={"attributes": {"condition": "used"}})
    curr = _snap(metadata={"attributes": {"condition": "like new"}})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert "attributes" in r.changed_fields


def test_listing_no_hardcoded_currency_assumption():
    prev = _snap(metadata={"price": "1000", "currency": "EUR"})
    curr = _snap(metadata={"price": "1200", "currency": "EUR"})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert r.diff["price"]["delta"] == 200.0  # correct regardless of currency, nothing USD-specific


# -- classified index (collection) -----------------------------------------

def test_index_listing_added():
    prev = _snap(metadata={"listings": [{"id": "a", "price": 100}]})
    curr = _snap(metadata={"listings": [{"id": "a", "price": 100}, {"id": "b", "price": 200}]})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_INDEX")
    assert r.change_type == ChangeType.LISTING_ADDED
    assert r.diff["listings_added"] == 1


def test_index_listing_removed():
    prev = _snap(metadata={"listings": [{"id": "a", "price": 100}, {"id": "b", "price": 200}]})
    curr = _snap(metadata={"listings": [{"id": "a", "price": 100}]})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_INDEX")
    assert r.change_type == ChangeType.LISTING_REMOVED


def test_index_ordering_change_alone_is_not_meaningful():
    listings = [{"id": "a", "price": 100}, {"id": "b", "price": 200}]
    prev = _snap(metadata={"listings": listings})
    curr = _snap(metadata={"listings": list(reversed(listings))})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_INDEX")
    assert not r.changed


# -- generic page -----------------------------------------------------------

def test_generic_same_page_no_change():
    prev = _snap()
    curr = _snap()
    r = detect_change(previous=prev, current=curr, page_type="UNKNOWN")
    assert not r.changed


def test_generic_meaningful_body_change():
    prev = _snap(simhash=simhash64(_BASE_BODY))
    rewritten = "An entirely unrelated block of text about something completely different from before."
    curr = _snap(body=rewritten, simhash=simhash64(rewritten))
    r = detect_change(previous=prev, current=curr, page_type="UNKNOWN")
    assert r.changed
    assert r.change_type == ChangeType.CONTENT_CHANGED
    assert r.severity in (Severity.MEDIUM, Severity.HIGH)


# -- false positives (spec section 45) --------------------------------------

def test_whitespace_only_is_not_a_change():
    prev = _snap(body="Hello   world  with content")
    curr = _snap(body="Hello world with content")
    r = detect_change(previous=prev, current=curr, page_type="NEWS_ARTICLE")
    assert not r.changed


def test_identical_metadata_different_key_order_is_not_a_change():
    prev = _snap(metadata={"a": 1, "b": 2})
    curr = _snap(metadata={"b": 2, "a": 1})
    r = detect_change(previous=prev, current=curr, page_type="CLASSIFIED_LISTING")
    assert not r.changed


def test_reordered_tags_are_not_a_change():
    prev = _snap(metadata={"tags": ["a", "b", "c"]})
    curr = _snap(metadata={"tags": ["c", "a", "b"]})
    r = detect_change(previous=prev, current=curr, page_type="NEWS_ARTICLE")
    assert not r.changed
