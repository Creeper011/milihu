import unittest
from collections.abc import AsyncIterator
from typing import Self, Unpack, override
from unittest.mock import patch

from pydantic import BaseModel

from milihu.exceptions.payload import PayloadTooLargeError
from milihu.services.core.litterbox import Litterbox
from milihu.services.downloader_service import DownloaderService
from milihu.types.download.format import DownloadFormat
from milihu.types.download.quality import DownloadQuality
from milihu.types.download.request import DownloadRequest
from milihu.types.media import MediaResolveOptions, MediaResult
from milihu.types.upload import UploadLifetime


class Backend:
    def __init__(self, filename: str = "video.mp4") -> None:
        self.calls = 0
        self.filename = filename
        self.options: MediaResolveOptions = {}

    async def resolve(
        self, url: str, **options: Unpack[MediaResolveOptions]
    ) -> MediaResult:
        self.calls += 1
        self.options = options
        return {
            "status": "file",
            "url": "https://cdn.example/video",
            "filename": self.filename,
            "delivery": "direct",
        }


class MultiQualityBackend:
    def __init__(self) -> None:
        self.qualities: list[DownloadQuality] = []

    async def resolve(
        self, url: str, **options: Unpack[MediaResolveOptions]
    ) -> MediaResult:
        q = options.get("video_quality")
        assert q is not None
        self.qualities.append(q)
        return {
            "status": "file",
            "url": f"https://cdn.example/video_{q}",
            "filename": f"video_{q}.mp4",
            "delivery": "direct",
        }


class FakeUploader(Litterbox):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.uploads: list[tuple[bytes, str]] = []

    @override
    async def upload(
        self,
        data: bytes,
        filename: str,
        *,
        lifetime: UploadLifetime | None = None,
    ) -> str:
        self.uploads.append((data, filename))
        return self.url


class Response:
    status = 200
    content_length: int | None = None

    def __init__(self, chunks: tuple[bytes, ...] = (b"1234", b"5678")) -> None:
        self.chunks = chunks

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    @property
    def content(self) -> Self:
        return self

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class Session:
    def __init__(self, response: Response | None = None) -> None:
        self.response = response or Response()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def get(self, url: str) -> Response:
        assert url == "https://cdn.example/video"
        return self.response


class DownloaderServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_empty_tunnel_without_retry(self) -> None:
        backend = Backend()
        with patch(
            "milihu.services.downloader_service.ClientSession",
            return_value=Session(Response(())),
        ):
            with self.assertRaisesRegex(RuntimeError, "empty"):
                await DownloaderService(backend).download(
                    "https://example.com/post", max_bytes=8
                )
        self.assertEqual(backend.calls, 1)

    async def test_downloads_resolved_file(self) -> None:
        backend = Backend("youtube_abc_1280x720_h264.mp4")
        with (
            patch(
                "milihu.services.downloader_service.ClientSession",
                return_value=Session(),
            ),
            patch(
                "milihu.services.downloader_service.perf_counter",
                side_effect=(10.0, 11.25),
            ),
        ):
            output = await DownloaderService(backend).download(
                "https://example.com/post", max_bytes=8
            )
        self.assertIsInstance(output, BaseModel)
        self.assertEqual(backend.options, {"video_quality": DownloadQuality.Q1440})
        self.assertEqual(output.filename, "youtube_abc_1280x720_h264.mp4")
        self.assertEqual(output.data, b"12345678")
        self.assertEqual(output.size_bytes, 8)
        self.assertEqual(output.elapsed_seconds, 1.25)
        self.assertEqual(output.resolution, "1280x720")
        self.assertEqual(output.codec, "h264")

    async def test_forwards_the_requested_format(self) -> None:
        request = DownloadRequest.model_validate(
            {"url": "https://example.com/post", "format": "ogg", "quality": "1080"}
        )
        backend = Backend("audio.OGG")
        with patch(
            "milihu.services.downloader_service.ClientSession", return_value=Session()
        ):
            output = await DownloaderService(backend).download(request, max_bytes=8)

        self.assertIs(backend.options.get("format"), DownloadFormat.OGG)
        self.assertIs(backend.options.get("video_quality"), DownloadQuality.Q1080)
        self.assertEqual(output.filename, "audio.OGG")

    async def test_refuses_a_different_format_before_fetching(self) -> None:
        request = DownloadRequest.model_validate(
            {"url": "https://example.com/post", "format": "mkv"}
        )
        with patch("milihu.services.downloader_service.ClientSession") as session:
            with self.assertRaisesRegex(RuntimeError, "does not offer the MKV format"):
                await DownloaderService(Backend()).download(request, max_bytes=8)

        session.assert_not_called()

    async def test_uploads_large_file_with_temporary_uploader_by_default(self) -> None:
        uploader = FakeUploader("https://litter.catbox.moe/abc123.mp4")
        with patch(
            "milihu.services.downloader_service.ClientSession", return_value=Session()
        ):
            output = await DownloaderService(Backend(), uploader=uploader).download(
                "https://example.com/post", max_bytes=7
            )

        self.assertEqual(uploader.uploads, [(b"12345678", "video.mp4")])
        self.assertEqual(output.url, "https://litter.catbox.moe/abc123.mp4")
        self.assertEqual(output.size_bytes, 8)

    async def test_stops_when_response_exceeds_temporary_upload_limit(self) -> None:
        with patch(
            "milihu.services.downloader_service.ClientSession", return_value=Session()
        ):
            with self.assertRaises(PayloadTooLargeError):
                await DownloaderService(
                    Backend(), max_upload_bytes=7
                ).download("https://example.com/post", max_bytes=4)

    async def test_steps_down_video_quality_to_fit_max_bytes(self) -> None:
        class MultiQualitySession:
            sizes = {
                "1440": 16,
                "1080": 14,
                "720": 12,
                "480": 10,
                "360": 8,
                "240": 6,
                "144": 4,
            }

            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                pass

            def get(self, url: str) -> Response:
                q = url.rsplit("_", 1)[-1]
                size = self.sizes[q]
                return Response((b"x" * 4, b"x" * (size - 4)))

        backend = MultiQualityBackend()
        uploader = FakeUploader("https://litter.catbox.moe/unused.mp4")
        with patch(
            "milihu.services.downloader_service.ClientSession",
            return_value=MultiQualitySession(),
        ):
            output = await DownloaderService(backend, uploader=uploader).download(
                "https://example.com/post", max_bytes=8
            )

        self.assertEqual(output.filename, "video_360.mp4")
        self.assertEqual(output.size_bytes, 8)
        self.assertIsNone(output.url)
        self.assertEqual(uploader.uploads, [])

    async def test_falls_back_to_uploader_when_lowest_quality_exceeds_max_bytes(
        self,
    ) -> None:
        class MultiQualitySession:
            sizes = {
                "1440": 16,
                "1080": 14,
                "720": 12,
                "480": 10,
                "360": 9,
                "240": 8,
                "144": 6,
            }

            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                pass

            def get(self, url: str) -> Response:
                q = url.rsplit("_", 1)[-1]
                size = self.sizes[q]
                return Response((b"x" * size,))

        uploader = FakeUploader("https://litter.catbox.moe/fallback.mp4")
        with patch(
            "milihu.services.downloader_service.ClientSession",
            return_value=MultiQualitySession(),
        ):
            output = await DownloaderService(
                MultiQualityBackend(), uploader=uploader
            ).download("https://example.com/post", max_bytes=5)

        self.assertEqual(output.url, "https://litter.catbox.moe/fallback.mp4")
        self.assertEqual(len(uploader.uploads), 1)
        self.assertEqual(output.filename, "video_144.mp4")
        self.assertEqual(output.size_bytes, 6)

    async def test_audio_format_does_not_step_down_video_quality(self) -> None:
        backend = Backend("track.mp3")
        uploader = FakeUploader("https://litter.catbox.moe/audio.mp3")
        request = DownloadRequest.model_validate(
            {"url": "https://example.com/post", "format": "mp3"}
        )
        with patch(
            "milihu.services.downloader_service.ClientSession", return_value=Session()
        ):
            output = await DownloaderService(backend, uploader=uploader).download(
                request, max_bytes=7
            )

        self.assertEqual(backend.calls, 1)
        self.assertEqual(output.url, "https://litter.catbox.moe/audio.mp3")

    async def test_explicit_video_quality_does_not_step_down_when_exceeding_max_bytes(
        self,
    ) -> None:
        backend = MultiQualityBackend()
        uploader = FakeUploader("https://litter.catbox.moe/video_1080.mp4")
        request = DownloadRequest.model_validate(
            {"url": "https://example.com/post", "quality": "1080"}
        )
        with patch(
            "milihu.services.downloader_service.ClientSession",
            return_value=Session(Response((b"1234", b"5678"))),
        ):
            with patch.object(
                Session,
                "get",
                return_value=Response((b"1234", b"5678")),
            ):
                output = await DownloaderService(backend, uploader=uploader).download(
                    request, max_bytes=5
                )

        self.assertEqual(backend.qualities, [DownloadQuality.Q1080])
        self.assertEqual(output.filename, "video_1080.mp4")
        self.assertEqual(output.size_bytes, 8)
        self.assertEqual(output.url, "https://litter.catbox.moe/video_1080.mp4")
