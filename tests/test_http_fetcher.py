import unittest
from collections.abc import AsyncIterator
from typing import Self
from unittest.mock import patch

from aiohttp import ClientError

from milihu.exceptions.download import DownloadError
from milihu.services.http.http_fetcher import AiohttpFetcher


class _FakeResponse:
    def __init__(
        self,
        chunks: tuple[bytes, ...] = (b"ab", b"cd"),
        *,
        status: int = 200,
        content_length: int | None = 4,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.content_length = content_length
        self._chunks = chunks
        self._error = error

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    @property
    def content(self) -> Self:
        return self

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def get(self, url: str) -> _FakeResponse:
        return self.response


class AiohttpFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_chunks_and_metadata(self) -> None:
        session = _FakeSession(_FakeResponse((b"hello ", b"world"), content_length=11))
        with patch(
            "milihu.services.http.http_fetcher.ClientSession", return_value=session
        ):
            async with AiohttpFetcher().open_session() as http_session:
                async with http_session.get("https://cdn.example/f.mp4") as stream:
                    self.assertEqual(stream.status, 200)
                    self.assertEqual(stream.content_length, 11)
                    chunks = [chunk async for chunk in stream.iter_chunks()]
        self.assertEqual(b"".join(chunks), b"hello world")

    async def test_translates_client_error_to_download_error(self) -> None:
        session = _FakeSession(_FakeResponse(error=ClientError("boom")))
        with patch(
            "milihu.services.http.http_fetcher.ClientSession", return_value=session
        ):
            async with AiohttpFetcher().open_session() as http_session:
                with self.assertRaisesRegex(DownloadError, "boom"):
                    async with http_session.get("https://cdn.example/f.mp4"):
                        pass
