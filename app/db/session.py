from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

# Explicit pool_size/max_overflow (Universal Adaptive Web Intelligence item
# 2): the unconfigured SQLAlchemy default (5 + 10 overflow) was an implicit
# ceiling nobody chose. Paired with crawl_engine.py no longer holding a
# session open across fetch/Playwright I/O, this stops concurrent crawl
# jobs from exhausting the pool.
engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
