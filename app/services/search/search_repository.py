"""Search storage abstraction (spec section 56): the API layer talks only
to `SearchRepository`. `PostgresSearchRepository` is the implementation --
full-text search against `webintel_unified`. Unlike the old `documents`
table, there's no stored generated `search_vector` column here -- only a
GIN index over the raw expression
(`ix_webintel_document_fts`: `to_tsvector('english', coalesce(title, '') ||
' ' || coalesce(content, ''))`, `WHERE record_kind='document'`) -- so the
query builds that same expression inline via `_document_tsvector()` on
every call. It must match the index expression character-for-character
(including the `content` null-coalesce, which the old stored column didn't
need since it was itself already coalesced at write time) or Postgres won't
recognize the query as index-eligible and will fall back to a sequential
scan -- correctness-preserving but slow, not silently wrong.
`index_document`/`update_document`/`delete_document` are no-ops here for
the same reason as before -- nothing to push to a separate index -- but
the methods exist on the interface so an `OpenSearchRepository`, which
*would* need real indexing calls, can be swapped in without touching
callers.
"""

from __future__ import annotations

import abc

from sqlalchemy import func, literal, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.services.search.models import SearchFilters, SearchHit, SearchResults

_SNIPPET_CHARS = 240


def _document_tsvector():
    # Must match docs/unified_schema.sql ix_webintel_document_fts *exactly*
    # (literal '' and ' ', not bind parameters), or Postgres will not use
    # the expression GIN index.
    empty = literal_column("''")
    space = literal_column("' '")
    return func.to_tsvector(
        literal_column("'english'"),
        func.coalesce(Document.title, empty) + space + func.coalesce(Document.content, empty),
    )


class SearchRepository(abc.ABC):
    @abc.abstractmethod
    async def index_document(self, document: Document) -> None: ...

    @abc.abstractmethod
    async def update_document(self, document: Document) -> None: ...

    @abc.abstractmethod
    async def delete_document(self, document_id: str) -> None: ...

    @abc.abstractmethod
    async def bulk_index(self, documents: list[Document]) -> None: ...

    @abc.abstractmethod
    async def search(self, filters: SearchFilters) -> SearchResults: ...


class PostgresSearchRepository(SearchRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def index_document(self, document: Document) -> None:
        return None  # already durable in `documents`; nothing to sync

    async def update_document(self, document: Document) -> None:
        return None

    async def delete_document(self, document_id: str) -> None:
        return None

    async def bulk_index(self, documents: list[Document]) -> None:
        return None

    async def search(self, filters: SearchFilters) -> SearchResults:
        conditions = []
        if filters.domain:
            conditions.append(Document.domain == filters.domain)
        if filters.source_id:
            conditions.append(Document.source_id == filters.source_id)
        if filters.language:
            conditions.append(Document.language == filters.language)
        if filters.date_from:
            conditions.append(Document.collected_at >= filters.date_from)
        if filters.date_to:
            conditions.append(Document.collected_at <= filters.date_to)

        if filters.query:
            tsquery = func.plainto_tsquery("english", filters.query)
            tsvector = _document_tsvector()
            conditions.append(tsvector.op("@@")(tsquery))
            rank = func.ts_rank(tsvector, tsquery)
            row_stmt = select(Document, rank).order_by(rank.desc())
        else:
            row_stmt = select(Document, literal(0.0)).order_by(Document.collected_at.desc())

        count_stmt = select(func.count()).select_from(Document)
        for condition in conditions:
            row_stmt = row_stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        total = (await self._session.execute(count_stmt)).scalar_one()

        row_stmt = row_stmt.limit(filters.limit).offset(filters.offset)
        rows = (await self._session.execute(row_stmt)).all()

        hits = [
            SearchHit(
                document_id=str(document.id),
                title=document.title,
                url=document.url,
                domain=document.domain,
                snippet=_snippet(document.current_content, filters.query),
                published_at=document.published_at,
                collected_at=document.collected_at,
                version=document.current_version,
                content_hash=document.content_hash,
                relevance=float(relevance or 0.0),
            )
            for document, relevance in rows
        ]
        return SearchResults(hits=hits, total=total)


def _snippet(content: str, query: str | None) -> str:
    if not content:
        return ""
    if not query:
        return content[:_SNIPPET_CHARS]

    lowered = content.lower()
    first_term = query.lower().split()[0] if query.split() else ""
    idx = lowered.find(first_term) if first_term else -1
    if idx == -1:
        return content[:_SNIPPET_CHARS]

    start = max(0, idx - 80)
    end = min(len(content), start + _SNIPPET_CHARS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"
