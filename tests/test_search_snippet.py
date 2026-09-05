from app.services.search.search_repository import _snippet


def test_snippet_without_query_returns_content_prefix():
    content = "x" * 500
    snippet = _snippet(content, None)
    assert len(snippet) == 240
    assert snippet == content[:240]


def test_snippet_empty_content_is_empty_string():
    assert _snippet("", "anything") == ""


def test_snippet_centers_on_first_query_term():
    content = "intro text here. " * 5 + "the important keyword appears right here in context. " + "more text. " * 20
    snippet = _snippet(content, "keyword search terms")
    assert "keyword" in snippet


def test_snippet_falls_back_to_prefix_when_term_not_found():
    content = "nothing relevant in this document at all, just filler text. " * 5
    snippet = _snippet(content, "unrelated_term_xyz")
    assert snippet == content[:240]


def test_snippet_adds_ellipsis_when_truncated_on_both_sides():
    content = "padding " * 100 + "findme " + "padding " * 100
    snippet = _snippet(content, "findme")
    assert snippet.startswith("...")
    assert snippet.endswith("...")
