from pathlib import Path

from app.services.extraction.forum_extractor import extract_forum
from app.services.extraction.structured_data import extract_structured_data

_HN_FIXTURE = Path(__file__).parent / "fixtures" / "forum_thread_hn.html"
_HN_URL = "https://news.ycombinator.com/item?id=49554643"

_JSON_LD_THREAD_HTML = '''
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"DiscussionForumPosting","headline":"Best laptop for travel?",
"text":"Looking for recommendations, thin and light preferred.",
"author":{"@type":"Person","name":"traveler42"},"datePublished":"2026-02-01T10:00:00Z",
"comment":[
  {"@type":"Comment","text":"The XPS 13 is great for that.","author":{"@type":"Person","name":"replier1"},"datePublished":"2026-02-01T11:00:00Z"},
  {"@type":"Comment","text":"Seconding the XPS 13, had mine for years.","author":{"@type":"Person","name":"replier2"},"datePublished":"2026-02-01T12:00:00Z"}
]}
</script>
</head><body></body></html>
'''


def test_real_hn_thread_extracts_conversation_via_dom_heuristics():
    html = _HN_FIXTURE.read_text()
    structured = extract_structured_data(html, _HN_URL)

    doc = extract_forum(_HN_URL, html, structured=structured)

    assert doc is not None
    assert doc.extractor == "forum"
    posts = doc.raw_metadata["forum_posts"]
    assert len(posts) > 100  # real thread has 1700+ comments, capped at MAX_POSTS_IN_METADATA
    assert doc.raw_metadata["reply_count"] > 100
    assert all(p["body"] for p in posts)
    # first extracted post carries a real author/timestamp from the fixture
    assert posts[0]["author"] is not None
    assert posts[0]["posted_at"] is not None


def test_json_ld_discussion_posting_extracts_op_and_replies():
    structured = extract_structured_data(_JSON_LD_THREAD_HTML, "https://forum.example.com/t/1")
    doc = extract_forum("https://forum.example.com/t/1", _JSON_LD_THREAD_HTML, structured=structured)

    assert doc is not None
    posts = doc.raw_metadata["forum_posts"]
    assert len(posts) == 3
    assert posts[0]["author"] == "traveler42"
    assert posts[0]["is_op"] is True
    assert posts[1]["author"] == "replier1"
    assert posts[1]["is_op"] is False
    assert doc.raw_metadata["reply_count"] == 2


def test_no_posts_found_returns_none():
    html = "<html><body><p>Just a plain page, no comments here.</p></body></html>"
    structured = extract_structured_data(html, "https://example.com/plain")
    doc = extract_forum("https://example.com/plain", html, structured=structured)
    assert doc is None
