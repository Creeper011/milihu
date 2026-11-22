"""Remote media link resolution and fetching contract for application services."""

from typing import Protocol

from milihu.faces.media import MediaSource


class RemoteMediaFetcher(Protocol):
    """Resolve and download remote image, GIF, or video URLs into a ``MediaSource``."""

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
    ) -> MediaSource: ...
