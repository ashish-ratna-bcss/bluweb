"""Meaningful `<ul>`/`<ol>` extraction distinct from navigation chrome.

Navigation / menu / footer / social lists are rejected. Content lists
(procedures, rankings, article bullets, result lists) are kept with their
nearest preceding heading for context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser

MAX_LISTS = 30
MAX_ITEMS_PER_LIST = 100
MIN_ITEMS = 2
MAX_ITEM_CHARS = 500

_CHROME_TAGS = frozenset({"nav", "header", "footer", "aside"})
_CHROME_CLASS_HINTS = (
    "nav", "menu", "navbar", "breadcrumb", "footer", "social", "share",
    "pagination", "pager", "sidebar", "toc", "toolbar", "cookie",
)
_CONTENT_CLASS_HINTS = (
    "content", "article", "entry", "post", "result", "ranking", "procedure",
    "steps", "list-item", "search-result", "main",
)


@dataclass
class ListItem:
    text: str
    url: str | None = None


@dataclass
class ExtractedList:
    list_type: str  # "ul" | "ol"
    heading: str | None
    items: list[ListItem] = field(default_factory=list)
    role: str = "content"  # "content" | "navigation" (navigation should not appear)


def extract_lists(html: str, base_url: str | None = None) -> list[ExtractedList]:
    from urllib.parse import urljoin

    tree = LexborHTMLParser(html)
    results: list[ExtractedList] = []

    for node in tree.css("ul, ol"):
        if len(results) >= MAX_LISTS:
            break
        if _is_chrome_list(node):
            continue

        items: list[ListItem] = []
        for li in node.css("li")[:MAX_ITEMS_PER_LIST]:
            # Only direct-ish list items: skip nested li text double-count by
            # preferring the li's own text without deep nested lists when possible.
            text = _list_item_text(li)
            if not text or len(text) < 2:
                continue
            link = li.css_first("a[href]")
            url = None
            if link is not None and base_url and link.attributes.get("href"):
                url = urljoin(base_url, link.attributes["href"])
            elif link is not None and link.attributes.get("href"):
                url = link.attributes.get("href")
            items.append(ListItem(text=text[:MAX_ITEM_CHARS], url=url))

        if len(items) < MIN_ITEMS:
            continue
        # Pure link menus with very short labels look like nav even outside <nav>.
        if _looks_like_link_menu(items):
            continue

        results.append(ExtractedList(
            list_type=node.tag or "ul",
            heading=_nearest_heading(node),
            items=items,
            role="content",
        ))

    return results


def _is_chrome_list(node) -> bool:
    current = node
    depth = 0
    while current is not None and depth < 8:
        tag = (current.tag or "").lower()
        if tag in _CHROME_TAGS:
            return True
        classes = (current.attributes.get("class") or "").lower()
        role = (current.attributes.get("role") or "").lower()
        if role in ("navigation", "menubar", "menu", "contentinfo"):
            return True
        if any(hint in classes for hint in _CHROME_CLASS_HINTS):
            # Allow if also clearly content-marked (e.g. "article-nav" is still chrome).
            if not any(hint in classes for hint in _CONTENT_CLASS_HINTS):
                return True
        current = current.parent
        depth += 1
    return False


def _looks_like_link_menu(items: list[ListItem]) -> bool:
    if not items:
        return True
    with_links = sum(1 for i in items if i.url)
    avg_len = sum(len(i.text) for i in items) / len(items)
    # Short linked labels, nearly all linked => menu.
    return with_links >= max(2, int(0.8 * len(items))) and avg_len < 40


def _list_item_text(li) -> str:
    """Prefer shallow text; strip nested list blocks so parent items stay short."""
    nested = li.css("ul, ol")
    if not nested:
        return li.text(deep=True, separator=" ").strip()
    # Reconstruct without nested lists by concatenating non-list direct children.
    parts: list[str] = []
    for child in li.iter(include_text=False):
        if child.tag in ("ul", "ol"):
            continue
        text = child.text(deep=True, separator=" ").strip()
        if text:
            parts.append(text)
    if parts:
        return " ".join(parts).strip()
    return li.text(deep=True, separator=" ").strip()


def _nearest_heading(node) -> str | None:
    """Walk previous siblings, then parent chain, for an h1-h6."""
    current = node
    for _ in range(6):
        sibling = current.prev
        steps = 0
        while sibling is not None and steps < 10:
            tag = (sibling.tag or "").lower()
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = sibling.text(strip=True)
                return text or None
            if tag in ("ul", "ol", "table", "section", "article"):
                break
            sibling = sibling.prev
            steps += 1
        parent = current.parent
        if parent is None:
            break
        current = parent
    return None
