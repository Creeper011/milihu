"""GIF conversion service."""

import asyncio
import contextlib
import tempfile
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

from milihu.exceptions.payload import InputTooLargeError
from milihu.faces.togif import MediaSource
from milihu.services.core.ffmpeg import FfmpegRunner
from milihu.services.core.payload_fitter import PayloadFitter
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


class FfmpegGifConverter:
    """Converts images and videos into GIFs."""

    def __init__(
        self,
        runner: FfmpegRunner | None = None,
        payload_fitter: PayloadFitter | None = None,
        presets: Sequence[GifPreset] = DEFAULT_GIF_PRESETS,
        max_input_bytes: int | None = DEFAULT_MAX_GIF_INPUT_BYTES,
    ) -> None:
        self.runner = runner or FfmpegRunner()
        self.payload_fitter = payload_fitter or PayloadFitter()
        self.presets = presets
        self.max_input_bytes = max_input_bytes

    def is_supported(self, source: MediaSource) -> bool:
        """Return whether ``source`` has supported media metadata."""
        if source.content_type and source.content_type.startswith(MEDIA_PREFIXES):
            return True
        return Path(source.filename).suffix.lower() in MEDIA_EXTENSIONS

    @staticmethod
    def _build_vf(preset: GifPreset) -> str:
        # rgb555le matches palettegen's 15-bit buckets, avoiding slow per-bucket list 
        # scans.
        return (
            f"fps={preset.fps},"
            f"scale='min({preset.width},iw)':-1:flags=lanczos,"
            f"split[s0][s1];"
            f"[s0]format=rgb555le,palettegen=max_colors={preset.max_colors}:stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
        )

    async def _probe_duration(self, input_path: Path) -> float | None:
        ffprobe = str(Path(self.runner.executable).with_name("ffprobe"))
        if "/" not in self.runner.executable and "\\" not in self.runner.executable:
            ffprobe = "ffprobe"
        try:
            process = await asyncio.create_subprocess_exec(
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(input_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
        except OSError:
            return None
        if process.returncode != 0:
            return None
        try:
            duration = float(stdout.decode().strip())
        except ValueError:
            return None
        return duration if duration > 0 else None

    @staticmethod
    async def _render_within_budget(
        run_ffmpeg: Callable[[], Coroutine[Any, Any, None]],
        output_path: Path,
        max_bytes: int | None,
    ) -> bytes | None:
        output_path.unlink(missing_ok=True)
        task = asyncio.create_task(run_ffmpeg())
        try:
            if max_bytes is not None:
                while not task.done():
                    await asyncio.wait({task}, timeout=DEFAULT_GIF_SIZE_POLL_SECONDS)
                    if task.done():
                        break
                    try:
                        current_size = output_path.stat().st_size
                    except FileNotFoundError:
                        continue
                    if current_size > max_bytes:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                        return None

            await task
        except BaseException:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            raise

        return output_path.read_bytes()

    async def convert(
        self,
        source: MediaSource,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Validate, read, and convert ``source`` into GIF bytes within ``max_bytes``"""
        if self.max_input_bytes is not None and source.size > self.max_input_bytes:
            raise InputTooLargeError(source.size, self.max_input_bytes)
        data = await source.read()
        if self.max_input_bytes is not None and len(data) > self.max_input_bytes:
            raise InputTooLargeError(len(data), self.max_input_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / f"input{Path(source.filename).suffix}"
            output_path = tmp_dir / "output.gif"
            input_path.write_bytes(data)

            duration = (
                await self._probe_duration(input_path)
                if max_bytes is not None
                else None
            )
            effective_duration = (
                duration * (DEFAULT_FILE_SIZE_LIMIT_BYTES / max_bytes)
                if duration is not None and max_bytes is not None
                else None
            )

            async def render(preset: GifPreset) -> bytes | None:
                return await self._render_within_budget(
                    lambda: self.runner.run(
                        "-y",
                        "-i",
                        str(input_path),
                        "-vf",
                        self._build_vf(preset),
                        str(output_path),
                    ),
                    output_path,
                    max_bytes,
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
