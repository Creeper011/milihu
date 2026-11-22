"""Remote repository metadata contract for project usecases."""

from typing import Protocol

from milihu.types.project import RemoteProjectInfo


class RemoteProjectFetcher(Protocol):
    """Fetch project description and URL from the configured git remote."""

    async def fetch(self) -> RemoteProjectInfo | None: ...
