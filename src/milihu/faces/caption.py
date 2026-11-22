"""Service protocols for adding text captions to media."""

from typing import Protocol

from milihu.faces.media import MediaSource
from milihu.types.caption import CaptionOutput, CaptionPosition, CaptionStyle
from milihu.types.default.caption import (
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_STYLE,
)

__all__ = ["MediaCaptioner", "MediaSource"]


class MediaCaptioner(Protocol):
    """Protocol for adding text captions to images, GIFs, and videos."""

    def is_supported(self, source: MediaSource) -> bool:
        """Return whether ``source`` has supported media metadata."""
        ...

    async def caption(
        self,
        source: MediaSource | str,
        text: str,
        *,
        position: CaptionPosition = DEFAULT_CAPTION_POSITION,
        style: CaptionStyle = DEFAULT_CAPTION_STYLE,
        max_bytes: int | None = None,
    ) -> CaptionOutput:
        """Validate, read, and render a caption onto ``source`` within budget."""
        ...
