from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

from app.services.preflight.models import HTMLAnalysisResult, JavaScriptAssessment

_SPA_ROOT_SELECTORS = ["#root", "#app", "#__next", "#__nuxt", "div[data-reactroot]", "[data-v-app]", "app-root"]
_MIN_MEANINGFUL_TEXT_CHARS = 200
_MODERATE_TEXT_CHARS = 800
_HIGH_SCRIPT_RATIO = 3.0
_MODERATE_SCRIPT_RATIO = 1.5
_MANY_EXTERNAL_SCRIPTS = 3
_HYDRATION_MARKERS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "__NUXT_DATA__",
    "window.__INITIAL_STATE__",
    "data-reactroot",
    "ng-version",
    "data-server-rendered",
    "webpackJsonp",
    "__vue_app__",
)
_FRAMEWORK_HINT_RE = re.compile(
    r"(react(-dom)?(\.production)?\.min\.js|vue(\.runtime)?(\.min)?\.js|angular|"
    r"_next/static|/_nuxt/|chunk-vendors|vite/client)",
    re.IGNORECASE,
)


def assess_javascript_dependency(
    html: str,
    analysis: HTMLAnalysisResult,
    *,
    extraction_quality: float | None = None,
    discovered_link_count: int | None = None,
    domain_js_required_rate: float | None = None,
) -> JavaScriptAssessment:
    """Multi-signal JS-dependency assessment. Playwright is never required
    solely because scripts exist -- almost every site has scripts.

    Signals are combined (not a single AND/OR): empty SPA roots and
    hydration markers are strong even with moderate SEO text; low text +
    external bundles remain the classic empty-shell case; prior domain
    behavior and failed extraction quality are soft boosts.

    Known residual: a page that ships *substantial* static fallback text
    alongside a client-rendered app can still under-trigger without a
    render compare (WhatsApp Web-class). Escalation quality comparison
    in the crawl engine closes that on the fetch path.
    """
    text_len = max(analysis.meaningful_text_length, 1)
    ratio = analysis.script_bytes / text_len
    tree = LexborHTMLParser(html)

    empty_spa_root = _empty_spa_root(tree)
    hydration = _hydration_markers(html, analysis)
    framework_assets = bool(_FRAMEWORK_HINT_RE.search(html))
    low_text = analysis.meaningful_text_length < _MIN_MEANINGFUL_TEXT_CHARS
    moderate_text = analysis.meaningful_text_length < _MODERATE_TEXT_CHARS
    high_ratio = ratio > _HIGH_SCRIPT_RATIO
    moderate_ratio = ratio > _MODERATE_SCRIPT_RATIO
    many_external = analysis.external_script_count >= _MANY_EXTERNAL_SCRIPTS
    thin_links = (discovered_link_count is not None and discovered_link_count < 3) or (
        discovered_link_count is None and len(analysis.internal_links) < 3
    )
    thin_structured = not (analysis.has_json_ld or analysis.has_open_graph)
    low_quality = extraction_quality is not None and extraction_quality < 0.35
    domain_prior = domain_js_required_rate is not None and domain_js_required_rate >= 0.4

    score = 0
    signals: list[str] = []

    if empty_spa_root:
        score += 4
        signals.append("empty SPA root")
    if hydration:
        score += 3
        signals.append("hydration/framework markers")
    if framework_assets and (low_text or empty_spa_root):
        score += 2
        signals.append("framework asset URLs")
    if low_text and (high_ratio or many_external):
        score += 4
        signals.append("low text + script density/external bundles")
    elif moderate_text and many_external and (moderate_ratio or thin_structured):
        score += 2
        signals.append("moderate text + many external scripts")
    if low_text and thin_links and thin_structured and analysis.external_script_count >= 1:
        score += 2
        signals.append("thin content/link/metadata shell")
    if low_quality and (many_external or hydration or empty_spa_root):
        score += 2
        signals.append("low extraction quality with JS signals")
    if domain_prior and (low_text or empty_spa_root or hydration or many_external):
        score += 1
        signals.append(f"domain JS-required rate {domain_js_required_rate:.0%}")

    # iframe-only shells (rare but real for embedded viewers)
    if analysis.iframe_count >= 1 and low_text and analysis.meaningful_text_length < 80:
        score += 2
        signals.append("iframe-dominant shell")

    likely_requires_browser = score >= 4

    if likely_requires_browser:
        reasoning = (
            f"{analysis.meaningful_text_length} chars visible text; "
            f"signals=[{', '.join(signals)}]; score={score}"
        )
    else:
        reasoning = (
            f"{analysis.meaningful_text_length} chars of visible text found without rendering"
            + (f" (soft signals: {', '.join(signals)}; score={score})" if signals else "")
        )

    return JavaScriptAssessment(
        likely_requires_browser=likely_requires_browser,
        script_to_text_ratio=round(ratio, 2),
        empty_content_containers_detected=empty_spa_root,
        reasoning=reasoning,
        hydration_markers_detected=hydration,
        framework_assets_detected=framework_assets,
        signal_score=score,
        signals=signals,
    )


def _empty_spa_root(tree) -> bool:
    for selector in _SPA_ROOT_SELECTORS:
        node = tree.css_first(selector)
        if node is not None and len(node.text(strip=True)) < 40:
            return True
    return False


def _hydration_markers(html: str, analysis: HTMLAnalysisResult) -> bool:
    if any(marker in html for marker in _HYDRATION_MARKERS):
        return True
    # id/class markers already counted via empty root; also catch noscript-only bodies
    return analysis.external_script_count >= 5 and analysis.meaningful_text_length < _MIN_MEANINGFUL_TEXT_CHARS
