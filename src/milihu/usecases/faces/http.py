"""HTTP byte-transfer contract for application services."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Protocol


class ByteStream(Protocol):
    """Streamed HTTP response body with status and optional content length."""

    @property
    def status(self) -> int: ...

    @property
    def content_length(self) -> int | None: ...

    def iter_chunks(self, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]: ...


class HttpSession(Protocol):
    """Reusable HTTP session for streaming remote resources."""

    def get(self, url: str) -> AbstractAsyncContextManager[ByteStream]: ...


class HttpFetcher(Protocol):
    """Open an HTTP transfer session backed by a real HTTP client."""

    def open_session(self) -> AbstractAsyncContextManager[HttpSession]: ...
