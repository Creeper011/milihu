"""FFmpeg-backed caption renderer adapter."""

import tempfile
import textwrap
from pathlib import Path

from milihu.services.ffmpeg.runner import FfmpegRunner
from milihu.types.caption import CaptionPosition, CaptionStyle
from milihu.types.default.caption import (
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_STYLE,
    DEFAULT_CAPTION_WRAP_WIDTH,
)
from milihu.types.default.gif import GifPreset


class FfmpegCaptionRenderer:
    """Renders text captions onto images, GIFs, and videos via ``FfmpegRunner``."""

    def __init__(
        self,
        runner: FfmpegRunner | None = None,
        *,
        wrap_width: int = DEFAULT_CAPTION_WRAP_WIDTH,
    ) -> None:
        self.runner = runner or FfmpegRunner()
        self.wrap_width = wrap_width

    async def probe_duration(self, input_path: Path) -> float | None:
        """Return media duration in seconds via ``ffprobe``, or ``None`` if unknown."""
        return await self.runner.probe_duration(input_path)

    def _wrap_text(self, text: str) -> str:
        lines: list[str] = []
        for raw_line in text.strip().splitlines():
            stripped = raw_line.strip()
            if not stripped:
                lines.append("")
                continue
            lines.extend(
                textwrap.wrap(
                    stripped,
                    width=self.wrap_width,
                    break_long_words=True,
                    break_on_hyphens=True,
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        raw = str(path)
        return raw.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    @classmethod
    def _build_caption_filter(
        cls,
        text_path: Path,
        line_count: int,
        *,
        position: CaptionPosition,
        style: CaptionStyle,
    ) -> str:
        escaped_path = cls._escape_filter_path(text_path)
        font_size_w = r"max(16\,w/16)"
        if style == "bar":
            font_size_iw = r"max(16\,iw/16)"
            bar_h_pad = f"ceil({line_count}*{font_size_iw}*1.35+{font_size_iw}*0.9)"
            bar_h_text = f"ceil({line_count}*{font_size_w}*1.35+{font_size_w}*0.9)"
            if position == "top":
                pad_filter = f"pad=iw:ih+{bar_h_pad}:0:{bar_h_pad}:white"
                y_expr = f"({bar_h_text}-text_h)/2"
            else:
                pad_filter = f"pad=iw:ih+{bar_h_pad}:0:0:white"
                y_expr = f"h-{bar_h_text}+({bar_h_text}-text_h)/2"
            drawtext = (
                f"drawtext=textfile='{escaped_path}':expansion=none:"
                f"font='Sans Bold':fontcolor=black:"
                f"fontsize={font_size_w}:text_align=center:"
                f"x=(w-text_w)/2:y={y_expr}"
            )
            return f"{pad_filter},{drawtext}"

        y_expr = (
            r"max(12\,h*0.04)" if position == "top" else r"h-text_h-max(12\,h*0.04)"
        )
        return (
            f"drawtext=textfile='{escaped_path}':expansion=none:"
            f"font='Sans Bold':fontcolor=white:"
            f"borderw=3:bordercolor=black:"
            f"fontsize={font_size_w}:text_align=center:"
            f"x=(w-text_w)/2:y={y_expr}"
        )

    @classmethod
    def _build_vf(
        cls,
        text_path: Path,
        line_count: int,
        *,
        position: CaptionPosition,
        style: CaptionStyle,
        preset: GifPreset | None,
    ) -> str:
        caption_filter = cls._build_caption_filter(
            text_path,
            line_count,
            position=position,
            style=style,
        )
        if preset is None:
            return caption_filter
        return (
            f"fps={preset.fps},"
            f"scale='min({preset.width},iw)':-1:flags=lanczos,"
            f"{caption_filter},"
            f"split[s0][s1];"
            f"[s0]format=rgb555le,palettegen=max_colors={preset.max_colors}:stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
        )

    async def render_caption(
        self,
        input_path: Path,
        output_path: Path,
        text: str,
        *,
        position: CaptionPosition = DEFAULT_CAPTION_POSITION,
        style: CaptionStyle = DEFAULT_CAPTION_STYLE,
        preset: GifPreset | None = None,
    ) -> None:
        """Render ``text`` onto ``input_path`` and write to ``output_path``."""
        wrapped = self._wrap_text(text)
        line_count = max(1, len(wrapped.splitlines()))
        with tempfile.TemporaryDirectory() as tmp:
            text_path = Path(tmp) / "caption.txt"
            text_path.write_text(wrapped, encoding="utf-8")
            vf = self._build_vf(
                text_path,
                line_count,
                position=position,
                style=style,
                preset=preset,
            )
            args: list[str] = ["-y", "-i", str(input_path), "-vf", vf]
            if preset is None:
                args.extend(["-frames:v", "1"])
            args.append(str(output_path))
            await self.runner.run(*args)
