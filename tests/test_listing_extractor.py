from pathlib import Path

from app.services.extraction.listing_extractor import extract_listing
from app.services.extraction.structured_data import extract_structured_data

_CL_FIXTURE = Path(__file__).parent / "fixtures" / "classified_listing_craigslist.html"
_CL_URL = "https://www.craigslist.org/view/d/scotts-valley-rocky-mountain-instinct/84MFCJ4k2QEo6UZ2NDrYz6"

_DOM_ONLY_HTML = '''
<html><body>
<h1 id="titletextonly">Vintage bookshelf, solid oak</h1>
<span class="price">$150</span>
<span class="location">Downtown</span>
<div id="postingbody">Solid oak bookshelf, five shelves, minor scratches on the left side but sturdy and in great shape overall.</div>
</body></html>
'''


def test_real_craigslist_listing_extracts_structured_product_offer():
    html = _CL_FIXTURE.read_text()
    structured = extract_structured_data(html, _CL_URL)

    doc = extract_listing(_CL_URL, html, structured=structured)

    assert doc is not None
    assert doc.extractor == "listing"
    assert "Rocky Mountain" in doc.headline
    assert doc.raw_metadata["price"] == "2100.00"
    assert doc.raw_metadata["currency"] == "USD"
    assert doc.raw_metadata["location"] == "Scotts Valley"
    assert len(doc.images) > 0
    assert "Electric Bike" in doc.body


def test_dom_heuristics_used_when_no_structured_data():
    structured = extract_structured_data(_DOM_ONLY_HTML, "https://classifieds.example.com/ad/1")
    doc = extract_listing("https://classifieds.example.com/ad/1", _DOM_ONLY_HTML, structured=structured)

    assert doc is not None
    assert doc.headline == "Vintage bookshelf, solid oak"
    assert doc.raw_metadata["price"] == "150"
    assert doc.raw_metadata["location"] == "Downtown"
    assert "oak bookshelf" in doc.body


def test_no_description_returns_none():
    html = "<html><body><h1>Title only, nothing else</h1></body></html>"
    structured = extract_structured_data(html, "https://example.com/empty")
    doc = extract_listing("https://example.com/empty", html, structured=structured)
    assert doc is None
