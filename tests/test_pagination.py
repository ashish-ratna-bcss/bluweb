from app.services.discovery.pagination import detect_pagination

BASE = "https://forum.example.com/thread/1"


def test_rel_next_link_wins_even_when_text_pagination_also_present():
    html = f"""
    <html><head><link rel="next" href="/thread/1?page=2"></head>
    <body><a href="/thread/1?page=2">Older Posts</a></body></html>
    """
    info = detect_pagination(html, BASE)
    assert info.method == "rel_next"
    assert info.next_url == "https://forum.example.com/thread/1?page=2"
    assert info.url_pattern == "https://forum.example.com/thread/1?page={page}"


def test_link_text_heuristic_matches_older_posts_not_just_bare_older():
    html = '<html><body><a href="/thread/1?page=2">Older Posts</a></body></html>'
    info = detect_pagination(html, BASE)
    assert info.method == "link_text"
    assert info.next_url == "https://forum.example.com/thread/1?page=2"


def test_read_more_does_not_false_positive_as_pagination():
    html = '<html><body><a href="/articles/5">Read more</a></body></html>'
    info = detect_pagination(html, BASE)
    assert info.next_url is None


def test_numbered_pagination_picks_smallest_page_greater_than_one():
    html = """
    <html><body>
    <a href="/thread/1?page=1">1</a>
    <a href="/thread/1?page=2">2</a>
    <a href="/thread/1?page=3">3</a>
    </body></html>
    """
    info = detect_pagination(html, BASE)
    assert info.method == "numbered"
    assert info.next_url == "https://forum.example.com/thread/1?page=2"


def test_no_pagination_signals_returns_empty_info():
    html = "<html><body><p>No pager here.</p></body></html>"
    info = detect_pagination(html, BASE)
    assert info.next_url is None
    assert info.method is None
