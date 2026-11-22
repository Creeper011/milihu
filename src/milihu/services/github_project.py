"""Resolve repository description and URL from the local git GitHub remote."""

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TypeIs

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import ValidationError

from milihu.types.default.project import (
    DEFAULT_GIT_REMOTE,
    DEFAULT_GITHUB_TIMEOUT_SECONDS,
    DEFAULT_REPO_DIR,
    GITHUB_API_HEADERS,
    GITHUB_API_REPOS_URL,
    GITHUB_REMOTE_PATTERN,
    GITHUB_WEB_BASE_URL,
)
from milihu.types.project import RemoteProjectInfo

_LOGGER = logging.getLogger(__name__)


def parse_github_slug(remote_url: str) -> tuple[str, str] | None:
    """Extract ``(owner, repo)`` from a GitHub HTTPS or SSH git remote URL."""
    match = GITHUB_REMOTE_PATTERN.fullmatch(remote_url.strip())
    if match is None:
        return None
    return match.group("owner"), match.group("repo")


def _is_object(value: object) -> TypeIs[Mapping[str, object]]:
    return isinstance(value, Mapping)


class GitHubProjectFetcher:
    """Read the git remote URL and fetch the GitHub repository description."""

    def __init__(
        self,
        *,
        repo_dir: Path = DEFAULT_REPO_DIR,
        remote: str = DEFAULT_GIT_REMOTE,
        timeout_seconds: float = DEFAULT_GITHUB_TIMEOUT_SECONDS,
    ) -> None:
        self._repo_dir = repo_dir
        self._remote = remote
        self._timeout = ClientTimeout(total=timeout_seconds)

    async def _read_remote_url(self) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self._repo_dir),
                "remote",
                "get-url",
                self._remote,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
        except OSError:
            return None
        if process.returncode != 0:
            return None
        url = stdout.decode(errors="replace").strip()
        return url or None

    async def _fetch_github_json(
        self, owner: str, repo: str
    ) -> Mapping[str, object] | None:
        url = f"{GITHUB_API_REPOS_URL}/{owner}/{repo}"
        try:
            async with (
                ClientSession(timeout=self._timeout) as session,
                session.get(url, headers=GITHUB_API_HEADERS) as response,
            ):
                if response.status != 200:
                    _LOGGER.warning(
                        "GitHub metadata request for %s/%s returned HTTP %s",
                        owner,
                        repo,
                        response.status,
                    )
                    return None
                payload: object = await response.json()
        except (ClientError, TimeoutError, ValueError) as exc:
            _LOGGER.warning("GitHub metadata request failed: %s", exc)
            return None
        return payload if _is_object(payload) else None

    async def fetch(self) -> RemoteProjectInfo | None:
        """Resolve GitHub description and repository URL from the git remote."""
        remote_url = await self._read_remote_url()
        if remote_url is None:
            return None
        slug = parse_github_slug(remote_url)
        if slug is None:
            return None

        owner, repo = slug
        payload = await self._fetch_github_json(owner, repo)
        if payload is None:
            return None

        raw_description = payload.get("description")
        description = (
            raw_description.strip()
            if isinstance(raw_description, str) and raw_description.strip()
            else None
        )
        raw_html_url = payload.get("html_url")
        repository_url = (
            raw_html_url.strip()
            if isinstance(raw_html_url, str) and raw_html_url.strip()
            else f"{GITHUB_WEB_BASE_URL}/{owner}/{repo}"
        )
        try:
            return RemoteProjectInfo.model_validate(
                {
                    "description": description,
                    "repository_url": repository_url,
                }
            )
        except ValidationError as exc:
            _LOGGER.warning("Invalid GitHub metadata payload: %s", exc)
            return None
