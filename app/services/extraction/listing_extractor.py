"""Classified/listing extraction (spec Phase 6 classified capability).

Same two-layer detection hierarchy as forum_extractor.py, cheapest/most-
reliable first:
1. schema.org Product/Offer JSON-LD (structured, exact) -- verified
   against a real Craigslist listing fixture (tests/fixtures/
   classified_listing_craigslist.html), which carries a full Product+Offer
   block (name/description/price/currency/location/images).
2. DOM heuristics: common price/title/description class-name patterns, for
   sites that don't publish schema.org markup for their listings.

No adapter registry here unlike forum_extractor.py: a single Product/Offer
JSON-LD shape covers the overwhelming majority of real classified sites
(it's the schema.org-recommended way to mark up exactly this), so there's
no second implementation to seam off yet -- add one if a real site turns
up that needs it.

The Scrapling adaptive-DOM fallback (spec capability 5) lives one level up
in extraction_router.py, applied uniformly to whatever page type comes
back empty from its dedicated extractor, rather than duplicated here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser

from app.services.extraction.article_extractor import ArticleDocument
from app.services.extraction.structured_data import StructuredData

_PRICE_SELECTORS = (".price", "[itemprop=price]", ".listing-price")
_TITLE_SELECTORS = ("#titletextonly", ".postingtitletext", "h1", "[itemprop=name]")
_DESCRIPTION_SELECTORS = ("#postingbody", ".description", "[itemprop=description]")
_LOCATION_SELECTORS = (".postingtitletext small", ".location", "[itemprop=addressLocality]")

_PRICE_RE = re.compile(r"[\d,]+\.?\d*")


@dataclass
class ListingDocument:
    title: str | None
    price: str | None
    currency: str | None
    location: str | None
    description: str
    images: list[str]


def extract_listing(url: str, html: str, *, structured: StructuredData) -> ArticleDocument | None:
    """Same call shape as extract_article/extract_forum: None on total
    failure, otherwise an ArticleDocument. `body` is the description text;
    price/currency/location/images -- the structure this phase is meant to
    preserve -- live in `raw_metadata`, same JSONB-reuse approach as
    forum_extractor.py."""
    listing = _from_structured_data(structured) or _from_dom_heuristics(html)

    if listing is None or len(listing.description) < 20:
        return None

    fields_detected = ["body"]
    if listing.title:
        fields_detected.append("headline")
    if listing.price:
        fields_detected.append("price")
    if listing.location:
        fields_detected.append("location")

    return ArticleDocument(
        extractor="listing",
        headline=listing.title,
        body=listing.description,
        author=None,
        publisher=None,
        published_at=None,
        updated_at=None,
        section=listing.location,
        canonical_url=structured.canonical_url or url,
        language=None,
        tags=[],
        images=listing.images,
        confidence=0.6 if listing.price else 0.4,
        fields_detected=fields_detected,
        raw_metadata={
            "price": listing.price,
            "currency": listing.currency,
            "location": listing.location,
        },
    )


def _from_structured_data(structured: StructuredData) -> ListingDocument | None:
    for block in structured.raw_json_ld:
        type_value = block.get("@type")
        if isinstance(type_value, list):
            type_value = type_value[0] if type_value else None
        if type_value not in ("Product", "Offer"):
            continue

        offer = block.get("offers", block) if type_value == "Product" else block
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        location = None
        place = offer.get("availableAtOrFrom") if isinstance(offer, dict) else None
        if isinstance(place, dict):
            address = place.get("address", {})
            if isinstance(address, dict):
                location = address.get("addressLocality")

        images = block.get("image") or []
        if isinstance(images, str):
            images = [images]

        return ListingDocument(
            title=block.get("name"),
            price=str(offer.get("price")) if isinstance(offer, dict) and offer.get("price") else None,
            currency=offer.get("priceCurrency") if isinstance(offer, dict) else None,
            location=location,
            description=str(block.get("description") or "").strip(),
            images=list(images),
        )
    return None


def _from_dom_heuristics(html: str) -> ListingDocument | None:
    tree = LexborHTMLParser(html)

    title_node = _first_match(tree, _TITLE_SELECTORS)
    price_node = _first_match(tree, _PRICE_SELECTORS)
    desc_node = _first_match(tree, _DESCRIPTION_SELECTORS)
    location_node = _first_match(tree, _LOCATION_SELECTORS)

    description = desc_node.text(deep=True, separator=" ").strip() if desc_node else ""
    if not description:
        return None

    price_text = price_node.text(strip=True) if price_node else None
    price_match = _PRICE_RE.search(price_text) if price_text else None

    return ListingDocument(
        title=title_node.text(strip=True) if title_node else None,
        price=price_match.group(0) if price_match else None,
        currency="USD" if price_text and "$" in price_text else None,
        location=location_node.text(strip=True) if location_node else None,
        description=description,
        images=[src for img in tree.css("img") if (src := _resolve_listing_image(img, html))],
    )


_LAZY_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-url")


def _resolve_listing_image(image_node, html: str) -> str | None:
    for attr in _LAZY_SRC_ATTRS:
        value = image_node.attributes.get(attr)
        if value:
            return value
    srcset = image_node.attributes.get("srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first:
            return first
    return image_node.attributes.get("src")


def _first_match(tree, selectors: tuple[str, ...]):
    for sel in selectors:
        found = tree.css_first(sel)
        if found is not None:
            return found
    return None
