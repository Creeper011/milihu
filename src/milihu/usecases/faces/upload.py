"""Temporary file upload contract for application services."""

from typing import Protocol

from milihu.types.upload import UploadLifetime


class FileUploader(Protocol):
    """Upload temporary files and return their hosted URL."""

    async def upload(
        self,
        data: bytes,
        filename: str,
        *,
        lifetime: UploadLifetime | None = None,
    ) -> str: ...
