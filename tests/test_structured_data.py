from app.services.extraction.structured_data import extract_structured_data

_ARTICLE_HTML = '''
<html><head><title>Test</title>
<meta property="og:title" content="A Great Article"/>
<meta property="og:type" content="article"/>
<meta property="og:url" content="https://news.example.com/article-1"/>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"NewsArticle","headline":"Breaking News Today",
"datePublished":"2026-01-15T10:00:00Z","dateModified":"2026-01-16T08:00:00Z",
"author":{"@type":"Person","name":"Jane Doe"},"publisher":{"@type":"Organization","name":"Example News"},
"articleSection":"Politics","keywords":["election","politics"],"image":["https://news.example.com/img.jpg"]}
</script>
</head><body><article><h1>Breaking News Today</h1><p>Body text here.</p></article></body></html>
'''


def test_extracts_all_fields_from_full_json_ld_and_og():
    result = extract_structured_data(_ARTICLE_HTML, "https://news.example.com/article-1")
    assert result.schema_type == "NewsArticle"
    assert result.headline == "Breaking News Today"
    assert result.author == "Jane Doe"
    assert result.published_at is not None and result.published_at.year == 2026
    assert result.updated_at is not None
    assert result.publisher == "Example News"
    assert result.section == "Politics"
    assert result.tags == ["election", "politics"]
    assert result.images == ["https://news.example.com/img.jpg"]
    assert result.og_type == "article"
    assert result.canonical_url == "https://news.example.com/article-1"


def test_no_structured_data_returns_empty_result_without_crashing():
    result = extract_structured_data("<html><body><p>hi</p></body></html>", "https://x.com/")
    assert result.schema_type is None
    assert result.headline is None
    assert result.tags == []


def test_malformed_json_ld_does_not_crash():
    html = '<script type="application/ld+json">{not valid json</script>'
    result = extract_structured_data(html, "https://x.com/")
    assert result.schema_type is None
