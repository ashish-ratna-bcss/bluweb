from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.security.url_security import URLSecurityService


def get_security_service(settings: Settings = Depends(get_settings)) -> URLSecurityService:
    return URLSecurityService(settings)
