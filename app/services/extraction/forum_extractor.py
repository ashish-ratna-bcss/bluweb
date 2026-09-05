"""Forum/discussion thread extraction (spec Phase 6 forum capability).

Adapter architecture: `ForumAdapter` is the plugin seam (mirrors the
ExtractorPlugin can_handle/extract shape generic_extractor.py already
anticipates) so a platform-specific adapter (Discourse, phpBB,
vBulletin...) can be added later without touching this module or its
caller. Only `GenericForumAdapter` exists today -- no platform-specific
fixture was available to build or verify anything narrower, so a generic
schema.org + DOM-heuristic adapter is the one production path; adding a
platform adapter later is just appending to `ADAPTERS` below.

Detection hierarchy per spec, cheapest/most-reliable first:
1. schema.org DiscussionForumPosting/Comment JSON-LD (structured, exact).
2. DOM heuristics: common comment/post container class-name patterns,
   verified against a real Hacker News thread fixture (tests/fixtures/
   forum_thread_hn.html) since HN carries no schema.org markup for
   comments -- this path has to work on real markup, not just synthetic.

The Scrapling adaptive-DOM fallback (spec capability 5) lives one level up
in extraction_router.py, applied uniformly to whatever page type -- forum
included -- comes back empty from its dedicated extractor, rather than
duplicated inside every extractor module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

from app.services.extraction.article_extractor import ArticleDocument
from app.services.extraction.structured_data import StructuredData, _parse_datetime

MIN_POSTS = 1
MAX_POSTS_IN_METADATA = 200  # cap so a 1700-comment thread doesn't blow up the JSONB column

# author/timestamp/body class-name patterns, in priority order. HN's own
# "hnuser"/"age"/"commtext" classes are listed explicitly since the real
# fixture uses them; the rest are common across other forum software.
_POST_CONTAINER_SELECTORS = (".comtr", ".comment", ".post", ".message")
_AUTHOR_SELECTORS = (".hnuser", ".author", ".username", ".user", "[rel=author]")
_TIME_SELECTORS = (".age", "time", ".date", ".timestamp", ".posted")
_BODY_SELECTORS = (".commtext", ".comment-body", ".post-body", ".message-body", ".content")


@dataclass
class ForumPost:
    author: str | None
    posted_at: datetime | None
    body: str
    is_op: bool = False
    post_id: str | None = None  # stable identity across crawls when the source exposes one (DOM id / JSON-LD @id)


@dataclass
class ForumThreadDocument:
    title: str | None
    posts: list[ForumPost] = field(default_factory=list)

    @property
    def reply_count(self) -> int:
        return max(0, len(self.posts) - 1)


class ForumAdapter:
    """Plugin seam: can_handle decides applicability, extract does the work."""

    name = "generic"

    def can_handle(self, url: str, html: str) -> bool:
        raise NotImplementedError

    def extract(self, url: str, html: str, *, structured: StructuredData) -> ForumThreadDocument | None:
        raise NotImplementedError


class GenericForumAdapter(ForumAdapter):
    name = "forum_generic"

    def can_handle(self, url: str, html: str) -> bool:
        return True  # last-resort catch-all; only adapter registered today

    def extract(self, url: str, html: str, *, structured: StructuredData) -> ForumThreadDocument | None:
        posts = _from_structured_data(structured)
        if not posts:
            posts = _from_dom_heuristics(html)

        if len(posts) < MIN_POSTS:
            return None

        title = structured.headline or _title_from_html(html)
        return ForumThreadDocument(title=title, posts=posts)


ADAPTERS: list[ForumAdapter] = [GenericForumAdapter()]


def extract_forum(url: str, html: str, *, structured: StructuredData) -> ArticleDocument | None:
    """Same call shape as extract_article: returns None on total failure,
    otherwise an ArticleDocument so extraction_router.py doesn't need a
    third return shape. `body` is the OP + top replies flattened to text
    (for full-text search / diffing); the full per-post structure -- what
    the spec actually asks this phase to preserve -- lives in
    `raw_metadata["forum_posts"]`, which the existing `extracted_metadata`
    JSONB column already stores without any schema change."""
    thread = None
    for adapter in ADAPTERS:
        if adapter.can_handle(url, html):
            thread = adapter.extract(url, html, structured=structured)
            if thread is not None:
                break

    if thread is None or not thread.posts:
        return None

    body = "\n\n".join(f"{p.author or 'unknown'}: {p.body}" for p in thread.posts[:50])
    if len(body) < 20:
        return None

    posts_metadata = [
        {
            "post_id": p.post_id,
            "author": p.author,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "body": p.body,
            "is_op": p.is_op,
        }
        for p in thread.posts[:MAX_POSTS_IN_METADATA]
    ]

    return ArticleDocument(
        extractor="forum",
        headline=thread.title,
        body=body,
        author=thread.posts[0].author if thread.posts else None,
        publisher=None,
        published_at=thread.posts[0].posted_at if thread.posts else None,
        updated_at=None,
        section=None,
        canonical_url=structured.canonical_url or url,
        language=None,
        tags=[],
        images=[],
        confidence=0.6 if len(thread.posts) > 1 else 0.3,
        fields_detected=["body", "posts"] + (["headline"] if thread.title else []),
        raw_metadata={
            "forum_posts": posts_metadata,
            "reply_count": thread.reply_count,
            "truncated": len(thread.posts) > MAX_POSTS_IN_METADATA,
        },
    )


def _from_structured_data(structured: StructuredData) -> list[ForumPost]:
    posts: list[ForumPost] = []
    for block in structured.raw_json_ld:
        type_value = block.get("@type")
        if isinstance(type_value, list):
            type_value = type_value[0] if type_value else None
        if type_value not in ("DiscussionForumPosting", "Comment", "SocialMediaPosting"):
            continue
        posts.append(_post_from_json_ld(block, is_op=(type_value != "Comment")))
        for comment in block.get("comment", []) or []:
            if isinstance(comment, dict):
                posts.append(_post_from_json_ld(comment, is_op=False))
    return posts


def _post_from_json_ld(block: dict, *, is_op: bool) -> ForumPost:
    author = block.get("author")
    if isinstance(author, dict):
        author = author.get("name")
    text = block.get("text") or block.get("articleBody") or ""
    post_id = block.get("@id") or block.get("url") or block.get("identifier")
    return ForumPost(
        author=str(author) if author else None,
        posted_at=_parse_datetime(block.get("datePublished")),
        body=str(text).strip(),
        is_op=is_op,
        post_id=str(post_id) if post_id else None,
    )


def _from_dom_heuristics(html: str) -> list[ForumPost]:
    tree = LexborHTMLParser(html)
    posts: list[ForumPost] = []

    for container_sel in _POST_CONTAINER_SELECTORS:
        nodes = tree.css(container_sel)
        if not nodes:
            continue
        for node in nodes:
            body_node = _first_match(node, _BODY_SELECTORS)
            if body_node is None:
                continue
            body_text = body_node.text(deep=True, separator=" ").strip()
            if len(body_text) < 5:
                continue
            author_node = _first_match(node, _AUTHOR_SELECTORS)
            time_node = _first_match(node, _TIME_SELECTORS)
            posted_at = None
            if time_node is not None:
                raw_time = time_node.attributes.get("title") or time_node.attributes.get("datetime")
                posted_at = _parse_datetime(raw_time) if raw_time else None
            posts.append(ForumPost(
                author=author_node.text(strip=True) if author_node else None,
                posted_at=posted_at,
                body=body_text,
                is_op=False,
                post_id=node.attributes.get("id"),
            ))
        if posts:
            break  # first container pattern that actually matched something wins

    return posts


def _first_match(node, selectors: tuple[str, ...]):
    for sel in selectors:
        found = node.css_first(sel)
        if found is not None:
            return found
    return None


def _title_from_html(html: str) -> str | None:
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    return title_node.text(strip=True) if title_node else None
