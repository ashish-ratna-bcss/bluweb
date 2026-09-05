from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_security_service
from app.core.config import Settings, get_settings
from app.core.errors import APIError
from app.core.metrics import preflight_count
from app.db.models.preflight import PreflightReportRow
from app.db.repositories.preflight_repository import PreflightRepository
from app.db.session import get_db
from app.schemas.preflight import PreflightReportResponse, PreflightRequest
from app.services.events import Event, default_bus
from app.services.preflight.service import PreflightService
from app.services.security.url_security import URLSecurityService

router = APIRouter(prefix="/preflight", tags=["preflight"])


@router.post(
    "",
    response_model=PreflightReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a pre-flight capability assessment against a URL",
)
async def create_preflight(
    body: PreflightRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    security: URLSecurityService = Depends(get_security_service),
) -> PreflightReportResponse:
    service = PreflightService(settings, security)
    report = await service.run(body.url)
    preflight_count.labels(status=report.status).inc()
    await default_bus.publish(Event("PREFLIGHT_COMPLETED", {
        "url": report.url,
        "status": report.status,
        "score": report.capability.score if report.capability else None,
        "recommended_fetch": report.fetch.recommended.value if report.fetch else None,
    }))

    repository = PreflightRepository(db)
    row = await repository.save(report, settings)
    return _row_to_response(row)


@router.get(
    "/{preflight_id}",
    response_model=PreflightReportResponse,
    summary="Retrieve a previously run pre-flight report",
)
async def get_preflight(
    preflight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PreflightReportResponse:
    repository = PreflightRepository(db)
    row = await repository.get(preflight_id)
    if row is None:
        raise APIError(
            code="PREFLIGHT_NOT_FOUND",
            message=f"No pre-flight report found for id {preflight_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _row_to_response(row)


def _row_to_response(row: PreflightReportRow) -> PreflightReportResponse:
    data = dict(row.report_json)
    data["preflight_id"] = row.id
    data["created_at"] = row.created_at
    data["expires_at"] = row.expires_at
    return PreflightReportResponse.model_validate(data)
