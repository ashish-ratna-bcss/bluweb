from app.services.normalization.url_normalizer import (
    canonicalize_for_frontier,
    extract_domain,
    normalize_url,
    registrable_domain,
)


def test_registrable_domain_strips_subdomain():
    assert registrable_domain("https://sfbay.craigslist.org/search/sss") == "craigslist.org"
    assert registrable_domain("https://www.craigslist.org/view/d/x") == "craigslist.org"


def test_registrable_domain_handles_multi_label_public_suffix():
    # "co.uk" is a public suffix, not a domain -- a naive last-two-labels
    # heuristic would wrongly return "co.uk" here instead of "example.co.uk".
    assert registrable_domain("https://www.example.co.uk/page") == "example.co.uk"


def test_registrable_domain_matches_extract_domain_for_bare_domain():
    assert registrable_domain("https://simonwillison.net/post") == "simonwillison.net"
    assert registrable_domain("https://simonwillison.net/post") == extract_domain("https://simonwillison.net/post")


def test_extract_domain_keeps_exact_hostname():
    assert extract_domain("https://sfbay.craigslist.org/search/sss") == "sfbay.craigslist.org"


def test_normalize_url_strips_utm_and_gclid():
    assert (
        normalize_url("https://Example.COM/a/b?utm_source=x&id=1&gclid=abc")
        == "https://example.com/a/b?id=1"
    )


def test_normalize_url_keeps_content_id_param():
    assert normalize_url("https://example.com/item?id=1") == "https://example.com/item?id=1"
    assert normalize_url("https://example.com/search?q=shoes&page=2") == "https://example.com/search?q=shoes&page=2"


def test_normalize_url_strips_trailing_slash():
    assert normalize_url("https://example.com/blog/") == "https://example.com/blog"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_canonicalize_for_frontier_aliases_normalize():
    url = "https://Example.COM/path/?utm_campaign=spring&id=9"
    assert canonicalize_for_frontier(url) == normalize_url(url)
    assert canonicalize_for_frontier(url) == "https://example.com/path?id=9"
