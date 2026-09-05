from app.services.extraction.article_extractor import extract_article
from app.services.classification.page_classifier import PageType
from app.services.extraction.quality_scorer import score_extraction


def test_real_bbc_article_scores_high():
    html = open("tests/fixtures/news_bbc_original.html").read()
    doc = extract_article("https://www.bbc.com/news/articles/c9dwjv96qyqo", html, PageType.NEWS_ARTICLE)
    score = score_extraction(doc)
    assert score.overall > 0.6
    assert score.title == 1.0
    assert score.contamination_ratio == 0.0


def test_contaminated_body_scores_lower_than_clean_body_of_similar_length():
    from app.services.extraction.article_extractor import ArticleDocument

    clean = ArticleDocument(
        extractor="test", headline="A real headline about something happening",
        body="This is a real paragraph of article content that discusses an actual event in detail. " * 8,
        author="Jane Doe", publisher="Example News", published_at=None, updated_at=None, section="News",
        canonical_url="https://x/1", language="en", tags=["news"], images=["https://x/1.jpg"],
        confidence=0.8, fields_detected=["body", "headline"], raw_metadata={},
    )
    contaminated = ArticleDocument(
        extractor="test", headline="A real headline about something happening",
        body=(
            "We use cookies to improve your experience. Subscribe to our newsletter for updates. "
            "All rights reserved. Terms of Service apply. Privacy Policy. Advertisement. "
            "Sponsored content below. Please enable javascript to continue. " * 4
        ),
        author="Jane Doe", publisher="Example News", published_at=None, updated_at=None, section="News",
        canonical_url="https://x/1", language="en", tags=["news"], images=["https://x/1.jpg"],
        confidence=0.8, fields_detected=["body", "headline"], raw_metadata={},
    )

    clean_score = score_extraction(clean)
    contaminated_score = score_extraction(contaminated)
    assert contaminated_score.contamination_ratio > clean_score.contamination_ratio
    assert contaminated_score.overall < clean_score.overall


def test_no_body_scores_zero():
    from app.services.extraction.article_extractor import ArticleDocument

    empty = ArticleDocument(
        extractor="test", headline=None, body="", author=None, publisher=None, published_at=None,
        updated_at=None, section=None, canonical_url=None, language=None, tags=[], images=[],
        confidence=0.0, fields_detected=[], raw_metadata={},
    )
    score = score_extraction(empty)
    assert score.overall == 0.0


def test_length_alone_does_not_guarantee_a_high_score():
    """spec section 16: do NOT equate longer text with better extraction --
    a huge wall of undifferentiated text (no paragraph breaks) must not
    automatically outscore a shorter, well-structured one."""
    from app.services.extraction.article_extractor import ArticleDocument

    wall_of_text = ArticleDocument(
        extractor="test", headline="Title", body="word " * 3000,  # 15000 chars, zero paragraph structure
        author=None, publisher=None, published_at=None, updated_at=None, section=None,
        canonical_url=None, language=None, tags=[], images=[], confidence=0.5,
        fields_detected=["body"], raw_metadata={},
    )
    structured = ArticleDocument(
        extractor="test", headline="Title",
        body="\n\n".join(["This is a well formed paragraph with real sentence structure and content."] * 6),
        author="A", publisher="B", published_at=None, updated_at=None, section="C",
        canonical_url="https://x/1", language="en", tags=["t"], images=["https://x/1.jpg"],
        confidence=0.8, fields_detected=["body"], raw_metadata={},
    )
    wall_score = score_extraction(wall_of_text)
    structured_score = score_extraction(structured)
    assert structured_score.overall > wall_score.overall
