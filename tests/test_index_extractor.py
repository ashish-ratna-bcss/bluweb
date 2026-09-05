from pathlib import Path

from app.services.extraction.index_extractor import extract_index

_FIXTURE = Path(__file__).parent / "fixtures" / "classified_index_craigslist.html"
_URL = "https://sfbay.craigslist.org/search/sss?query=bicycle"


def test_real_craigslist_search_page_extracts_listings():
    html = _FIXTURE.read_text()
    result = extract_index(html, _URL)

    assert len(result.listings) >= 100
    assert result.container_selector_signature is not None

    first = result.listings[0]
    assert first.title
    assert first.url and first.url.startswith("https://www.craigslist.org/")
    assert first.price and "$" in first.price


def test_no_repeated_structure_returns_empty():
    html = "<html><body><h1>Just a headline</h1><p>Some text, no repeated items here.</p></body></html>"
    result = extract_index(html, "https://example.com/")
    assert result.listings == []
    assert result.container_selector_signature is None


def test_below_minimum_repetition_threshold_is_ignored():
    html = "<html><body><ul>" + "".join(
        f'<li class="row"><a href="/item{i}">Item {i}</a></li>' for i in range(3)
    ) + "</ul></body></html>"
    result = extract_index(html, "https://example.com/")
    assert result.listings == []  # 3 items, below MIN_REPEATED_ITEMS=5


def test_synthetic_repeated_grid_extracts_all_fields():
    html = "<html><body><div class='grid'>" + "".join(
        f'<div class="card"><a href="/listing/{i}">Widget {i}</a>'
        f'<span>$ {10 + i}</span><img src="/img/{i}.jpg"></div>'
        for i in range(6)
    ) + "</div></body></html>"
    result = extract_index(html, "https://shop.example.com/")
    assert len(result.listings) == 6
    for i, item in enumerate(result.listings):
        assert item.url == f"https://shop.example.com/listing/{i}"
        assert item.price == f"$ {10 + i}"
        assert item.image == f"https://shop.example.com/img/{i}.jpg"


def test_lazy_loaded_image_prefers_data_src_over_placeholder_src():
    html = "<html><body><div class='grid'>" + "".join(
        f'<div class="card"><a href="/listing/{i}">Widget {i}</a>'
        f'<span>$ {10 + i}</span><img src="/placeholder.gif" data-src="/real/{i}.jpg"></div>'
        for i in range(6)
    ) + "</div></body></html>"
    result = extract_index(html, "https://shop.example.com/")
    assert len(result.listings) == 6
    for i, item in enumerate(result.listings):
        assert item.image == f"https://shop.example.com/real/{i}.jpg"


def test_truncates_at_max_items():
    html = "<html><body><ul>" + "".join(
        f'<li><a href="/item/{i}">Item {i}</a></li>' for i in range(250)
    ) + "</ul></body></html>"
    result = extract_index(html, "https://example.com/")
    assert len(result.listings) == 200  # MAX_ITEMS
    assert result.truncated is True
