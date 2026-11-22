"""Service protocols for GIF conversion."""

from typing import Protocol


class MediaSource(Protocol):
    """Protocol for readable media inputs with metadata."""

    filename: str
    content_type: str | None
    size: int

    async def read(self) -> bytes:
        """Read and return raw media bytes."""
        ...


class GifConverter(Protocol):
    """Protocol for converting media files into GIFs."""

    def is_supported(self, source: MediaSource) -> bool:
        """Return whether ``source`` has supported media metadata."""
        ...

    async def convert(
        self,
        source: MediaSource,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Validate, read, and convert ``source`` into GIF bytes within ``max_bytes``."""
        ...
