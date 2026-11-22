"""Media backend contract for application services."""

from typing import Protocol, Unpack

from milihu.types.media import MediaResolveOptions, MediaResult


class MediaBackend(Protocol):
    """Resolve media URLs through the configured backend."""

    async def resolve(
        self, url: str, **options: Unpack[MediaResolveOptions]
    ) -> MediaResult: ...
