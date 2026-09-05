"""Search storage abstraction (spec section 56): the API layer talks only
to `SearchRepository`. `PostgresSearchRepository` is the implementation --
full-text search against `documents.search_vector`, a Postgres stored
generated column (`to_tsvector('english', title || ' ' || current_content)`,
recomputed by the database itself whenever either input changes) with a
GIN index, per spec Phase Q. `index_document`/`update_document`/
`delete_document` are no-ops here for the same reason as before -- the
generated column keeps itself in sync, there's still nothing to push to a
separate index -- but the methods exist on the interface so an
`OpenSearchRepository`, which *would* need real indexing calls, can be
swapped in without touching callers.
"""

from __future__ import annotations

import abc

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.services.search.models import SearchFilters, SearchHit, SearchResults

_SNIPPET_CHARS = 240


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
            conditions.append(Document.search_vector.op("@@")(tsquery))
            rank = func.ts_rank(Document.search_vector, tsquery)
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
