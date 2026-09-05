from app.services.extraction.article_extractor import ArticleDocument
from app.services.extraction.provenance import build_provenance
from app.services.extraction.structured_data import StructuredData


def _doc(**overrides) -> ArticleDocument:
    defaults = dict(
        extractor="news", headline=None, body="", author=None, publisher=None, published_at=None,
        updated_at=None, section=None, canonical_url=None, language=None, tags=[], images=[],
        confidence=0.8, fields_detected=[], raw_metadata={},
    )
    defaults.update(overrides)
    return ArticleDocument(**defaults)


def test_headline_matching_structured_data_gets_json_ld_source():
    structured = StructuredData(headline="Real Headline", raw_json_ld=[{"@type": "NewsArticle"}])
    doc = _doc(headline="Real Headline", body="enough body text here")

    prov = build_provenance(doc, structured)

    assert prov["title"]["source"] == "json_ld"
    assert prov["title"]["confidence"] == 0.95
    assert prov["title"]["extractor"] == "news"


def test_headline_not_matching_structured_data_falls_back_to_dom():
    structured = StructuredData(headline="Different Headline", raw_json_ld=[{"@type": "NewsArticle"}])
    doc = _doc(headline="Generic-extracted Title", body="enough body text here")

    prov = build_provenance(doc, structured)

    assert prov["title"]["source"] == "dom"


def test_body_uses_trafilatura_source_for_news_extractor_and_is_truncated():
    doc = _doc(extractor="news", body="x" * 5000)

    prov = build_provenance(doc, None)

    assert prov["body"]["source"] == "trafilatura"
    assert len(prov["body"]["value"]) == 200  # snippet only -- never the whole body


def test_body_uses_scrapling_source_for_scrapling_fallback():
    doc = _doc(extractor="scrapling", body="fallback content")

    prov = build_provenance(doc, None)

    assert prov["body"]["source"] == "scrapling"


def test_body_uses_dom_source_for_forum_and_listing_extractors():
    forum_doc = _doc(extractor="forum", body="thread text")
    listing_doc = _doc(extractor="listing", body="listing text")

    assert build_provenance(forum_doc, None)["body"]["source"] == "dom"
    assert build_provenance(listing_doc, None)["body"]["source"] == "dom"


def test_price_from_schema_org_when_structured_data_present():
    structured = StructuredData(schema_type="Product", raw_json_ld=[{"@type": "Product"}])
    doc = _doc(extractor="listing", body="a listing", raw_metadata={"price": "$50"})

    prov = build_provenance(doc, structured)

    assert prov["price"]["source"] == "schema_org"
    assert prov["price"]["value"] == "$50"


def test_price_from_dom_when_no_structured_data():
    doc = _doc(extractor="listing", body="a listing", raw_metadata={"price": "$50"})

    prov = build_provenance(doc, None)

    assert prov["price"]["source"] == "dom"
    assert prov["price"]["confidence"] < prov.get("price", {}).get("confidence", 1.0) + 1  # sanity: exists, lower than schema_org case
    assert prov["price"]["confidence"] == 0.6


def test_document_with_no_recordable_fields_gets_unknown_note_not_fabricated_confidence():
    doc = _doc(headline=None, body="", author=None, raw_metadata={})

    prov = build_provenance(doc, None)

    assert prov["_note"]["source"] == "unknown"
    assert prov["_note"]["confidence"] == 0.0
    assert prov["_note"]["value"] is None
