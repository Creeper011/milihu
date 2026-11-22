"""GIF conversion orchestration service."""

import tempfile
from collections.abc import Sequence
from pathlib import Path

from milihu.exceptions.payload import InputTooLargeError
from milihu.faces.media import MediaSource
from milihu.types.default.gif import (
    DEFAULT_GIF_PRESETS,
    DEFAULT_GIF_SIZE_POLL_SECONDS,
    DEFAULT_LONG_MEDIA_SECONDS,
    DEFAULT_MAX_GIF_INPUT_BYTES,
    DEFAULT_SHORT_MEDIA_SECONDS,
    GifPreset,
)
from milihu.types.default.payload import DEFAULT_FILE_SIZE_LIMIT_BYTES
from milihu.types.media import MEDIA_EXTENSIONS, MEDIA_PREFIXES
from milihu.usecases.faces.gif import GifRenderer
from milihu.usecases.faces.remote_media import RemoteMediaFetcher
from milihu.usecases.payload_fitter import PayloadFitter


class GifConverterService:
    """Orchestrates validation and progressive fitting of images and videos to GIF."""

    def __init__(
        self,
        renderer: GifRenderer,
        payload_fitter: PayloadFitter | None = None,
        presets: Sequence[GifPreset] = DEFAULT_GIF_PRESETS,
        max_input_bytes: int | None = DEFAULT_MAX_GIF_INPUT_BYTES,
        remote_fetcher: RemoteMediaFetcher | None = None,
    ) -> None:
        self.renderer = renderer
        self.payload_fitter = payload_fitter or PayloadFitter()
        self.presets = presets
        self.max_input_bytes = max_input_bytes
        self.remote_fetcher = remote_fetcher

    def is_supported(self, source: MediaSource) -> bool:
        """Return whether ``source`` has supported media metadata."""
        if source.content_type and source.content_type.startswith(MEDIA_PREFIXES):
            return True
        return Path(source.filename).suffix.lower() in MEDIA_EXTENSIONS

    async def _resolve_source(self, source: MediaSource | str) -> MediaSource:
        if not isinstance(source, str):
            return source
        if self.remote_fetcher is None:
            raise ValueError("Remote media links are not configured.")
        resolved = await self.remote_fetcher.fetch(
            source,
            max_bytes=self.max_input_bytes,
        )
        if not self.is_supported(resolved):
            raise ValueError(
                "That link does not point to a supported image, GIF, or video."
            )
        return resolved

    async def convert(
        self,
        source: MediaSource | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Validate, read, and convert ``source`` into GIF bytes within budget."""
        media = await self._resolve_source(source)
        if self.max_input_bytes is not None and media.size > self.max_input_bytes:
            raise InputTooLargeError(media.size, self.max_input_bytes)
        data = await media.read()
        if self.max_input_bytes is not None and len(data) > self.max_input_bytes:
            raise InputTooLargeError(len(data), self.max_input_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / f"input{Path(media.filename).suffix}"
            output_path = tmp_dir / "output.gif"
            input_path.write_bytes(data)

            duration = (
                await self.renderer.probe_duration(input_path)
                if max_bytes is not None
                else None
            )
            effective_duration = (
                duration * (DEFAULT_FILE_SIZE_LIMIT_BYTES / max_bytes)
                if duration is not None and max_bytes is not None
                else None
            )

            async def render(preset: GifPreset) -> bytes | None:
                return await self.payload_fitter.render_within_budget(
                    lambda: self.renderer.render_gif(input_path, output_path, preset),
                    output_path,
                    max_bytes,
                    poll_seconds=DEFAULT_GIF_SIZE_POLL_SECONDS,
                )

            return await self.payload_fitter.fit(
                self.presets,
                render,
                max_bytes,
                prefer_first=(
                    effective_duration is None
                    or effective_duration <= DEFAULT_SHORT_MEDIA_SECONDS
                ),
                check_last_first=(
                    effective_duration is not None
                    and effective_duration > DEFAULT_LONG_MEDIA_SECONDS
                ),
            )
