"""`preflight` kind.

See app/db/models/unified.py for the shared column set and the STI
rationale. The DDL only gives this kind three dedicated columns
(`preflight_status`, `preflight_payload`, `preflight_expires_at`) --
`capability_score`, `confidence`, `crawler_version`, `extractor_version`,
`duration_ms`, `error` have no dedicated columns, so they're folded into
`preflight_payload` (which already holds the full nested report blob per
the DDL's own comment: "capability, discovery, fetch, content, extraction,
sample, limitations, recommendations, duration_ms, ..."). Nothing in the
app actually reads these six back as separate row attributes (confirmed by
reading every caller of `PreflightRepository`) -- `app/api/v1/preflight.py`
only ever reads `row.report_json`/`row.id`/`row.created_at`/
`row.expires_at` -- so this is a safe, low-risk fold.
"""

from app.db.models.unified import WebIntelUnified


class PreflightReportRow(WebIntelUnified):
    __mapper_args__ = {"polymorphic_identity": "preflight"}

    @property
    def status(self) -> str | None:
        return self.preflight_status

    @status.setter
    def status(self, value: str | None) -> None:
        self.preflight_status = value

    @property
    def report_json(self) -> dict:
        return self.preflight_payload

    @report_json.setter
    def report_json(self, value: dict) -> None:
        self.preflight_payload = value

    @property
    def expires_at(self):
        return self.preflight_expires_at

    @expires_at.setter
    def expires_at(self, value) -> None:
        self.preflight_expires_at = value

    @property
    def capability_score(self) -> int | None:
        return (self.preflight_payload or {}).get("capability", {}).get("score")

    @capability_score.setter
    def capability_score(self, value: int | None) -> None:
        payload = dict(self.preflight_payload or {})
        payload["capability"] = {**payload.get("capability", {}), "score": value}
        self.preflight_payload = payload

    @property
    def confidence(self) -> str | None:
        return (self.preflight_payload or {}).get("capability", {}).get("confidence")

    @confidence.setter
    def confidence(self, value: str | None) -> None:
        payload = dict(self.preflight_payload or {})
        payload["capability"] = {**payload.get("capability", {}), "confidence": value}
        self.preflight_payload = payload

    @property
    def crawler_version(self) -> str | None:
        return (self.preflight_payload or {}).get("crawler_version")

    @crawler_version.setter
    def crawler_version(self, value: str | None) -> None:
        self.preflight_payload = {**(self.preflight_payload or {}), "crawler_version": value}

    @property
    def extractor_version(self) -> str | None:
        return (self.preflight_payload or {}).get("extractor_version")

    @extractor_version.setter
    def extractor_version(self, value: str | None) -> None:
        self.preflight_payload = {**(self.preflight_payload or {}), "extractor_version": value}

    @property
    def duration_ms(self) -> float | None:
        return (self.preflight_payload or {}).get("duration_ms")

    @duration_ms.setter
    def duration_ms(self, value: float | None) -> None:
        self.preflight_payload = {**(self.preflight_payload or {}), "duration_ms": value}

    @property
    def error(self) -> str | None:
        return (self.preflight_payload or {}).get("error")

    @error.setter
    def error(self, value: str | None) -> None:
        self.preflight_payload = {**(self.preflight_payload or {}), "error": value}
