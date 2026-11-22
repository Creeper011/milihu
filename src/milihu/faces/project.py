"""Service protocol for reading project metadata."""

from typing import Protocol

from milihu.types.project import ProjectOutput


class ProjectInfoProvider(Protocol):
    """Protocol consumed by Discord cogs to inspect project metadata."""

    async def get_project(self) -> ProjectOutput: ...
