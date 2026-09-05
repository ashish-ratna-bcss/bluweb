from app.services.extraction.completeness_scorer import score_completeness


def test_clean_short_excerpt_of_a_much_longer_page_scores_low_completeness():
    result = score_completeness(extracted_body_chars=300, page_meaningful_text_chars=6000)
    assert result.text_coverage < 0.1
    assert result.overall < 0.1


def test_body_covering_most_of_the_pages_visible_text_scores_high():
    result = score_completeness(extracted_body_chars=1800, page_meaningful_text_chars=2000)
    assert result.overall > 0.85


def test_coverage_caps_at_one_even_when_body_exceeds_raw_visible_text():
    # A structured extractor can legitimately produce more normalized text
    # than the page's raw visible-text count (e.g. reformatted whitespace).
    result = score_completeness(extracted_body_chars=5000, page_meaningful_text_chars=2000)
    assert result.overall == 1.0


def test_index_page_judged_by_unclaimed_links_not_text_coverage():
    # 100 internal links, only 40 became listing items -- 60% left unclaimed.
    result = score_completeness(
        extracted_body_chars=50, page_meaningful_text_chars=10000,
        listing_count=40, internal_link_count=100,
    )
    assert result.unclaimed_link_ratio == 0.6
    assert result.overall == 0.4


def test_index_page_that_captured_every_link_scores_fully_complete():
    result = score_completeness(
        extracted_body_chars=50, page_meaningful_text_chars=10000,
        listing_count=50, internal_link_count=50,
    )
    assert result.unclaimed_link_ratio == 0.0
    assert result.overall == 1.0


def test_detected_but_uncrawled_pagination_reduces_completeness():
    with_pagination = score_completeness(extracted_body_chars=1800, page_meaningful_text_chars=2000, pagination_detected=True)
    without_pagination = score_completeness(extracted_body_chars=1800, page_meaningful_text_chars=2000, pagination_detected=False)
    assert with_pagination.overall < without_pagination.overall
    assert with_pagination.pagination_uncrawled is True


def test_no_body_and_no_page_text_is_zero_not_fabricated():
    result = score_completeness(extracted_body_chars=0, page_meaningful_text_chars=0)
    assert result.overall == 0.0
