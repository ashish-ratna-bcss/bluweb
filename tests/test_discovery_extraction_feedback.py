"""Discovery -> extraction feedback loop (Phase 8.1 sections 9/20): an
index/listing page's extracted items must become candidate child URLs, not
just stored metadata. `_extract_child_urls_from_listings` is the pure
decision function `_process_page` enqueues from -- tested directly since
`_process_page` itself needs a live Postgres session.
"""

from app.services.crawling.crawl_engine import (
    _MAX_CHILD_URLS_PER_INDEX_PAGE,
    _extract_child_urls_from_listings,
)


def _listing(url):
    return {"id": url, "title": "t", "url": url, "price": None, "location": None, "image": None}


def test_real_index_page_yields_child_urls_to_crawl():
    raw_metadata = {"listings": [_listing("https://example.com/item/1"), _listing("https://example.com/item/2")]}

    urls = _extract_child_urls_from_listings(raw_metadata, same_domain_only=True, seed_domain="example.com")

    assert urls == ["https://example.com/item/1", "https://example.com/item/2"]


def test_no_listings_yields_no_child_urls():
    assert _extract_child_urls_from_listings({}, same_domain_only=True, seed_domain="example.com") == []
    assert _extract_child_urls_from_listings(None, same_domain_only=True, seed_domain="example.com") == []


def test_items_with_no_url_are_skipped_not_fabricated():
    raw_metadata = {"listings": [
        {"id": "x", "title": "t", "url": None, "price": None, "location": None, "image": None},
        _listing("https://example.com/item/2"),
    ]}

    urls = _extract_child_urls_from_listings(raw_metadata, same_domain_only=True, seed_domain="example.com")

    assert urls == ["https://example.com/item/2"]


def test_same_domain_only_filters_cross_domain_listing_urls():
    raw_metadata = {"listings": [_listing("https://other-domain.example/item/1"), _listing("https://example.com/item/2")]}

    urls = _extract_child_urls_from_listings(raw_metadata, same_domain_only=True, seed_domain="example.com")

    assert urls == ["https://example.com/item/2"]


def test_same_registrable_domain_allows_cross_subdomain_listing_urls():
    """Real bug fix: `seed_domain` here is a registrable domain (eTLD+1),
    not an exact hostname -- a real Craigslist search-results page's items
    all link to a different subdomain (www.craigslist.org) than the search
    page itself (sfbay.craigslist.org). An exact-hostname comparison
    silently dropped every single listing; this must not regress."""
    raw_metadata = {"listings": [_listing("https://www.craigslist.org/view/d/item-1")]}

    urls = _extract_child_urls_from_listings(raw_metadata, same_domain_only=True, seed_domain="craigslist.org")

    assert urls == ["https://www.craigslist.org/view/d/item-1"]


def test_cross_domain_allowed_when_same_domain_only_is_false():
    raw_metadata = {"listings": [_listing("https://other-domain.example/item/1")]}

    urls = _extract_child_urls_from_listings(raw_metadata, same_domain_only=False, seed_domain="example.com")

    assert urls == ["https://other-domain.example/item/1"]


def test_child_url_count_is_capped_so_one_index_page_cannot_flood_the_frontier():
    raw_metadata = {"listings": [_listing(f"https://example.com/item/{i}") for i in range(500)]}

    urls = _extract_child_urls_from_listings(raw_metadata, same_domain_only=True, seed_domain="example.com")

    assert len(urls) == _MAX_CHILD_URLS_PER_INDEX_PAGE
