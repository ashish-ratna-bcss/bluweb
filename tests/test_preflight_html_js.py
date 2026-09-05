from app.services.preflight.html import analyze_html
from app.services.preflight.javascript import assess_javascript_dependency

STATIC_ARTICLE_HTML = """
<html><head><title>A real article</title>
<meta property="og:title" content="A real article"/>
<script type="application/ld+json">{"@type": "NewsArticle"}</script>
<link rel="canonical" href="https://news.example.com/article-1"/>
</head>
<body>
<h1>Big Headline</h1>
<p>""" + ("This is real visible article text. " * 40) + """</p>
<a href="/article-2">Next article</a>
<a href="https://other-domain.example/ext">External</a>
<img src="/photo.jpg"/>
</body></html>
"""

SPA_SHELL_HTML = """
<html><head><title>App</title>
<script src="/static/bundle.js"></script>
""" + ("<script>" + ("x=1;" * 2000) + "</script>") * 5 + """
</head>
<body><div id="root"></div></body></html>
"""


def test_analyze_html_extracts_signals_from_static_article():
    result = analyze_html(STATIC_ARTICLE_HTML, "https://news.example.com/article-1")
    assert result.title == "A real article"
    assert result.has_json_ld is True
    assert result.has_open_graph is True
    assert result.canonical_url == "https://news.example.com/article-1"
    assert result.meaningful_text_length > 200
    assert "https://news.example.com/article-2" in result.internal_links
    assert not any("other-domain.example" in link for link in result.internal_links)
    assert result.image_count == 1


def test_js_assessment_says_static_page_does_not_need_browser():
    analysis = analyze_html(STATIC_ARTICLE_HTML, "https://news.example.com/article-1")
    assessment = assess_javascript_dependency(STATIC_ARTICLE_HTML, analysis)
    assert assessment.likely_requires_browser is False


def test_js_assessment_flags_empty_spa_shell_as_needing_browser():
    analysis = analyze_html(SPA_SHELL_HTML, "https://app.example.com/")
    assessment = assess_javascript_dependency(SPA_SHELL_HTML, analysis)
    assert assessment.likely_requires_browser is True
    assert assessment.empty_content_containers_detected is True
