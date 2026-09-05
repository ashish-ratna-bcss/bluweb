import pytest

from app.core.config import Settings
from app.services.security.url_security import URLSecurityService


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="sqlite+aiosqlite:///:memory:")


@pytest.fixture
def security(settings: Settings) -> URLSecurityService:
    return URLSecurityService(settings)
