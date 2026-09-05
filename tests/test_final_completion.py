"""Final completion-pass tests: offset/start pagination, lists, tables,
JS multi-signal detection, infinite-scroll budgets, browser learning."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.crawling.browser_scroll import ScrollBudget
from app.services.crawling.domain_profile_service import decide_routing
from app.services.crawling.fetch_router import FetchStrategy
from app.services.discovery.pagination import detect_pagination
from app.services.extraction.list_extractor import extract_lists
from app.services.extraction.provenance import build_provenance
from app.services.extraction.structured_data import StructuredData
from app.services.extraction.table_extractor import extract_tables
from app.services.preflight.html import analyze_html
from app.services.preflight.javascript import assess_javascript_dependency

_FIXTURES = Path(__file__).parent / "fixtures"


def test_offset_pagination_infers_next_from_current_url_and_observed_step():
    html = """
    <html><body>
    <a href="/items?offset=0">1</a>
    <a href="/items?offset=20">2</a>
    <a href="/items?offset=40">3</a>
    </body></html>
    """
    info = detect_pagination(html, "https://example.com/items?offset=0")
    assert info.next_url == "https://example.com/items?offset=20"
    assert info.method == "offset"
    assert info.page_size == 20
    assert "{offset}" in (info.url_pattern or "")


def test_start_pagination_infers_next_from_observed_sequence():
    info = detect_pagination(
        '<html><body><a href="/x?start=50">50</a><a href="/x?start=75">75</a></body></html>',
        "https://example.com/x?start=25",
    )
    assert info.next_url is not None
    assert "start=" in info.next_url
    assert info.page_size == 25


def test_offset_not_invented_without_evidence():
    html = "<html><body><p>No pager</p></body></html>"
    info = detect_pagination(html, "https://example.com/items")
    assert info.next_url is None


def test_content_list_extracted_with_heading_nav_rejected():
    html = """
    <html><body>
    <nav><ul><li><a href="/a">Home</a></li><li><a href="/b">About</a></li>
    <li><a href="/c">Blog</a></li></ul></nav>
    <h2>Installation steps</h2>
    <ol>
      <li>Install dependencies with the package manager for your platform.</li>
      <li>Configure the environment variables listed in the documentation.</li>
      <li>Run the database migrations before starting the application server.</li>
    </ol>
    </body></html>
    """
    lists = extract_lists(html, "https://example.com/docs")
    assert len(lists) == 1
    assert lists[0].heading == "Installation steps"
    assert lists[0].list_type == "ol"
    assert len(lists[0].items) == 3


def test_grouped_header_table_aligns_leaf_columns():
    html = """
    <table>
    <thead>
      <tr>
        <th rowspan="2">City</th>
        <th colspan="2">City proper</th>
        <th colspan="2">Metro</th>
      </tr>
      <tr>
        <th>Population</th><th>Area</th>
        <th>Population</th><th>Area</th>
      </tr>
    </thead>
    <tbody>
      <tr><th>Jakarta</th><td>10</td><td>664</td><td>33</td><td>7063</td></tr>
      <tr><th>Dhaka</th><td>10</td><td>338</td><td>36</td><td>2570</td></tr>
    </tbody>
    </table>
    """
    tables = extract_tables(html)
    assert len(tables) == 1
    assert len(tables[0].headers) == 5
    assert tables[0].headers[0] == "City"
    assert "City proper" in tables[0].headers[1]
    assert "Population" in tables[0].headers[1]
    assert len(tables[0].rows[0]) == 5


def test_wikipedia_fixture_grouped_headers_no_longer_misalign():
    html = (_FIXTURES / "generic_wikipedia_table_page.html").read_text()
    tables = extract_tables(html)
    cities_table = next(t for t in tables if t.headers and "City" in t.headers[0])
    assert len(cities_table.headers) >= 10
    assert len(cities_table.rows[0]) == len(cities_table.headers)


def test_js_external_bundle_spa_flagged_without_inline_blob():
    html = """
    <html><head><title>App</title>
    <script src="/static/runtime.js"></script>
    <script src="/static/vendor.js"></script>
    <script src="/static/main.js"></script>
    </head><body><div id="root"></div></body></html>
    """
    analysis = analyze_html(html, "https://spa.example.com/")
    assessment = assess_javascript_dependency(html, analysis)
    assert assessment.likely_requires_browser is True
    assert analysis.external_script_count >= 3


def test_js_hydration_marker_contributes_even_with_moderate_seo_text():
    filler = "Welcome to the app. " * 15  # ~300 chars -- above low_text threshold
    html = f"""
    <html><head><script id="__NEXT_DATA__" type="application/json">{{"props":{{}}}}</script>
    <script src="/_next/static/chunks/main.js"></script>
    <script src="/_next/static/chunks/webpack.js"></script>
    <script src="/_next/static/chunks/framework.js"></script>
    </head><body><div id="__next"></div><p>{filler}</p></body></html>
    """
    analysis = analyze_html(html, "https://next.example.com/")
    assessment = assess_javascript_dependency(html, analysis)
    assert assessment.hydration_markers_detected is True
    assert assessment.likely_requires_browser is True


def test_scroll_budget_defaults_are_bounded():
    budget = ScrollBudget()
    assert budget.max_scrolls <= 10
    assert budget.max_consecutive_no_change >= 1
    assert budget.max_browser_seconds > 0


def test_domain_learning_prefers_browser_when_quality_compares_win():
    stats = SimpleNamespace(
        http_attempts=10, http_successes=9, http_extraction_failures=0,
        browser_attempts=8, browser_successes=7,
        preferred_extractor="generic",
        browser_superior_count=7, http_superior_count=1, browser_equivalent_count=0,
        avg_extraction_quality=0.7, quality_observations=10,
    )
    decision = decide_routing(requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=5)
    assert decision.strategy == FetchStrategy.BROWSER
    assert "browser superior" in " ".join(decision.reasons).lower()


def test_provenance_includes_tables_lists_index_items():
    doc = SimpleNamespace(
        headline="Index",
        author=None,
        body="item1\n\nitem2",
        extractor="index",
        confidence=0.6,
        raw_metadata={
            "tables": [{"headers": ["A"], "rows": [["1"]], "caption": None}],
            "lists": [{"heading": "Steps", "items": []}],
            "listings": [{"id": "u1"}, {"id": "u2"}],
            "completeness": 0.8,
        },
    )
    prov = build_provenance(doc, StructuredData())
    assert "index_items" in prov
    assert "tables" in prov
    assert "lists" in prov
    assert "completeness" in prov
