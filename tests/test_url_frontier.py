from app.services.normalization.url_frontier import is_sink_url, sink_reason


def test_sink_login_and_cart():
    assert is_sink_url("https://example.com/login") is True
    assert is_sink_url("https://example.com/cart") is True
    assert sink_reason("https://example.com/login") == "sink_segment:login"
    assert sink_reason("https://example.com/cart") == "sink_segment:cart"


def test_article_url_is_not_sink():
    assert is_sink_url("https://example.com/blog/my-summer-trip") is False
    assert sink_reason("https://example.com/blog/my-summer-trip") is None


def test_blog_login_is_sink():
    assert is_sink_url("https://example.com/blog/login") is True
    assert sink_reason("https://example.com/blog/login") == "sink_segment:login"


def test_empty_and_malformed_not_sink():
    assert is_sink_url("") is False
    assert is_sink_url("   ") is False
    assert sink_reason("") is None
