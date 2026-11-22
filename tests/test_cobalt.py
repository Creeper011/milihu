import asyncio
import unittest
from collections.abc import Mapping
from typing import Self
from unittest.mock import patch

from milihu.services.core.cobalt import Cobalt
from milihu.types.download.format import DownloadFormat
from milihu.types.download.quality import DownloadQuality


class Response:
    def __init__(self, data: Mapping[str, object]) -> None:
        self.data = data

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    async def json(self) -> Mapping[str, object]:
        await asyncio.sleep(0)
        return self.data


class Session:
    def __init__(self, result: Mapping[str, object]) -> None:
        self.result = result
        self.requests: list[dict[str, object]] = []
        self.closed = False

    def get(self, path: str) -> Response:
        return Response({"cobalt": {"version": "11"}})

    def post(self, path: str, *, json: dict[str, object]) -> Response:
        self.requests.append(json)
        return Response(self.result)

    async def close(self) -> None:
        self.closed = True


class CobaltTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_use_generic_options(self) -> None:
        session = Session(
            {
                "status": "redirect",
                "url": "https://cdn.example/file",
                "filename": "a.mp4",
            }
        )
        with patch("milihu.services.core.cobalt.ClientSession", return_value=session):
            async with Cobalt("http://127.0.0.1:9000") as backend:
                first, second = await asyncio.gather(
                    backend.resolve(
                        "https://example.com/one", format=DownloadFormat.OPUS
                    ),
                    backend.resolve(
                        "https://example.com/two",
                        mode="mute",
                        video_quality=DownloadQuality.Q720,
                        format=DownloadFormat.WEBM,
                    ),
                )

        self.assertEqual(
            first,
            {
                "status": "file",
                "url": "https://cdn.example/file",
                "filename": "a.mp4",
                "delivery": "direct",
            },
        )
        self.assertEqual(first, second)
        self.assertEqual(
            session.requests,
            [
                {
                    "url": "https://example.com/one",
                    "downloadMode": "audio",
                    "audioFormat": "opus",
                },
                {
                    "url": "https://example.com/two",
                    "downloadMode": "mute",
                    "videoQuality": "720",
                    "youtubeVideoContainer": "webm",
                    "youtubeVideoCodec": "vp9",
                },
            ],
        )
        self.assertTrue(session.closed)

    async def test_picker_and_error_are_normalized(self) -> None:
        for raw, expected in [
            (
                {"status": "picker", "picker": [{"type": "photo", "url": "https://x"}]},
                {
                    "status": "selection",
                    "items": [{"type": "photo", "url": "https://x"}],
                },
            ),
            (
                {"status": "error", "error": {"code": "bad.link"}},
                {"status": "error", "code": "bad.link"},
            ),
        ]:
            session = Session(raw)
            with patch(
                "milihu.services.core.cobalt.ClientSession", return_value=session
            ):
                async with Cobalt("http://127.0.0.1:9000") as backend:
                    self.assertEqual(
                        await backend.resolve("https://example.com"), expected
                    )

    async def test_processing_response_keeps_work_for_downloader(self) -> None:
        session = Session(
            {
                "status": "local-processing",
                "type": "merge",
                "service": "youtube",
                "tunnel": ["https://a", "https://b"],
                "output": {"type": "video/mp4", "filename": "video.mp4"},
            }
        )
        with patch(
            "milihu.services.core.cobalt.ClientSession", return_value=session
        ):
            async with Cobalt("http://127.0.0.1:9000") as backend:
                result = await backend.resolve("https://example.com")

        self.assertEqual(
            result,
            {
                "status": "processing",
                "operation": "merge",
                "source": "youtube",
                "urls": ["https://a", "https://b"],
                "output": {"type": "video/mp4", "filename": "video.mp4"},
            },
        )

    async def test_rejects_requests_outside_lifecycle(self) -> None:
        backend = Cobalt("http://127.0.0.1:9000")
        with self.assertRaises(RuntimeError):
            await backend.resolve("https://example.com")
