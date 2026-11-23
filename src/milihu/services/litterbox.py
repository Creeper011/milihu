"""Litterbox HTTP adapter for temporary file uploads."""

from aiohttp import ClientError, ClientSession, ClientTimeout, FormData

from milihu.exceptions.download import DownloadError
from milihu.types.default.upload import (
    DEFAULT_UPLOAD_LIFETIME,
    LITTERBOX_API_URL,
)
from milihu.types.upload import UploadLifetime


class Litterbox:
    """Upload temporary files to Litterbox with a configured retention lifetime."""

    def __init__(
        self,
        api_url: str = LITTERBOX_API_URL,
        *,
        lifetime: UploadLifetime = DEFAULT_UPLOAD_LIFETIME,
    ) -> None:
        self.api_url = api_url
        self.lifetime = lifetime

    async def upload(
        self,
        data: bytes,
        filename: str,
        *,
        lifetime: UploadLifetime | None = None,
    ) -> str:
        """Upload ``data`` as ``filename`` and return the hosted file URL."""
        selected_lifetime = self.lifetime if lifetime is None else lifetime
        form = FormData()
        form.add_field("reqtype", "fileupload")
        form.add_field("time", str(selected_lifetime))
        form.add_field(
            "fileToUpload",
            data,
            filename=filename,
            content_type="application/octet-stream",
        )
        try:
            async with ClientSession(timeout=ClientTimeout(total=300)) as session:
                async with session.post(self.api_url, data=form) as response:
                    response.raise_for_status()
                    url = (await response.text()).strip()
        except (ClientError, TimeoutError) as exc:
            raise DownloadError(f"Temporary upload failed: {exc}") from exc
        if not url.startswith(("http://", "https://")):
            raise DownloadError(f"Temporary upload failed: {url or 'empty response'}")
        return url
