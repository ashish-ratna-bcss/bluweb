from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from app.services.preflight.models import HTMLAnalysisResult

_DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")


def analyze_html(html: str, base_url: str) -> HTMLAnalysisResult:
    tree = LexborHTMLParser(html)

    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else None

    body_node = tree.body
    meaningful_text = body_node.text(separator=" ", strip=True) if body_node else ""

    script_bytes = sum(len(node.html or "") for node in tree.css("script"))
    # `script_bytes` measures each <script> tag's OWN markup -- for
    # `<script src="bundle.js">` that's just the short wrapper tag, not the
    # (unfetched) bundle's real weight. A modern bundler-built SPA often
    # ships its entire app as 2+ external chunks with almost no inline JS,
    # which `script_bytes` alone can't see -- this count is the structural
    # proxy for that (Phase 8.1 section 4), used alongside it, not instead.
    external_script_count = sum(1 for node in tree.css("script[src]"))
    heading_count = len(tree.css("h1, h2, h3"))
    has_json_ld = bool(tree.css('script[type="application/ld+json"]'))
    has_open_graph = bool(tree.css('meta[property^="og:"]'))

    canonical_node = tree.css_first('link[rel="canonical"]')
    canonical_url = urljoin(base_url, canonical_node.attributes.get("href")) if (
        canonical_node and canonical_node.attributes.get("href")
    ) else None

    base_domain = urlsplit(base_url).netloc
    internal_links: list[str] = []
    document_links: list[str] = []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        if urlsplit(absolute).netloc == base_domain:
            internal_links.append(absolute)
        if absolute.lower().endswith(_DOCUMENT_EXTENSIONS):
            document_links.append(absolute)

    return HTMLAnalysisResult(
        title=title,
        meaningful_text_length=len(meaningful_text),
        total_html_length=len(html),
        script_bytes=script_bytes,
        external_script_count=external_script_count,
        heading_count=heading_count,
        has_json_ld=has_json_ld,
        has_open_graph=has_open_graph,
        canonical_url=canonical_url,
        internal_links=internal_links[:200],
        image_count=len(tree.css("img")),
        document_links=list(dict.fromkeys(document_links))[:50],
        iframe_count=len(tree.css("iframe")),
    )
