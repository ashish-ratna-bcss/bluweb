"""Phase 9 perf measurements (spec section 43): small/medium/large page and
index scale, for the new discovery/extraction modules. `pytest-benchmark`
reports real wall-clock numbers into the final report rather than guessed
ones -- run with `pytest tests/test_performance.py --benchmark-only`.
"""

from app.services.discovery.pagination import detect_pagination
from app.services.extraction.article_extractor import ArticleDocument
from app.services.extraction.completeness_scorer import score_completeness
from app.services.extraction.index_extractor import extract_index
from app.services.extraction.quality_scorer import score_extraction
from app.services.extraction.table_extractor import extract_tables

BASE_URL = "https://example.com/search"


def _make_index_html(n: int) -> str:
    items = "".join(
        f'<li class="result-row"><a class="result-title" href="/item/{i}" title="Item {i}">Item {i}</a>'
        f'<span class="result-price">${i}.00</span></li>'
        for i in range(n)
    )
    return f"<html><body><ul>{items}</ul></body></html>"


def _make_body(n_paragraphs: int) -> str:
    return "\n\n".join("This is a real sentence of article content with genuine words in it." for _ in range(n_paragraphs))


def test_index_extraction_small(benchmark):
    html = _make_index_html(5)  # MIN_REPEATED_ITEMS floor
    result = benchmark(extract_index, html, BASE_URL)
    assert len(result.listings) == 5


def test_index_extraction_medium(benchmark):
    html = _make_index_html(50)
    result = benchmark(extract_index, html, BASE_URL)
    assert len(result.listings) == 50


def test_index_extraction_large_real_fixture(benchmark):
    html = open("tests/fixtures/classified_index_craigslist.html").read()
    result = benchmark(extract_index, html, "https://sfbay.craigslist.org/search/sss")
    assert len(result.listings) > 100


def test_quality_scoring_small_body(benchmark):
    doc = ArticleDocument(
        extractor="test", headline="Short headline", body=_make_body(2), author=None, publisher=None,
        published_at=None, updated_at=None, section=None, canonical_url=None, language=None,
        tags=[], images=[], confidence=0.5, fields_detected=["body"], raw_metadata={},
    )
    benchmark(score_extraction, doc)


def test_quality_scoring_large_body(benchmark):
    doc = ArticleDocument(
        extractor="test", headline="Long article headline", body=_make_body(500), author="A", publisher="B",
        published_at=None, updated_at=None, section="News", canonical_url="https://x/1", language="en",
        tags=["t"], images=["https://x/1.jpg"], confidence=0.8, fields_detected=["body"], raw_metadata={},
    )
    benchmark(score_extraction, doc)


def test_pagination_detection_large_page(benchmark):
    html = _make_index_html(200) + '<a rel="next" href="/search?page=2">Next</a>'
    info = benchmark(detect_pagination, html, BASE_URL)
    assert info.next_url is not None


def test_completeness_scoring(benchmark):
    result = benchmark(
        score_completeness, extracted_body_chars=1800, page_meaningful_text_chars=2000,
        listing_count=0, internal_link_count=40, pagination_detected=True,
    )
    assert result.overall > 0


def test_table_extraction_small(benchmark):
    html = "<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody>" + "".join(
        f"<tr><td>{i}</td><td>{i * 2}</td></tr>" for i in range(5)
    ) + "</tbody></table>"
    tables = benchmark(extract_tables, html)
    assert len(tables) == 1


def test_table_extraction_real_wikipedia_page(benchmark):
    html = open("tests/fixtures/generic_wikipedia_table_page.html").read()
    tables = benchmark(extract_tables, html)
    assert len(tables) > 0
