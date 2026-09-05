"""Generic repeated-item index/listing extraction (spec Phase 9 section
18) -- produces the `listings[]` structure Phase 7/8's own reports
flagged as missing for CLASSIFIED_INDEX pages (no index-page extractor
existed; CLASSIFIED_INDEX documents were routed through the single-listing
extractor, which expects one Product/Offer block, not a collection).

Deliberately domain-agnostic (spec section 46: no `if domain == ...`
hacks). The detection is purely structural: find the parent whose direct
children repeat most often with the same (tag, class-signature) and each
contain a link -- that's the "item card" pattern shared by classified
grids, news-index cards, forum-thread-index rows, and search results
alike. Verified against a real Craigslist search-results page (357
`li.cl-static-search-result` items, tags/classes never referenced by name
in this code -- only their *repetition* is used).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

MIN_REPEATED_ITEMS = 5  # fewer than this could be a coincidental structural match, not a real listing grid
MAX_ITEMS = 200  # bounded -- an index page with thousands of items still only needs a representative sample

_MONEY_RE = re.compile(r"(?:₹|Rs\.?|INR|\$|USD|€|£)\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE)


@dataclass
class ListingItem:
    item_id: str
    title: str | None
    url: str | None
    price: str | None
    location: str | None
    description: str
    image: str | None = None
    summary: str | None = None
    published_at: str | None = None
    author: str | None = None
    status: str | None = None


@dataclass
class IndexExtractionResult:
    listings: list[ListingItem] = field(default_factory=list)
    container_selector_signature: str | None = None  # diagnostic only, not a CSS selector guaranteed to still match later
    truncated: bool = False


def extract_index(html: str, base_url: str) -> IndexExtractionResult:
    tree = LexborHTMLParser(html)
    group = _find_largest_repeated_group(tree)
    if group is None:
        return IndexExtractionResult()

    signature, nodes = group
    listings = []
    for node in nodes[:MAX_ITEMS]:
        item = _extract_item(node, base_url)
        if item is not None:
            listings.append(item)

    return IndexExtractionResult(
        listings=listings, container_selector_signature=signature, truncated=len(nodes) > MAX_ITEMS,
    )


def _find_largest_repeated_group(tree) -> tuple[str, list] | None:
    best_signature = None
    best_nodes: list = []

    for parent in tree.css("*"):
        if _is_chrome_container(parent):
            continue
        buckets: dict[str, list] = {}
        for child in parent.iter(include_text=False):
            # `.iter(include_text=False)` already yields only direct
            # children -- do NOT add a `child.parent is parent` guard:
            # selectolax node objects are lightweight wrappers created
            # fresh per access, so `is` identity comparison between two
            # separately-obtained references is always False even for the
            # same underlying DOM node (found live while testing this
            # against the real Craigslist fixture -- silently discarded
            # every child and made the whole detector return nothing).
            sig = _signature(child)
            if sig is None:
                continue
            buckets.setdefault(sig, []).append(child)

        for sig, nodes in buckets.items():
            if len(nodes) >= MIN_REPEATED_ITEMS and len(nodes) > len(best_nodes):
                with_links = [n for n in nodes if n.css_first("a[href]") is not None or (n.tag == "a" and n.attributes.get("href"))]
                if len(with_links) >= MIN_REPEATED_ITEMS:
                    best_signature, best_nodes = sig, with_links

    if best_signature is None:
        return None
    return best_signature, best_nodes


_CHROME_TAGS = frozenset({"nav", "header", "footer", "aside"})
_CHROME_CLASS_HINTS = ("nav", "menu", "navbar", "breadcrumb", "footer", "social", "pagination", "pager")


def _is_chrome_container(node) -> bool:
    tag = (node.tag or "").lower()
    if tag in _CHROME_TAGS:
        return True
    classes = (node.attributes.get("class") or "").lower()
    role = (node.attributes.get("role") or "").lower()
    if role in ("navigation", "menubar", "contentinfo"):
        return True
    return any(hint in classes for hint in _CHROME_CLASS_HINTS)


def _signature(node) -> str | None:
    if node.tag in ("script", "style", "link", "meta", "br", "img"):
        return None
    classes = node.attributes.get("class")
    class_part = "." + ".".join(sorted(classes.split())) if classes else ""
    return f"{node.tag}{class_part}"


# Lazy-load attribute conventions (Phase 8.1 section 7), checked before the
# plain `src` -- a lazy-loaded <img> commonly ships a tiny placeholder/blank
# gif in `src` and the real image URL in one of these instead; only ever
# reads an already-present attribute, never fetches anything.
_LAZY_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-url")


def _resolve_image_url(image_node, base_url: str) -> str | None:
    for attr in _LAZY_SRC_ATTRS:
        value = image_node.attributes.get(attr)
        if value:
            return urljoin(base_url, value)

    srcset = image_node.attributes.get("srcset")
    if srcset:
        first_candidate = srcset.split(",")[0].strip().split(" ")[0]
        if first_candidate:
            return urljoin(base_url, first_candidate)

    src = image_node.attributes.get("src")
    return urljoin(base_url, src) if src else None


def _extract_item(node, base_url: str) -> ListingItem | None:
    link = node if (node.tag == "a" and node.attributes.get("href")) else node.css_first("a[href]")
    if link is None:
        return None
    href = link.attributes.get("href")
    url = urljoin(base_url, href) if href else None

    full_text = node.text(deep=True, separator=" ").strip()
    if len(full_text) < 3:
        return None

    # A dedicated title-ish child (common convention: class="title"/"name"/
    # "heading") beats the raw anchor text, which often includes the whole
    # card's text (price, location, ...) when the anchor wraps everything --
    # exactly the real Craigslist card shape (title attribute sits on the
    # outer repeated node, not the inner link).
    title_node = node.css_first('[class*="title" i], [class*="name" i], h1, h2, h3')
    title = (
        node.attributes.get("title")
        or (title_node.text(deep=True, separator=" ").strip() if title_node is not None else None)
        or link.attributes.get("title")
        or link.text(deep=True, separator=" ").strip()
    )
    if not title or len(title) > 300:
        # fall back to the first short-ish text run in the item when the
        # anchor text itself is unusable (empty, or the whole card's text
        # dumped into one link with no inner structure)
        title = full_text[:200]

    price_match = _MONEY_RE.search(full_text)
    price = price_match.group(0) if price_match else None

    location_node = node.css_first('[class*="location" i], [class*="place" i]')
    location = location_node.text(deep=True, separator=" ").strip() if location_node is not None else None

    image_node = node.css_first("img")
    image = _resolve_image_url(image_node, base_url) if image_node is not None else None

    description = full_text
    if price:
        description = description.replace(price, "").strip()

    author_node = node.css_first('[class*="author" i], [class*="byline" i], [rel="author"]')
    author = author_node.text(deep=True, separator=" ").strip() if author_node is not None else None
    time_node = node.css_first("time, [class*='date' i], [datetime]")
    published_at = None
    if time_node is not None:
        published_at = time_node.attributes.get("datetime") or time_node.text(strip=True) or None

    item_id = url or f"item:{hash(full_text) & 0xFFFFFFFF:x}"
    summary = description[:280] if description else None

    return ListingItem(
        item_id=item_id, title=title.strip() or None, url=url, price=price,
        location=location, description=description[:1000], image=image,
        summary=summary, published_at=published_at, author=author,
    )
