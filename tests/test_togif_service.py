import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

from milihu.exceptions.payload import PayloadTooLargeError
from milihu.services.ffmpeg.gif_renderer import FfmpegGifRenderer
from milihu.services.ffmpeg.runner import FfmpegRunner
from milihu.types.default.gif import DEFAULT_GIF_PRESETS, GifPreset
from milihu.types.default.payload import BYTES_PER_MIB
from milihu.usecases.togif_service import GifConverterService


@dataclass
class FakeSource:
    filename: str = "clip.mp4"
    content_type: str | None = "video/mp4"
    size: int = 128
    payload: bytes = b"input-video"

    async def read(self) -> bytes:
        return self.payload


class RecordingGifRenderer:
    def __init__(
        self,
        sizes_by_width: dict[int, int],
        *,
        duration: float | None = None,
    ) -> None:
        self.sizes_by_width = sizes_by_width
        self.duration = duration
        self.attempted_widths: list[int] = []

    async def probe_duration(self, input_path: Path) -> float | None:
        return self.duration

    async def render_gif(
        self,
        input_path: Path,
        output_path: Path,
        preset: GifPreset,
    ) -> None:
        size = self.sizes_by_width[preset.width]
        self.attempted_widths.append(preset.width)
        output_path.write_bytes(b"g" * size)


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
        renderer = RecordingGifRenderer(
            {preset.width: 1024 for preset in DEFAULT_GIF_PRESETS},
            duration=30.0,
        )
        converter = GifConverterService(renderer=renderer)

        result = await converter.convert(FakeSource(), max_bytes=max_bytes)

        self.assertEqual(len(result), 1024)
        self.assertEqual(renderer.attempted_widths, [640])

    async def test_low_byte_limit_checks_smallest_preset_first_for_medium_duration(
        self,
    ) -> None:
        max_bytes = 5 * BYTES_PER_MIB
        renderer = RecordingGifRenderer(
            {preset.width: max_bytes + 1024 for preset in DEFAULT_GIF_PRESETS},
            duration=20.0,
        )
        converter = GifConverterService(renderer=renderer)

        with self.assertRaises(PayloadTooLargeError):
            await converter.convert(FakeSource(), max_bytes=max_bytes)

        self.assertEqual(renderer.attempted_widths, [200])

    async def test_ffmpeg_gif_renderer_builds_gif_filter_and_runner_probes_duration(
        self,
    ) -> None:
        runner = FfmpegRunner("/usr/bin/ffmpeg")
        renderer = FfmpegGifRenderer(runner)
        preset = GifPreset(fps=15, width=480, max_colors=128)
        recorded: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> None:
            recorded.append(args)

        with patch.object(runner, "run", side_effect=fake_run):
            await renderer.render_gif(Path("/tmp/in.mp4"), Path("/tmp/out.gif"), preset)

        self.assertEqual(len(recorded), 1)
        args = recorded[0]
        self.assertEqual(args[:3], ("-y", "-i", "/tmp/in.mp4"))
        self.assertEqual(args[3], "-vf")
        self.assertIn("fps=15", args[4])
        self.assertIn("min(480,iw)", args[4])
        self.assertIn("max_colors=128", args[4])
        self.assertEqual(args[5], "/tmp/out.gif")

        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"12.5\n", b""))
        with patch(
            "milihu.services.ffmpeg.runner.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ) as exec_mock:
            duration = await renderer.probe_duration(Path("/tmp/in.mp4"))

        self.assertEqual(duration, 12.5)
        self.assertEqual(exec_mock.call_args.args[0], "/usr/bin/ffprobe")
