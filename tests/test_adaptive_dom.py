from app.services.extraction.adaptive_dom import extract_with_scrapling

_TEXT = "Selector healing content block, long enough to pass the minimum character threshold for a real match in this test."
_HTML_ORIGINAL = f"<html><body><div class='article-body'>{_TEXT}</div></body></html>"
_HTML_CHANGED_MARKUP = f"<html><body><section class='content-block'>{_TEXT}</section></body></html>"


def test_finds_content_via_known_selector():
    result = extract_with_scrapling(_HTML_ORIGINAL, identifier="test-domain:pattern-a")
    assert result is not None
    assert "Selector healing content block" in result.text


def test_relocates_after_class_and_tag_change_same_identifier():
    # First call establishes + saves the selector under this identifier.
    extract_with_scrapling(_HTML_ORIGINAL, identifier="test-domain:pattern-b")
    # Markup changed (div.article-body -> section.content-block) but the
    # identifier is the same -- Scrapling's own adaptive storage should
    # relocate the content without the .article-body selector matching.
    result = extract_with_scrapling(_HTML_CHANGED_MARKUP, identifier="test-domain:pattern-b")
    assert result is not None
    assert "Selector healing content block" in result.text


def test_no_matching_content_returns_none():
    result = extract_with_scrapling("<html><body><p>short</p></body></html>", identifier="test-domain:pattern-c")
    assert result is None
