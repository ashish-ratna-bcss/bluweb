"""Generic pagination detection. Multiple signals, none hardcoded to a
single site: `rel=next`, link-text heuristics, numbered 1-2-3 runs, and
evidence-backed `offset`/`start` (and aliases) next-value inference.

Offset/start URLs are NEVER invented from thin air -- they require the
current URL (or an observed next link) to already carry the parameter,
plus a page-size/step inferred from that value or from an observed
sibling sequence. Caps prevent infinite pagination / URL explosion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

_NEXT_TEXT_RE = re.compile(r"\b(next|older|load more|show more)\b|»|›", re.IGNORECASE)
_PAGE_PARAM_RE = re.compile(r"[?&](page|p|pg)=(\d+)", re.IGNORECASE)
_PAGE_PATH_RE = re.compile(r"/page[/-](\d+)/?$", re.IGNORECASE)
_OFFSET_PARAMS = ("offset", "start", "from", "skip")
_MAX_INFERRED_OFFSET = 10_000  # hard ceiling -- never invent deep offset URLs


@dataclass
class PaginationInfo:
    next_url: str | None = None
    method: str | None = None  # "rel_next" | "link_text" | "numbered" | "offset" | "start"
    url_pattern: str | None = None
    page_size: int | None = None
    current_offset: int | None = None


def detect_pagination(html: str, base_url: str) -> PaginationInfo:
    tree = LexborHTMLParser(html)

    rel_next = tree.css_first('link[rel="next"], a[rel~="next"]')
    if rel_next is not None:
        href = rel_next.attributes.get("href")
        if href:
            url = urljoin(base_url, href)
            # Prefer an explicit next link even when it uses offset/start.
            offset_info = _offset_info_from_url(url, base_url)
            if offset_info is not None:
                return offset_info
            return PaginationInfo(next_url=url, method="rel_next", url_pattern=_to_pattern(url))

    for anchor in tree.css("a[href]"):
        text = anchor.text(strip=True)
        if text and _NEXT_TEXT_RE.search(text):
            href = anchor.attributes.get("href")
            if href:
                url = urljoin(base_url, href)
                offset_info = _offset_info_from_url(url, base_url)
                if offset_info is not None:
                    return offset_info
                return PaginationInfo(next_url=url, method="link_text", url_pattern=_to_pattern(url))

    numbered = _detect_numbered_pagination(tree, base_url)
    offset = _detect_offset_pagination(tree, base_url)

    # Prefer offset/start when the current URL (or observed links) carry those
    # params -- numbered "1 2 3" text often coexists with ?offset= and would
    # otherwise steal the signal with a page= rewrite that doesn't match.
    if offset is not None and (
        _parse_offset_param(base_url) is not None
        or (offset.method in ("offset", "start", "from", "skip"))
    ):
        # If numbered also fired but points at a page= URL while offset is
        # present, still prefer offset.
        if numbered is None or _parse_offset_param(numbered.next_url or "") is not None or _parse_offset_param(base_url) is not None:
            return offset

    if numbered is not None:
        return numbered

    if offset is not None:
        return offset

    return PaginationInfo()


def _detect_numbered_pagination(tree, base_url: str) -> PaginationInfo | None:
    numbered_links: list[tuple[int, str]] = []
    for anchor in tree.css("a[href]"):
        text = anchor.text(strip=True)
        if text.isdigit():
            href = anchor.attributes.get("href")
            if href:
                numbered_links.append((int(text), urljoin(base_url, href)))

    if len(numbered_links) < 3:
        return None

    candidates = [(n, url) for n, url in numbered_links if n > 1]
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    _, next_url = candidates[0]
    return PaginationInfo(next_url=next_url, method="numbered", url_pattern=_to_pattern(next_url))


def _detect_offset_pagination(tree, base_url: str) -> PaginationInfo | None:
    """Infer the next offset/start URL only when evidence exists:

    1. Current URL already has offset/start/from/skip, OR
    2. At least two observed links share the same param with an arithmetic
       step (page size), from which we can safely advance once.
    """
    current = _parse_offset_param(base_url)
    observed: list[tuple[str, int, str]] = []  # (param, value, absolute_url)
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href)
        parsed = _parse_offset_param(absolute)
        if parsed is not None:
            observed.append((*parsed, absolute))

    if current is not None:
        param, value = current
        step = _infer_step(param, value, observed)
        if step is None or step <= 0:
            return None
        next_value = value + step
        if next_value > _MAX_INFERRED_OFFSET:
            return None
        next_url = _replace_query_param(base_url, param, next_value)
        return PaginationInfo(
            next_url=next_url,
            method=param if param in ("offset", "start") else "offset",
            url_pattern=_to_offset_pattern(base_url, param),
            page_size=step,
            current_offset=value,
        )

    # No offset on current URL -- require an observed sequence with a clear step.
    by_param: dict[str, list[tuple[int, str]]] = {}
    for param, value, absolute in observed:
        by_param.setdefault(param, []).append((value, absolute))

    for param, pairs in by_param.items():
        values = sorted({v for v, _ in pairs})
        if len(values) < 2:
            continue
        steps = {values[i + 1] - values[i] for i in range(len(values) - 1)}
        if len(steps) != 1:
            continue
        step = steps.pop()
        if step <= 0:
            continue
        # Prefer advancing from the smallest observed value (page 0/start).
        current_value = values[0]
        next_value = current_value + step
        if next_value in {v for v, _ in pairs}:
            # Already have that page linked -- use the observed URL.
            next_url = next(url for v, url in pairs if v == next_value)
        else:
            if next_value > _MAX_INFERRED_OFFSET:
                continue
            sample = pairs[0][1]
            next_url = _replace_query_param(sample, param, next_value)
        return PaginationInfo(
            next_url=next_url,
            method=param if param in ("offset", "start") else "offset",
            url_pattern=_to_offset_pattern(next_url, param),
            page_size=step,
            current_offset=current_value,
        )

    return None


def _offset_info_from_url(next_url: str, base_url: str) -> PaginationInfo | None:
    parsed_next = _parse_offset_param(next_url)
    if parsed_next is None:
        return None
    param, next_value = parsed_next
    current = _parse_offset_param(base_url)
    current_value = current[1] if current and current[0] == param else 0
    step = next_value - current_value if next_value > current_value else None
    return PaginationInfo(
        next_url=next_url,
        method=param if param in ("offset", "start") else "offset",
        url_pattern=_to_offset_pattern(next_url, param),
        page_size=step,
        current_offset=current_value,
    )


def _infer_step(param: str, current_value: int, observed: list[tuple[str, int, str]]) -> int | None:
    same = sorted({v for p, v, _ in observed if p == param})
    if current_value == 0 and not same:
        # No sibling evidence and offset=0 -- cannot invent a page size.
        return None
    if same:
        greater = [v for v in same if v > current_value]
        if greater:
            return min(greater) - current_value
        # All observed offsets are <= current -- use the gcd-like common step
        # from the observed sequence if one exists.
        if len(same) >= 2:
            steps = {same[i + 1] - same[i] for i in range(len(same) - 1)}
            if len(steps) == 1:
                step = steps.pop()
                return step if step > 0 else None
    # Single next jump not available; if current itself equals a common
    # page size (20/25/50/100), treat that as the step when starting at 0
    # is impossible -- only when current > 0 (evidence of page size).
    if current_value > 0:
        return current_value
    return None


def _parse_offset_param(url: str) -> tuple[str, int] | None:
    query = parse_qs(urlsplit(url).query, keep_blank_values=False)
    for param in _OFFSET_PARAMS:
        if param in query and query[param]:
            raw = query[param][0]
            if raw.isdigit():
                return param, int(raw)
    return None


def _replace_query_param(url: str, param: str, value: int) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query[param] = [str(value)]
    # Flatten while preserving other params' first values in stable order.
    flat = []
    for key, values in query.items():
        for v in values:
            flat.append((key, v))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(flat), parts.fragment))


def _to_pattern(url: str) -> str:
    normalized = _PAGE_PARAM_RE.sub(lambda m: f"{m.group(0)[0]}{m.group(1)}={{page}}", url)
    if normalized == url:
        normalized = _PAGE_PATH_RE.sub(lambda m: url[: m.start()] + "/page/{page}", url)
    for param in _OFFSET_PARAMS:
        if f"{param}=" in normalized.lower():
            return _to_offset_pattern(normalized, param)
    return normalized


def _to_offset_pattern(url: str, param: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    flat = []
    for key, values in query.items():
        if key == param:
            flat.append(f"{key}={{{param}}}")
        else:
            for v in values:
                flat.append(urlencode([(key, v)]))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(flat), parts.fragment))
