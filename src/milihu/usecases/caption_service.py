"""Caption rendering orchestration service."""

import tempfile
from collections.abc import Sequence
from pathlib import Path

from milihu.exceptions.payload import InputTooLargeError, PayloadTooLargeError
from milihu.faces.media import MediaSource
from milihu.types.caption import CaptionOutput, CaptionPosition, CaptionStyle
from milihu.types.default.caption import (
    DEFAULT_CAPTION_MAX_TEXT_LENGTH,
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_PRESETS,
    DEFAULT_CAPTION_SIZE_POLL_SECONDS,
    DEFAULT_CAPTION_STYLE,
    DEFAULT_MAX_CAPTION_INPUT_BYTES,
)
from milihu.types.default.gif import (
    DEFAULT_LONG_MEDIA_SECONDS,
    DEFAULT_SHORT_MEDIA_SECONDS,
    GifPreset,
)
from milihu.types.default.payload import DEFAULT_FILE_SIZE_LIMIT_BYTES
from milihu.types.media import (
    CAPTION_MEDIA_EXTENSIONS,
    MEDIA_PREFIXES,
    STATIC_IMAGE_EXTENSIONS,
)
from milihu.usecases.faces.caption import CaptionRenderer
from milihu.usecases.faces.remote_media import RemoteMediaFetcher
from milihu.usecases.payload_fitter import PayloadFitter


class CaptionService:
    """Orchestrates validation and caption rendering for images, GIFs, and videos."""

    def __init__(
        self,
        renderer: CaptionRenderer,
        payload_fitter: PayloadFitter | None = None,
        presets: Sequence[GifPreset] = DEFAULT_CAPTION_PRESETS,
        max_input_bytes: int | None = DEFAULT_MAX_CAPTION_INPUT_BYTES,
        max_text_length: int = DEFAULT_CAPTION_MAX_TEXT_LENGTH,
        remote_fetcher: RemoteMediaFetcher | None = None,
    ) -> None:
        self.renderer = renderer
        self.payload_fitter = payload_fitter or PayloadFitter()
        self.presets = presets
        self.max_input_bytes = max_input_bytes
        self.max_text_length = max_text_length
        self.remote_fetcher = remote_fetcher

    def is_supported(self, source: MediaSource) -> bool:
        """Return whether ``source`` has supported image, GIF, or video metadata."""
        if source.content_type and source.content_type.startswith(MEDIA_PREFIXES):
            return True
        return Path(source.filename).suffix.lower() in CAPTION_MEDIA_EXTENSIONS

    @staticmethod
    def _is_static_image(source: MediaSource) -> bool:
        if source.content_type:
            mime = source.content_type.split(";", 1)[0].strip().lower()
            if mime == "image/gif" or mime.startswith("video/"):
                return False
            if mime.startswith("image/"):
                return True
        return Path(source.filename).suffix.lower() in STATIC_IMAGE_EXTENSIONS

    def _validate_text(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Caption text cannot be empty.")
        if len(cleaned) > self.max_text_length:
            raise ValueError(
                f"Caption text must be at most {self.max_text_length} characters."
            )
        return cleaned

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
        cleaned_text = self._validate_text(text)
        media = await self._resolve_source(source)

        if self.max_input_bytes is not None and media.size > self.max_input_bytes:
            raise InputTooLargeError(media.size, self.max_input_bytes)
        data = await media.read()
        if self.max_input_bytes is not None and len(data) > self.max_input_bytes:
            raise InputTooLargeError(len(data), self.max_input_bytes)

        stem = Path(media.filename).stem or "media"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / f"input{Path(media.filename).suffix}"
            input_path.write_bytes(data)

            if self._is_static_image(media):
                output_path = tmp_dir / "output.png"
                rendered = await self.payload_fitter.render_within_budget(
                    lambda: self.renderer.render_caption(
                        input_path,
                        output_path,
                        cleaned_text,
                        position=position,
                        style=style,
                        preset=None,
                    ),
                    output_path,
                    max_bytes,
                    poll_seconds=DEFAULT_CAPTION_SIZE_POLL_SECONDS,
                )
                if rendered is None or (
                    max_bytes is not None and len(rendered) > max_bytes
                ):
                    raise PayloadTooLargeError(
                        max_bytes,
                        size=len(rendered) if rendered is not None else None,
                    )
                return CaptionOutput(
                    filename=f"{stem}_captioned.png",
                    data=rendered,
                    extension=".png",
                )

            output_path = tmp_dir / "output.gif"
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
                    lambda: self.renderer.render_caption(
                        input_path,
                        output_path,
                        cleaned_text,
                        position=position,
                        style=style,
                        preset=preset,
                    ),
                    output_path,
                    max_bytes,
                    poll_seconds=DEFAULT_CAPTION_SIZE_POLL_SECONDS,
                )

            gif_bytes = await self.payload_fitter.fit(
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
            return CaptionOutput(
                filename=f"{stem}_captioned.gif",
                data=gif_bytes,
                extension=".gif",
            )
