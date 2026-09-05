from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.preflight import PreflightReportRow
from app.services.normalization.url_normalizer import extract_domain, normalize_url
from app.services.preflight.models import PreflightReport


class PreflightRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, report: PreflightReport, settings: Settings) -> PreflightReportRow:
        now = datetime.now(timezone.utc)
        row = PreflightReportRow(
            id=uuid.uuid4(),
            url=report.url,
            normalized_url=normalize_url(report.url),
            domain=extract_domain(report.url),
            status=report.status,
            capability_score=report.capability.score,
            confidence=report.capability.confidence.value,
            report_json=_report_to_json(report),
            duration_ms=report.duration_ms,
            error=report.error,
            created_at=now,
            expires_at=now + timedelta(hours=settings.preflight_report_ttl_hours),
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get(self, preflight_id: uuid.UUID) -> PreflightReportRow | None:
        result = await self._session.execute(
            select(PreflightReportRow).where(PreflightReportRow.id == preflight_id)
        )
        return result.scalar_one_or_none()


def _report_to_json(report: PreflightReport) -> dict:
    data = asdict(report)
    data["capability"]["confidence"] = report.capability.confidence.value
    data["fetch"]["recommended"] = report.fetch.recommended.value
    return data
