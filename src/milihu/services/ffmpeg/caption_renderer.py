"""FFmpeg-backed caption renderer adapter."""

import math
import tempfile
from pathlib import Path

from milihu.services.ffmpeg.runner import FfmpegRunner
from milihu.types.caption import CaptionPosition, CaptionStyle
from milihu.types.default.caption import (
    CAPTION_EXTRA_WIDE_CHARS,
    CAPTION_FONT_DIVISOR,
    CAPTION_NARROW_CHARS,
    CAPTION_THIN_CHARS,
    CAPTION_WIDE_LOWER_CHARS,
    CAPTION_WIDTH_RATIO,
    DEFAULT_CAPTION_MAX_EM,
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_STYLE,
    DEFAULT_CAPTION_WRAP_WIDTH,
    MIN_CAPTION_FONT_SIZE,
)
from milihu.types.default.gif import GifPreset


def _char_em_width(ch: str) -> float:
    if ch in CAPTION_NARROW_CHARS:
        return 0.35
    if ch in CAPTION_THIN_CHARS:
        return 0.45
    if ch in CAPTION_WIDE_LOWER_CHARS:
        return 1.0
    if ch in CAPTION_EXTRA_WIDE_CHARS:
        return 1.05
    if ch.isupper():
        return 0.80
    if ord(ch) > 0x024F:
        return 1.15
    return 0.65


def _line_em_width(line: str) -> float:
    return sum(_char_em_width(ch) for ch in line)


def _split_long_word(word: str, max_chars: int, max_em: float) -> list[str]:
    chunks: list[str] = []
    chunk: list[str] = []
    chunk_em = 0.0
    for ch in word:
        ch_em = _char_em_width(ch)
        if chunk and (len(chunk) + 1 > max_chars or chunk_em + ch_em > max_em):
            chunks.append("".join(chunk))
            chunk = [ch]
            chunk_em = ch_em
        else:
            chunk.append(ch)
            chunk_em += ch_em
    if chunk:
        chunks.append("".join(chunk))
    return chunks


def _wrap_paragraph(paragraph: str, max_chars: int, max_em: float) -> list[str]:
    words = paragraph.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    current_em = 0.0
    space_em = _char_em_width(" ")

    for word in words:
        word_em = _line_em_width(word)
        if current:
            if (
                len(current) + 1 + len(word) <= max_chars
                and current_em + space_em + word_em <= max_em
            ):
                current = f"{current} {word}"
                current_em += space_em + word_em
                continue
            lines.append(current)
            current = ""
            current_em = 0.0

        if len(word) <= max_chars and word_em <= max_em:
            current = word
            current_em = word_em
        else:
            chunks = _split_long_word(word, max_chars, max_em)
            lines.extend(chunks[:-1])
            current = chunks[-1]
            current_em = _line_em_width(current)

    if current:
        lines.append(current)
    return lines


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

    def _wrap_text(self, text: str, *, max_em: float = DEFAULT_CAPTION_MAX_EM) -> str:
        lines: list[str] = []
        for raw_line in text.strip().splitlines():
            stripped = raw_line.strip()
            if not stripped:
                lines.append("")
                continue
            lines.extend(_wrap_paragraph(stripped, self.wrap_width, max_em))
        return "\n".join(lines)

    def _resolve_layout(
        self, text: str, target_width: int | None
    ) -> tuple[str, float | None]:
        if target_width is None:
            return self._wrap_text(text, max_em=DEFAULT_CAPTION_MAX_EM), None

        font_size = max(MIN_CAPTION_FONT_SIZE, target_width / CAPTION_FONT_DIVISOR)
        max_width_px = target_width * CAPTION_WIDTH_RATIO
        max_em = max_width_px / font_size
        wrapped = self._wrap_text(text, max_em=max_em)
        widest_em = max(
            (_line_em_width(line) for line in wrapped.splitlines()),
            default=0.0,
        )
        if widest_em > 0 and widest_em * font_size > max_width_px:
            font_size = max(1.0, max_width_px / widest_em)
        return wrapped, font_size

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
        font_size: float | None = None,
    ) -> str:
        escaped_path = cls._escape_filter_path(text_path)
        if font_size is not None:
            font_size_w = f"{font_size:.2f}".rstrip("0").rstrip(".")
        else:
            font_size_w = rf"max(10\,w/{CAPTION_FONT_DIVISOR})"

        if style == "bar":
            if font_size is not None:
                bar_h = math.ceil(line_count * font_size * 1.35 + font_size * 0.9)
                bar_h_pad = str(bar_h)
                bar_h_text = str(bar_h)
            else:
                font_size_iw = rf"max(10\,iw/{CAPTION_FONT_DIVISOR})"
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
        font_size: float | None = None,
    ) -> str:
        caption_filter = cls._build_caption_filter(
            text_path,
            line_count,
            position=position,
            style=style,
            font_size=font_size,
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
        source_width = await self.runner.probe_width(input_path)
        if preset is not None and source_width is not None:
            target_width: int | None = min(preset.width, source_width)
        elif source_width is not None:
            target_width = source_width
        elif preset is not None:
            target_width = preset.width
        else:
            target_width = None

        wrapped, font_size = self._resolve_layout(text, target_width)
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
                font_size=font_size,
            )
            args: list[str] = ["-y", "-i", str(input_path), "-vf", vf]
            if preset is None:
                args.extend(["-frames:v", "1"])
            args.append(str(output_path))
            await self.runner.run(*args)
