"""aiohttp implementation of HTTP byte-stream fetching."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from milihu.exceptions.download import DownloadError
from milihu.usecases.faces.http import ByteStream, HttpSession


class _AiohttpByteStream:
    def __init__(self, response: ClientResponse) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status

    @property
    def content_length(self) -> int | None:
        return self._response.content_length

    async def iter_chunks(self, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.content.iter_chunked(chunk_size):
                yield chunk
        except (ClientError, TimeoutError) as exc:
            raise DownloadError(str(exc)) from exc


class _AiohttpSession:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    @asynccontextmanager
    async def get(self, url: str) -> AsyncIterator[ByteStream]:
        try:
            async with self._session.get(url) as response:
                response.raise_for_status()
                yield _AiohttpByteStream(response)
        except (ClientError, TimeoutError) as exc:
            raise DownloadError(str(exc)) from exc


class AiohttpFetcher:
    """Open reusable ``aiohttp`` sessions for streaming remote files."""

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        self._timeout = ClientTimeout(total=timeout_seconds)

    @asynccontextmanager
    async def open_session(self) -> AsyncIterator[HttpSession]:
        async with ClientSession(timeout=self._timeout) as session:
            yield _AiohttpSession(session)
