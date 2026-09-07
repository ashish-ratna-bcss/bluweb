from app.services.classification.page_classifier import PageType, classify
from app.services.extraction.structured_data import StructuredData, extract_structured_data

_EMPTY = StructuredData()


def test_news_article_from_json_ld():
    html = '''<script type="application/ld+json">
    {"@type":"NewsArticle","headline":"H","datePublished":"2026-01-01","author":{"name":"A"}}
    </script>'''
    sd = extract_structured_data(html, "https://news.example.com/a")
    result = classify(url="https://news.example.com/a", structured=sd, html_analysis=None)
    assert result.page_type == PageType.NEWS_ARTICLE
    assert result.confidence > 0.9


def test_blog_posting_from_json_ld():
    html = '<script type="application/ld+json">{"@type":"BlogPosting","headline":"H"}</script>'
    sd = extract_structured_data(html, "https://blog.example.com/p")
    result = classify(url="https://blog.example.com/p", structured=sd, html_analysis=None)
    assert result.page_type == PageType.BLOG_POST


def test_forum_thread_url_pattern():
    result = classify(url="https://forum.example.com/t/some-topic/123", structured=_EMPTY, html_analysis=None)
    assert result.page_type == PageType.FORUM_THREAD


def test_forum_index_url_pattern():
    result = classify(url="https://forum.example.com/forum/", structured=_EMPTY, html_analysis=None)
    assert result.page_type == PageType.FORUM_INDEX


def test_classified_listing_url_pattern():
    result = classify(
        url="https://classifieds.example.com/listings/car-for-sale-1234", structured=_EMPTY, html_analysis=None
    )
    assert result.page_type == PageType.CLASSIFIED_LISTING


def test_search_results_url_pattern():
    result = classify(url="https://example.com/search?q=test", structured=_EMPTY, html_analysis=None)
    assert result.page_type == PageType.SEARCH_RESULTS


def test_staff_directory_url_pattern():
    result = classify(url="https://example.gov/about/staff/", structured=_EMPTY, html_analysis=None)
    assert result.page_type == PageType.DIRECTORY


def test_member_directory_url_pattern():
    result = classify(url="https://example.org/member-directory", structured=_EMPTY, html_analysis=None)
    assert result.page_type == PageType.DIRECTORY


def test_seed_homepage_flag_wins_over_url_heuristics():
    result = classify(url="https://example.com/", structured=_EMPTY, html_analysis=None, is_seed_homepage=True)
    assert result.page_type == PageType.HOME


def test_pdf_extension_is_document_regardless_of_other_signals():
    result = classify(url="https://example.com/report.pdf", structured=_EMPTY, html_analysis=None)
    assert result.page_type == PageType.DOCUMENT
    assert result.confidence > 0.9


def test_no_signals_is_unknown_not_a_guess():
    result = classify(url="https://example.com/random-page-xyz", structured=_EMPTY, html_analysis=None)
    assert result.page_type == PageType.UNKNOWN
    assert result.confidence == 0.0


# -- Phase 7 section 31: combined URL+DOM+structured+historical forum signal --

def _repeated_comments_html(n: int) -> str:
    posts = "".join(f'<div class="comment">user{i}: some comment text here</div>' for i in range(n))
    return f"<html><body>{posts}</body></html>"


def test_forum_like_dom_structure_wins_without_url_hint():
    # No "/thread/" or "/topic/" in the URL (e.g. Hacker-News-style ?id=) --
    # this is exactly the Phase 6 gap: URL regex alone would miss it.
    html = _repeated_comments_html(20)
    result = classify(url="https://news.example.com/item?id=123", structured=_EMPTY, html_analysis=None, html=html)
    assert result.page_type == PageType.FORUM_THREAD
    assert any("comment-like containers" in s for s in result.signals)


def test_few_comment_like_elements_is_not_enough_alone():
    html = _repeated_comments_html(2)  # below _MIN_FORUM_LIKE_CONTAINERS
    result = classify(url="https://example.com/item?id=1", structured=_EMPTY, html_analysis=None, html=html)
    assert result.page_type != PageType.FORUM_THREAD


def test_discussion_forum_posting_schema_is_a_forum_signal():
    html = '''<script type="application/ld+json">
    {"@type":"DiscussionForumPosting","headline":"Thread","text":"OP text"}
    </script>'''
    sd = extract_structured_data(html, "https://example.com/item?id=1")
    result = classify(url="https://example.com/item?id=1", structured=sd, html_analysis=None, html=html)
    assert result.page_type == PageType.FORUM_THREAD


def test_historical_pattern_hint_only_used_as_last_resort():
    # No URL/DOM/schema signal at all -- historical hint is the only thing
    # available, so it may be used, but at low confidence.
    result = classify(
        url="https://example.com/random-page-xyz", structured=_EMPTY, html_analysis=None,
        pattern_hint="FORUM_THREAD",
    )
    assert result.page_type == PageType.FORUM_THREAD
    assert result.confidence <= 0.5


def test_historical_pattern_hint_does_not_override_strong_current_evidence():
    html = '<script type="application/ld+json">{"@type":"NewsArticle","headline":"H"}</script>'
    sd = extract_structured_data(html, "https://news.example.com/a")
    result = classify(
        url="https://news.example.com/a", structured=sd, html_analysis=None,
        pattern_hint="FORUM_THREAD",  # should be ignored -- schema.org NewsArticle is much stronger evidence
    )
    assert result.page_type == PageType.NEWS_ARTICLE
