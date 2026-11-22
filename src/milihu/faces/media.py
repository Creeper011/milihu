"""Shared readable media input protocol for application services."""

from typing import Protocol

from milihu.types.common import NonEmptyStr


class MediaSource(Protocol):
    """Protocol for readable media inputs with metadata."""

    @property
    def filename(self) -> NonEmptyStr: ...

    @property
    def content_type(self) -> str | None: ...

    @property
    def size(self) -> int: ...

    async def read(self) -> bytes:
        """Read and return raw media bytes."""
        ...
