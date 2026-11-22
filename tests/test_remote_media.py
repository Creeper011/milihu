import asyncio
import socket
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

from milihu.exceptions.payload import InputTooLargeError
from milihu.services.http.remote_media import (
    HttpRemoteMediaFetcher,
    extract_media_url_from_html,
)


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content_length: int | None = len(body)
        self.content = _FakeContent([body] if body else [])


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    @asynccontextmanager
    async def get(self, url: str, **_: Any) -> AsyncIterator[_FakeResponse]:
        self.requested.append(url)
        yield self._responses[url]

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class RemoteMediaTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_media_url_from_html_prefers_video_then_gif(self) -> None:
        klipy_html = """
        <html><head>
          <meta property="og:video" content="https://static.klipy.com/ii/123/video.mp4">
          <meta property="og:image" content="https://static.klipy.com/ii/123/thumb.jpg">
        </head></html>
        """
        self.assertEqual(
            extract_media_url_from_html(
                klipy_html,
                "https://klipy.com/gifs/momona-lady-jewelpet",
            ),
            "https://static.klipy.com/ii/123/video.mp4",
        )

        gif_fallback_html = """
        <html><head>
          <meta property="og:video" content="https://example.com/embed/player.html">
          <meta property="og:video:type" content="text/html">
          <meta property="og:image" content="/assets/anim.gif">
        </head></html>
        """
        self.assertEqual(
            extract_media_url_from_html(
                gif_fallback_html,
                "https://example.com/gifs/anim",
            ),
            "https://example.com/assets/anim.gif",
        )

    async def test_validate_url_blocks_private_ips_and_bad_schemes(self) -> None:
        fetcher = HttpRemoteMediaFetcher()

        with self.assertRaises(ValueError):
            await fetcher.validate_url("file:///etc/passwd")

        async def fake_private_dns(*_: Any, **__: Any) -> list[Any]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))]

        with patch.object(
            asyncio.get_running_loop(),
            "getaddrinfo",
            side_effect=fake_private_dns,
        ):
            with self.assertRaises(ValueError):
                await fetcher.validate_url("https://internal.example/gif.gif")

    async def test_fetch_resolves_html_gif_page_and_preserves_slug(self) -> None:
        page_url = "https://klipy.com/gifs/momona-lady-jewelpet"
        media_url = "https://static.klipy.com/ii/abc/video.mp4"
        fake_session = _FakeSession(
            {
                page_url: _FakeResponse(
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=(
                        f'<meta property="og:video" content="{media_url}">'.encode()
                    ),
                ),
                media_url: _FakeResponse(
                    headers={"Content-Type": "video/mp4"},
                    body=b"mp4-video-bytes",
                ),
            }
        )
        fetcher = HttpRemoteMediaFetcher()

        with (
            patch.object(fetcher, "validate_url", return_value=None),
            patch(
                "milihu.services.http.remote_media.ClientSession",
                return_value=fake_session,
            ),
        ):
            source = await fetcher.fetch(page_url, max_bytes=1024)

        self.assertEqual(fake_session.requested, [page_url, media_url])
        self.assertEqual(source.filename, "momona-lady-jewelpet.mp4")
        self.assertEqual(source.content_type, "video/mp4")
        self.assertEqual(await source.read(), b"mp4-video-bytes")

    async def test_fetch_enforces_max_bytes(self) -> None:
        media_url = "https://cdn.example/huge.gif"
        fake_session = _FakeSession(
            {
                media_url: _FakeResponse(
                    headers={"Content-Type": "image/gif"},
                    body=b"x" * 2048,
                )
            }
        )
        fetcher = HttpRemoteMediaFetcher()

        with (
            patch.object(fetcher, "validate_url", return_value=None),
            patch(
                "milihu.services.http.remote_media.ClientSession",
                return_value=fake_session,
            ),
        ):
            with self.assertRaises(InputTooLargeError):
                await fetcher.fetch(media_url, max_bytes=1024)
