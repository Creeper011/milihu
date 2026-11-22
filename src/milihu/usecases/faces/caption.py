"""Caption rendering and probing contract for application services."""

from pathlib import Path
from typing import Protocol

from milihu.types.caption import CaptionPosition, CaptionStyle
from milihu.types.default.caption import (
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_STYLE,
)
from milihu.types.default.gif import GifPreset


class CaptionRenderer(Protocol):
    """Low-level media duration probing and caption rendering backend contract."""

    async def probe_duration(self, input_path: Path) -> float | None: ...

    async def render_caption(
        self,
        input_path: Path,
        output_path: Path,
        text: str,
        *,
        position: CaptionPosition = DEFAULT_CAPTION_POSITION,
        style: CaptionStyle = DEFAULT_CAPTION_STYLE,
        preset: GifPreset | None = None,
    ) -> None: ...
