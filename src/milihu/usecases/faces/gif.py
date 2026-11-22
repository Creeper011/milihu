"""GIF rendering and probing contract for application services."""

from pathlib import Path
from typing import Protocol

from milihu.types.default.gif import GifPreset


class GifRenderer(Protocol):
    """Low-level media duration probing and GIF rendering backend contract."""

    async def probe_duration(self, input_path: Path) -> float | None: ...

    async def render_gif(
        self,
        input_path: Path,
        output_path: Path,
        preset: GifPreset,
    ) -> None: ...
