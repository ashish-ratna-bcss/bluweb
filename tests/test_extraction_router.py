from app.services.classification.page_classifier import PageType
from app.services.events import Event, default_bus
from app.services.extraction.extraction_router import _scrapling_fallback, extract_for_page

_FORUM_URL = "https://forum.example.com/t/123-great-topic"
_FORUM_HTML = '''
<html><body>
<div class="comment"><span class="hnuser">alice</span><span class="age" title="2026-01-01T00:00:00Z">1h</span>
<div class="commtext">Opening post about the topic, with enough text to count as real content here.</div></div>
<div class="comment"><span class="hnuser">bob</span><span class="age" title="2026-01-01T01:00:00Z">2h</span>
<div class="commtext">A reply that adds more discussion to the thread, also long enough.</div></div>
</body></html>
'''

_LISTING_URL = "https://classifieds.example.com/listings/vintage-bike-123"
_LISTING_HTML = '''
<html><body>
<h1 id="titletextonly">Vintage road bike, great condition</h1>
<span class="price">$300</span>
<div id="postingbody">Steel frame road bike from the 80s, recently serviced, new tires and cabling all around.</div>
</body></html>
'''


async def test_forum_url_routes_to_forum_extractor_and_publishes_event():
    received = []
    default_bus.subscribe("FORUM_EXTRACTED", lambda e: received.append(e))

    result = await extract_for_page(_FORUM_URL, _FORUM_HTML, html_analysis=None)

    assert result.classification.page_type == PageType.FORUM_THREAD
    assert result.document is not None
    assert result.document.extractor == "forum"
    assert len(received) == 1


async def test_classified_url_routes_to_listing_extractor_and_publishes_event():
    received = []
    default_bus.subscribe("LISTING_EXTRACTED", lambda e: received.append(e))

    result = await extract_for_page(_LISTING_URL, _LISTING_HTML, html_analysis=None)

    assert result.classification.page_type == PageType.CLASSIFIED_LISTING
    assert result.document is not None
    assert result.document.extractor == "listing"
    assert len(received) == 1


async def test_scrapling_fallback_recovers_content_and_publishes_attempt():
    received = []
    default_bus.subscribe("SCRAPLING_ATTEMPTED", lambda e: received.append(e))

    html = "<html><body><main>" + ("Scrapling last-resort recovered text. " * 5) + "</main></body></html>"
    document = await _scrapling_fallback(
        "https://example.com/mystery-page", html, PageType.UNKNOWN, fallback_document=None
    )

    assert document is not None
    assert document.extractor == "scrapling"
    assert "recovered text" in document.body
    assert received and received[-1].payload["success"] is True


async def test_scrapling_fallback_returns_original_when_nothing_found():
    html = "<html><body><p>x</p></body></html>"
    document = await _scrapling_fallback(
        "https://example.com/empty-page", html, PageType.UNKNOWN, fallback_document=None
    )
    assert document is None
