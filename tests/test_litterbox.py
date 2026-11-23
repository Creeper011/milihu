import unittest
from typing import Self
from unittest.mock import patch

from aiohttp import FormData

from milihu.services.litterbox import Litterbox
from milihu.types.default.upload import DEFAULT_UPLOAD_LIFETIME, LITTERBOX_API_URL
from milihu.types.upload import UploadLifetime


class Response:
    def __init__(self, body: str = "https://litter.catbox.moe/abc123.mp4\n") -> None:
        self.body = body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    async def text(self) -> str:
        return self.body


class Session:
    def __init__(self, response: Response | None = None) -> None:
        self.response = response or Response()
        self.calls: list[tuple[str, FormData]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def post(self, url: str, *, data: FormData) -> Response:
        self.calls.append((url, data))
        return self.response


class LitterboxTests(unittest.IsolatedAsyncioTestCase):
    def test_upload_lifetime_defaults_to_twelve_hours(self) -> None:
        self.assertEqual(
            set(UploadLifetime),
            {"1h", "12h", "24h", "72h"},
        )
        self.assertIs(DEFAULT_UPLOAD_LIFETIME, UploadLifetime.TWELVE_HOURS)
        self.assertEqual(DEFAULT_UPLOAD_LIFETIME, "12h")

    async def test_uploads_file_with_default_lifetime(self) -> None:
        session = Session()
        with patch(
            "milihu.services.litterbox.ClientSession", return_value=session
        ):
            url = await Litterbox().upload(b"video-bytes", "clip.mp4")

        self.assertEqual(url, "https://litter.catbox.moe/abc123.mp4")
        [(posted_url, form)] = session.calls
        self.assertEqual(posted_url, LITTERBOX_API_URL)
        fields = {
            options["name"]: (options.get("filename"), value)
            for options, _, value in form._fields  # pyright: ignore[reportPrivateUsage]
        }
        self.assertEqual(fields["reqtype"], (None, "fileupload"))
        self.assertEqual(fields["time"], (None, "12h"))
        self.assertEqual(fields["fileToUpload"], ("clip.mp4", b"video-bytes"))

    async def test_rejects_non_url_response(self) -> None:
        session = Session(Response("Invalid lifetime"))
        with patch(
            "milihu.services.litterbox.ClientSession", return_value=session
        ):
            with self.assertRaisesRegex(RuntimeError, "Invalid lifetime"):
                await Litterbox().upload(b"video-bytes", "clip.mp4")
