import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from milihu.exceptions.payload import InputTooLargeError, PayloadTooLargeError
from milihu.services.ffmpeg.caption_renderer import FfmpegCaptionRenderer
from milihu.services.ffmpeg.runner import FfmpegRunner
from milihu.types.caption import CaptionPosition, CaptionStyle
from milihu.types.default.caption import (
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_STYLE,
)
from milihu.types.default.gif import DEFAULT_GIF_PRESETS, GifPreset
from milihu.types.default.payload import BYTES_PER_MIB
from milihu.usecases.caption_service import CaptionService


@dataclass
class FakeSource:
    filename: str = "photo.png"
    content_type: str | None = "image/png"
    size: int = 128
    payload: bytes = b"input-media"

    async def read(self) -> bytes:
        return self.payload


class RecordingCaptionRenderer:
    def __init__(
        self,
        *,
        static_size: int = 512,
        sizes_by_width: dict[int, int] | None = None,
        duration: float | None = None,
    ) -> None:
        self.static_size = static_size
        self.sizes_by_width = sizes_by_width or {
            preset.width: 1024 for preset in DEFAULT_GIF_PRESETS
        }
        self.duration = duration
        self.calls: list[
            tuple[str, CaptionPosition, CaptionStyle, GifPreset | None]
        ] = []

    async def probe_duration(self, input_path: Path) -> float | None:
        return self.duration

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
        self.calls.append((text, position, style, preset))
        size = self.static_size if preset is None else self.sizes_by_width[preset.width]
        output_path.write_bytes(b"c" * size)


class CaptionServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_is_supported_accepts_images_gifs_and_videos(self) -> None:
        service = CaptionService(renderer=RecordingCaptionRenderer())
        self.assertTrue(
            service.is_supported(FakeSource(filename="a.png", content_type="image/png"))
        )
        self.assertTrue(
            service.is_supported(FakeSource(filename="a.gif", content_type=None))
        )
        self.assertTrue(
            service.is_supported(FakeSource(filename="a.mp4", content_type="video/mp4"))
        )
        self.assertFalse(
            service.is_supported(
                FakeSource(filename="notes.txt", content_type="text/plain")
            )
        )

    async def test_static_image_renders_png_with_default_bar_style(self) -> None:
        renderer = RecordingCaptionRenderer(static_size=256)
        service = CaptionService(renderer=renderer)

        output = await service.caption(
            FakeSource(filename="cat.jpg", content_type="image/jpeg"),
            "  hello world  ",
            max_bytes=1024,
        )

        self.assertEqual(output.filename, "cat_captioned.png")
        self.assertEqual(output.extension, ".png")
        self.assertEqual(len(output.data), 256)
        self.assertEqual(renderer.calls, [("hello world", "top", "bar", None)])

    async def test_remote_url_resolved_via_remote_fetcher(self) -> None:
        class FakeRemoteFetcher:
            def __init__(self) -> None:
                self.urls: list[str] = []

            async def fetch(
                self,
                url: str,
                *,
                max_bytes: int | None = None,
            ) -> FakeSource:
                self.urls.append(url)
                return FakeSource(
                    filename="momona-lady-jewelpet.mp4",
                    content_type="video/mp4",
                    size=256,
                )

        fetcher = FakeRemoteFetcher()
        renderer = RecordingCaptionRenderer()
        service = CaptionService(renderer=renderer, remote_fetcher=fetcher)

        output = await service.caption(
            "https://klipy.com/gifs/momona-lady-jewelpet",
            "hello",
            max_bytes=5 * BYTES_PER_MIB,
        )

        self.assertEqual(fetcher.urls, ["https://klipy.com/gifs/momona-lady-jewelpet"])
        self.assertEqual(output.filename, "momona-lady-jewelpet_captioned.gif")
        self.assertEqual(output.extension, ".gif")

    async def test_static_image_exceeding_max_bytes_raises_payload_too_large(
        self,
    ) -> None:
        renderer = RecordingCaptionRenderer(static_size=2048)
        service = CaptionService(renderer=renderer)

        with self.assertRaises(PayloadTooLargeError):
            await service.caption(FakeSource(), "too big", max_bytes=1024)

    async def test_animated_gif_fits_within_budget_and_returns_gif_extension(
        self,
    ) -> None:
        max_bytes = 5 * BYTES_PER_MIB
        renderer = RecordingCaptionRenderer(
            sizes_by_width={
                preset.width: (max_bytes + 1024 if preset.width > 360 else 1024)
                for preset in DEFAULT_GIF_PRESETS
            },
            duration=4.0,
        )
        service = CaptionService(renderer=renderer)

        output = await service.caption(
            FakeSource(filename="dance.gif", content_type="image/gif"),
            "nice move",
            position="bottom",
            style="bar",
            max_bytes=max_bytes,
        )

        self.assertEqual(output.extension, ".gif")
        self.assertEqual(len(output.data), 1024)
        self.assertTrue(all(call[1] == "bottom" for call in renderer.calls))
        self.assertTrue(all(call[2] == "bar" for call in renderer.calls))
        self.assertTrue(all(call[3] is not None for call in renderer.calls))

    async def test_empty_and_overlong_text_rejected(self) -> None:
        service = CaptionService(
            renderer=RecordingCaptionRenderer(), max_text_length=10
        )

        with self.assertRaises(ValueError):
            await service.caption(FakeSource(), "   ")

        with self.assertRaises(ValueError):
            await service.caption(FakeSource(), "a" * 11)

    async def test_input_too_large_rejected_before_read(self) -> None:
        service = CaptionService(
            renderer=RecordingCaptionRenderer(),
            max_input_bytes=64,
        )

        with self.assertRaises(InputTooLargeError):
            await service.caption(FakeSource(size=128), "hi")

    async def test_ffmpeg_caption_renderer_builds_overlay_and_bar_filters(self) -> None:
        runner = FfmpegRunner("/usr/bin/ffmpeg")
        renderer = FfmpegCaptionRenderer(runner)
        recorded: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> None:
            recorded.append(args)

        with patch.object(runner, "run", side_effect=fake_run):
            await renderer.render_caption(
                Path("/tmp/in.png"),
                Path("/tmp/out.png"),
                "hello: 100%",
                position="top",
                style="overlay",
                preset=None,
            )
            await renderer.render_caption(
                Path("/tmp/in.gif"),
                Path("/tmp/out.gif"),
                "bottom bar caption",
                position="bottom",
                style="bar",
                preset=GifPreset(fps=12, width=360, max_colors=128),
            )

        self.assertEqual(len(recorded), 2)

        overlay_args = recorded[0]
        self.assertEqual(overlay_args[:4], ("-y", "-i", "/tmp/in.png", "-vf"))
        self.assertIn("drawtext=textfile=", overlay_args[4])
        self.assertIn("expansion=none", overlay_args[4])
        self.assertIn("fontcolor=white", overlay_args[4])
        self.assertIn("borderw=3", overlay_args[4])
        self.assertNotIn("pad=", overlay_args[4])
        self.assertEqual(overlay_args[5:], ("-frames:v", "1", "/tmp/out.png"))

        bar_args = recorded[1]
        self.assertEqual(bar_args[:4], ("-y", "-i", "/tmp/in.gif", "-vf"))
        self.assertIn("fps=12", bar_args[4])
        self.assertIn("min(360,iw)", bar_args[4])
        self.assertIn("pad=iw:ih+", bar_args[4])
        self.assertIn(":0:0:white", bar_args[4])
        self.assertIn("fontcolor=black", bar_args[4])
        self.assertIn("max_colors=128", bar_args[4])
        self.assertEqual(bar_args[5:], ("/tmp/out.gif",))
