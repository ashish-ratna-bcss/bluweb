from pathlib import Path

from app.services.extraction.table_extractor import extract_tables

_FIXTURES = Path(__file__).parent / "fixtures"

TABLE_WITH_THEAD_TBODY = """
<table>
<caption>Status Report</caption>
<thead><tr><th>Name</th><th>Status</th><th>Date</th></tr></thead>
<tbody>
<tr><td>Example</td><td>Active</td><td>2026-09-05</td></tr>
<tr><td>Other</td><td>Inactive</td><td>2026-09-01</td></tr>
</tbody>
</table>
"""

TABLE_WITH_THEAD_NO_TBODY = """
<table>
<thead><tr><th>Name</th><th>Status</th></tr></thead>
<tr><td>Example</td><td>Active</td></tr>
<tr><td>Other</td><td>Inactive</td></tr>
</table>
"""

TABLE_FIRST_ROW_HEADER_NO_THEAD = """
<table>
<tr><th>Col A</th><th>Col B</th></tr>
<tr><td>1</td><td>2</td></tr>
<tr><td>3</td><td>4</td></tr>
</table>
"""

LAYOUT_TABLE_NO_HEADER_SIGNAL = """
<table>
<tr><td><img src="logo.png"/></td><td>Site Name</td></tr>
</table>
"""

TOO_FEW_ROWS_TABLE = """
<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>
"""

SINGLE_COLUMN_TABLE = """
<table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr><tr><td>2</td></tr></tbody></table>
"""

TABLE_WITH_TH_ROW_HEADER_IN_BODY = """
<table>
<thead><tr><th>City</th><th>Population</th><th>Area</th></tr></thead>
<tbody>
<tr><th scope="row">Jakarta</th><td>10,154,134</td><td>664</td></tr>
<tr><th scope="row">Dhaka</th><td>10,295,407</td><td>338</td></tr>
</tbody>
</table>
"""


def test_thead_tbody_table_extracted_with_correct_headers_and_rows():
    tables = extract_tables(TABLE_WITH_THEAD_TBODY)
    assert len(tables) == 1
    table = tables[0]
    assert table.headers == ["Name", "Status", "Date"]
    assert table.rows == [["Example", "Active", "2026-09-05"], ["Other", "Inactive", "2026-09-01"]]
    assert table.caption == "Status Report"


def test_thead_without_tbody_still_excludes_header_row_from_data():
    tables = extract_tables(TABLE_WITH_THEAD_NO_TBODY)
    assert len(tables) == 1
    assert tables[0].headers == ["Name", "Status"]
    assert len(tables[0].rows) == 2
    assert tables[0].rows[0] == ["Example", "Active"]


def test_first_row_th_used_as_header_when_no_thead_present():
    tables = extract_tables(TABLE_FIRST_ROW_HEADER_NO_THEAD)
    assert len(tables) == 1
    assert tables[0].headers == ["Col A", "Col B"]
    assert tables[0].rows == [["1", "2"], ["3", "4"]]


def test_layout_table_with_no_header_signal_is_rejected():
    assert extract_tables(LAYOUT_TABLE_NO_HEADER_SIGNAL) == []


def test_table_below_min_rows_is_rejected():
    assert extract_tables(TOO_FEW_ROWS_TABLE) == []


def test_single_column_table_is_rejected():
    assert extract_tables(SINGLE_COLUMN_TABLE) == []


def test_row_header_th_cell_in_tbody_is_not_dropped():
    """Real bug found live against an actual Wikipedia wikitable: its row's
    identifying cell (city name) is a `<th scope="row">`, not a `<td>` -- a
    td-only query silently dropped the first column from every row."""
    tables = extract_tables(TABLE_WITH_TH_ROW_HEADER_IN_BODY)
    assert len(tables) == 1
    assert tables[0].rows[0] == ["Jakarta", "10,154,134", "664"]
    assert tables[0].rows[1] == ["Dhaka", "10,295,407", "338"]


def test_multiple_real_tables_on_one_page():
    html = TABLE_WITH_THEAD_TBODY + TABLE_FIRST_ROW_HEADER_NO_THEAD
    tables = extract_tables(html)
    assert len(tables) == 2


def test_real_wikipedia_multi_row_header_table_aligns_after_grouped_header_fix():
    """Grouped/multi-row headers (colspan/rowspan) are expanded into leaf
    column labels so data cells align. Previously this fixture locked in
    misalignment (6 group headers vs 10-12 data cells); that limitation is
    closed in the final completion pass."""
    html = (_FIXTURES / "generic_wikipedia_table_page.html").read_text()
    tables = extract_tables(html)

    cities_table = next(t for t in tables if t.headers and t.headers[0].startswith("City"))
    assert len(cities_table.headers) >= 10
    assert len(cities_table.rows[0]) == len(cities_table.headers)
    assert cities_table.header_row_count >= 2
