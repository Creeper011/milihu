"""Service protocol for media downloads."""

from typing import Protocol

from milihu.types.download.output import DownloadOutput
from milihu.types.download.request import DownloadRequest


class MediaDownloader(Protocol):
    """Protocol consumed by Discord cogs to download media from a URL."""

    async def download(
        self,
        url: DownloadRequest | str,
        *,
        max_bytes: int,
    ) -> DownloadOutput: ...
