from app.services.normalization.url_normalizer import extract_domain, registrable_domain


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
