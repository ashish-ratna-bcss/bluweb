"""Deterministic HTML table extraction. Structural only -- no column-meaning
inference. Filters layout tables (no header signal). Supports multi-row /
grouped headers via colspan/rowspan expansion so Wikipedia-style comparison
tables keep leaf column labels aligned with data cells.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser

MIN_DATA_ROWS = 2
MIN_COLUMNS = 2
MAX_TABLES = 20
MAX_ROWS_PER_TABLE = 200
MAX_HEADER_ROWS = 4


@dataclass
class TableData:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    caption: str | None = None
    header_row_count: int = 1


def extract_tables(html: str) -> list[TableData]:
    tree = LexborHTMLParser(html)
    tables: list[TableData] = []

    for table_node in tree.css("table")[:MAX_TABLES]:
        all_rows = table_node.css("tr")
        if not all_rows:
            continue

        tbody = table_node.css_first("tbody")
        thead = table_node.css_first("thead")
        tfoot = table_node.css_first("tfoot")

        if thead is not None:
            header_row_nodes = thead.css("tr")[:MAX_HEADER_ROWS]
            if tbody is not None:
                data_row_nodes = tbody.css("tr")
            else:
                data_row_nodes = all_rows[len(thead.css("tr")) :]
        else:
            header_row_nodes, data_start = _detect_leading_header_rows(all_rows)
            data_row_nodes = all_rows[data_start:]

        if tfoot is not None:
            # Exclude tfoot rows that also appear in the data selection.
            tfoot_count = len(tfoot.css("tr"))
            if tfoot_count and data_row_nodes:
                # When tbody is present, tfoot is a sibling -- already excluded.
                # Without tbody, trailing tfoot rows may be in all_rows.
                if tbody is None and thead is None:
                    data_row_nodes = data_row_nodes[:-tfoot_count] if len(data_row_nodes) > tfoot_count else data_row_nodes

        headers = _merge_header_rows(header_row_nodes) if header_row_nodes else []

        rows: list[list[str]] = []
        for row_node in data_row_nodes[:MAX_ROWS_PER_TABLE]:
            # Skip residual all-th header-like rows that slipped past detection.
            if _row_is_header_like(row_node) and not rows:
                continue
            cells = row_node.css("td, th")
            if not cells:
                continue
            rows.append([_cell_text(c) for c in cells])

        if not headers:
            continue
        column_count = max(len(headers), max((len(r) for r in rows), default=0))
        if len(rows) < MIN_DATA_ROWS or column_count < MIN_COLUMNS:
            continue

        # Pad short rows / trim long rows to header width when close (merged
        # headers should match leaf column count); keep raw cells if wildly off.
        if headers and rows:
            width = len(headers)
            normalized = []
            for row in rows:
                if abs(len(row) - width) <= 2 and len(row) != width:
                    if len(row) < width:
                        normalized.append(row + [""] * (width - len(row)))
                    else:
                        normalized.append(row[:width])
                else:
                    normalized.append(row)
            rows = normalized

        caption_node = table_node.css_first("caption")
        tables.append(TableData(
            headers=headers,
            rows=rows,
            caption=caption_node.text(strip=True) if caption_node else None,
            header_row_count=max(1, len(header_row_nodes)),
        ))

    return tables


def _detect_leading_header_rows(all_rows) -> tuple[list, int]:
    """Take consecutive leading all/mostly-th rows as the header block."""
    header_rows = []
    for row in all_rows[:MAX_HEADER_ROWS]:
        if _row_is_header_like(row):
            header_rows.append(row)
        else:
            break
    if not header_rows:
        # Classic: first row has any <th> cells.
        if all_rows and all_rows[0].css("th"):
            return [all_rows[0]], 1
        return [], 0
    return header_rows, len(header_rows)


def _row_is_header_like(row) -> bool:
    cells = row.css("th, td")
    if not cells:
        return False
    th_count = sum(1 for c in cells if c.tag == "th")
    return th_count >= max(1, (len(cells) + 1) // 2) and th_count == len(cells)


def _merge_header_rows(header_row_nodes) -> list[str]:
    if not header_row_nodes:
        return []
    if len(header_row_nodes) == 1:
        return [_cell_text(c) for c in header_row_nodes[0].css("th, td")]

    grid: dict[tuple[int, int], str] = {}
    occupied: set[tuple[int, int]] = set()
    n_rows = len(header_row_nodes)

    for r, row in enumerate(header_row_nodes):
        c = 0
        for cell in row.css("th, td"):
            while (r, c) in occupied:
                c += 1
            colspan = _span(cell, "colspan")
            rowspan = _span(cell, "rowspan")
            text = _cell_text(cell)
            for dr in range(rowspan):
                for dc in range(colspan):
                    rr, cc = r + dr, c + dc
                    occupied.add((rr, cc))
                    # Only the origin cell stores text; rowspan/colspan
                    # expansion fills occupied so later cells skip slots.
                    if dr == 0 and dc == 0:
                        grid[(rr, cc)] = text
                    elif dr == 0 and text:
                        # Horizontal group label applies to each spanned col.
                        grid[(rr, cc)] = text
            c += colspan

    if not occupied:
        return []
    n_cols = max(cc for _, cc in occupied) + 1

    headers: list[str] = []
    for c in range(n_cols):
        parts: list[str] = []
        for r in range(n_rows):
            text = grid.get((r, c))
            if not text:
                # Covered by a rowspan from above -- don't repeat the label.
                continue
            if not parts or parts[-1] != text:
                parts.append(text)
        headers.append(" / ".join(parts) if parts else f"col_{c + 1}")
    return headers


def _span(cell, name: str) -> int:
    raw = cell.attributes.get(name)
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, value)


def _cell_text(cell) -> str:
    return cell.text(strip=True)
