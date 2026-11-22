"""Service protocols for GIF conversion."""

from typing import Protocol

from milihu.faces.media import MediaSource

__all__ = ["GifConverter", "MediaSource"]


class GifConverter(Protocol):
    """Protocol for converting media files into GIFs."""

    def is_supported(self, source: MediaSource) -> bool:
        """Return whether ``source`` has supported media metadata."""
        ...

    async def convert(
        self,
        source: MediaSource | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Validate, read, and convert ``source`` into GIF bytes within budget."""
        ...
