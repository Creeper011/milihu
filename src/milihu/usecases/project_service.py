"""Application service assembling project metadata and runtime stack."""

import asyncio
import importlib.metadata
import platform
import time
from collections.abc import Callable

from milihu.types.default.project import (
    DEFAULT_PROJECT_CACHE_TTL_SECONDS,
    DEFAULT_PROJECT_RETRY_TTL_SECONDS,
)
from milihu.types.project import ProjectOutput, RemoteProjectInfo
from milihu.usecases.faces.project import RemoteProjectFetcher


def _default_stack() -> tuple[str, ...]:
    items = [f"Python {platform.python_version()}"]
    try:
        items.append(f"discord.py {importlib.metadata.version('discord.py')}")
    except importlib.metadata.PackageNotFoundError:
        pass
    return tuple(items)


class ProjectService:
    """Resolve remote repository metadata and runtime stack for display."""

    def __init__(
        self,
        remote_fetcher: RemoteProjectFetcher | None = None,
        *,
        stack_provider: Callable[[], tuple[str, ...]] = _default_stack,
        cache_ttl_seconds: float = DEFAULT_PROJECT_CACHE_TTL_SECONDS,
        retry_ttl_seconds: float = DEFAULT_PROJECT_RETRY_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._remote_fetcher = remote_fetcher
        self._stack_provider = stack_provider
        self._cache_ttl_seconds = cache_ttl_seconds
        self._retry_ttl_seconds = retry_ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cached_remote: RemoteProjectInfo | None = None
        self._expires_at: float | None = None

    async def get_project(self) -> ProjectOutput:
        """Return project description, repository URL, and runtime stack."""
        remote = await self._get_remote()
        return ProjectOutput(
            description=remote.description if remote is not None else None,
            repository_url=remote.repository_url if remote is not None else None,
            stack=self._stack_provider(),
        )

    async def _get_remote(self) -> RemoteProjectInfo | None:
        if self._remote_fetcher is None:
            return None
        if self._expires_at is not None and self._clock() < self._expires_at:
            return self._cached_remote
        async with self._lock:
            if self._expires_at is not None and self._clock() < self._expires_at:
                return self._cached_remote
            remote = await self._remote_fetcher.fetch()
            self._cached_remote = remote
            ttl = (
                self._cache_ttl_seconds
                if remote is not None
                else self._retry_ttl_seconds
            )
            self._expires_at = self._clock() + ttl
            return remote
