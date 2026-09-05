"""Extraction provenance: for each important field on an already-built
document, record where the value came from. Extends field-level provenance
to structured sub-results (tables, lists, index items) without a separate
provenance database -- everything lives under
`extracted_metadata["provenance"]`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.services.extraction.structured_data import StructuredData

_BODY_SNIPPET_CHARS = 200

_BODY_SOURCE_BY_EXTRACTOR = {
    "news": "trafilatura", "blog": "trafilatura", "generic": "trafilatura",
    "scrapling": "scrapling", "forum": "dom", "listing": "dom", "index": "dom",
}


@dataclass
class ProvenanceEntry:
    value: object
    source: str
    extractor: str
    confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


def build_provenance(document, structured: StructuredData | None) -> dict[str, object]:
    """Returns a plain dict ready for `extracted_metadata["provenance"]`."""
    provenance: dict[str, object] = {}
    used_structured = structured is not None and bool(structured.raw_json_ld or structured.schema_type)
    structured_source = "json_ld" if (structured and structured.raw_json_ld) else "schema_org"

    if document.headline:
        if used_structured and structured.headline == document.headline:
            provenance["title"] = ProvenanceEntry(document.headline, structured_source, document.extractor, 0.95).to_dict()
        else:
            provenance["title"] = ProvenanceEntry(document.headline, "dom", document.extractor, document.confidence).to_dict()

    if document.author:
        if used_structured and structured.author == document.author:
            provenance["author"] = ProvenanceEntry(document.author, structured_source, document.extractor, 0.9).to_dict()
        else:
            provenance["author"] = ProvenanceEntry(document.author, "dom", document.extractor, document.confidence).to_dict()

    if document.body:
        snippet = document.body[:_BODY_SNIPPET_CHARS]
        source = _BODY_SOURCE_BY_EXTRACTOR.get(document.extractor, "unknown")
        provenance["body"] = ProvenanceEntry(snippet, source, document.extractor, document.confidence).to_dict()

    price = (document.raw_metadata or {}).get("price")
    if price:
        source = "schema_org" if used_structured else "dom"
        provenance["price"] = ProvenanceEntry(price, source, document.extractor, 0.9 if used_structured else 0.6).to_dict()

    meta = document.raw_metadata or {}
    tables = meta.get("tables") or []
    if tables:
        provenance["tables"] = ProvenanceEntry(
            {"count": len(tables), "captions": [t.get("caption") for t in tables[:5] if isinstance(t, dict)]},
            "dom",
            document.extractor,
            0.7,
        ).to_dict()

    lists = meta.get("lists") or []
    if lists:
        provenance["lists"] = ProvenanceEntry(
            {
                "count": len(lists),
                "headings": [lst.get("heading") for lst in lists[:5] if isinstance(lst, dict)],
            },
            "dom",
            document.extractor,
            0.65,
        ).to_dict()

    listings = meta.get("listings") or []
    if listings:
        provenance["index_items"] = ProvenanceEntry(
            {
                "count": len(listings),
                "sample_ids": [item.get("id") for item in listings[:5] if isinstance(item, dict)],
            },
            "dom",
            document.extractor,
            0.7,
        ).to_dict()

    completeness = meta.get("completeness")
    if completeness is not None:
        provenance["completeness"] = ProvenanceEntry(
            completeness, "scorer", document.extractor, 0.8,
        ).to_dict()

    if not provenance:
        provenance["_note"] = ProvenanceEntry(None, "unknown", document.extractor, 0.0).to_dict()

    return provenance
