import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import override
from unittest.mock import patch

from milihu.exceptions.payload import PayloadTooLargeError
from milihu.services.core.ffmpeg import FfmpegRunner
from milihu.services.togif_service import FfmpegGifConverter
from milihu.types.default.gif import DEFAULT_GIF_PRESETS, GifPreset
from milihu.types.default.payload import BYTES_PER_MIB


@dataclass
class FakeSource:
    filename: str = "clip.mp4"
    content_type: str | None = "video/mp4"
    size: int = 128
    payload: bytes = b"input-video"

    async def read(self) -> bytes:
        return self.payload


class RecordingRunner(FfmpegRunner):
    def __init__(self, sizes_by_width: dict[int, int]) -> None:
        super().__init__("ffmpeg")
        self.sizes_by_width = sizes_by_width
        self.attempted_widths: list[int] = []

    @override
    async def run(self, *args: str) -> None:
        vf = args[args.index("-vf") + 1]
        output_path = Path(args[-1])
        for width, size in self.sizes_by_width.items():
            if f"min({width},iw)" in vf:
                self.attempted_widths.append(width)
                output_path.write_bytes(b"g" * size)
                return
        raise AssertionError(f"Unexpected vf: {vf}")


class ToGifServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_default_gif_presets_include_high_budget_tiers(self) -> None:
        self.assertEqual(
            DEFAULT_GIF_PRESETS[:3],
            (
                GifPreset(fps=20, width=640, max_colors=256),
                GifPreset(fps=18, width=540, max_colors=256),
                GifPreset(fps=15, width=480, max_colors=256),
            ),
        )

    async def test_high_byte_limit_prefers_top_preset_for_medium_duration_video(
        self,
    ) -> None:
        max_bytes = 100 * BYTES_PER_MIB
        runner = RecordingRunner({preset.width: 1024 for preset in DEFAULT_GIF_PRESETS})
        converter = FfmpegGifConverter(runner=runner)

        with patch.object(converter, "_probe_duration", return_value=30.0):
            result = await converter.convert(FakeSource(), max_bytes=max_bytes)

        self.assertEqual(len(result), 1024)
        self.assertEqual(runner.attempted_widths, [640])

    async def test_low_byte_limit_checks_smallest_preset_first_for_medium_duration(
        self,
    ) -> None:
        max_bytes = 5 * BYTES_PER_MIB
        runner = RecordingRunner(
            {preset.width: max_bytes + 1024 for preset in DEFAULT_GIF_PRESETS}
        )
        converter = FfmpegGifConverter(runner=runner)

        with patch.object(converter, "_probe_duration", return_value=20.0):
            with self.assertRaises(PayloadTooLargeError):
                await converter.convert(FakeSource(), max_bytes=max_bytes)

        self.assertEqual(runner.attempted_widths, [200])
