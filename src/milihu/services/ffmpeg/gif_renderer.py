"""FFmpeg-backed GIF renderer adapter."""

from pathlib import Path

from milihu.services.ffmpeg.runner import FfmpegRunner
from milihu.types.default.gif import GifPreset


class FfmpegGifRenderer:
    """Renders images and videos to GIFs using an ``FfmpegRunner``."""

    def __init__(self, runner: FfmpegRunner | None = None) -> None:
        self.runner = runner or FfmpegRunner()

    async def probe_duration(self, input_path: Path) -> float | None:
        """Return media duration in seconds via ``ffprobe``, or ``None`` if unknown."""
        return await self.runner.probe_duration(input_path)

    @staticmethod
    def _build_gif_vf(preset: GifPreset) -> str:
        # rgb555le matches palettegen's 15-bit buckets, avoiding slow per-bucket scans.
        return (
            f"fps={preset.fps},"
            f"scale='min({preset.width},iw)':-1:flags=lanczos,"
            f"split[s0][s1];"
            f"[s0]format=rgb555le,palettegen=max_colors={preset.max_colors}:stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
        )

    async def render_gif(
        self,
        input_path: Path,
        output_path: Path,
        preset: GifPreset,
    ) -> None:
        """Render ``input_path`` to a GIF at ``output_path`` using ``preset``."""
        await self.runner.run(
            "-y",
            "-i",
            str(input_path),
            "-vf",
            self._build_gif_vf(preset),
            str(output_path),
        )
