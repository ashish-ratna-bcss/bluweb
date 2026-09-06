"""Raw artifact storage on MinIO/S3-compatible object storage.

The `minio` SDK is synchronous; every call here is pushed to a thread via
`asyncio.to_thread` so it doesn't block the event loop. Metadata about each
stored object (key, size, sha256, content-type) belongs in Postgres
(`raw_artifacts` table) -- this module only knows how to move bytes.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings


@dataclass(frozen=True)
class StoredArtifact:
    storage_key: str
    size: int
    sha256: str
    content_type: str


class ArtifactStore:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket

    async def ensure_bucket(self) -> None:
        import asyncio

        def _ensure() -> None:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)

        await asyncio.to_thread(_ensure)

    @staticmethod
    def build_key(*, source_id: str, crawl_id: str, page_id: str, extension: str) -> str:
        return f"raw/{source_id}/{crawl_id}/{page_id}.{extension.lstrip('.')}"

    async def put_bytes(self, storage_key: str, data: bytes, content_type: str) -> StoredArtifact:
        import asyncio

        sha256 = hashlib.sha256(data).hexdigest()

        def _put() -> None:
            self._client.put_object(
                self._bucket,
                storage_key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

        try:
            await asyncio.to_thread(_put)
        except S3Error as exc:
            raise ArtifactStoreError(f"failed to store artifact {storage_key}: {exc}") from exc

        return StoredArtifact(storage_key=storage_key, size=len(data), sha256=sha256, content_type=content_type)

    async def get_bytes(self, storage_key: str) -> bytes:
        import asyncio

        def _get() -> bytes:
            response = self._client.get_object(self._bucket, storage_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(_get)
        except S3Error as exc:
            raise ArtifactStoreError(f"failed to read artifact {storage_key}: {exc}") from exc


class ArtifactStoreError(Exception):
    pass
