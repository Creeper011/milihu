"""Checks ProjectService and GitHubProjectFetcher."""

import asyncio
import unittest
from collections.abc import Mapping
from unittest.mock import AsyncMock, patch

from pydantic import HttpUrl, TypeAdapter

from milihu.services.github_project import GitHubProjectFetcher, parse_github_slug
from milihu.types.default.project import DEFAULT_REPO_DIR
from milihu.types.project import ProjectOutput, RemoteProjectInfo
from milihu.usecases.project_service import ProjectService

_URL = TypeAdapter(HttpUrl)


class _StubFetcher:
    def __init__(
        self,
        responses: list[RemoteProjectInfo | None],
        *,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._responses = list(responses)
        self._gate = gate
        self.calls = 0

    async def fetch(self) -> RemoteProjectInfo | None:
        self.calls += 1
        if self._gate is not None:
            await self._gate.wait()
        return self._responses.pop(0)


class ProjectServiceTests(unittest.TestCase):
    def test_caches_failures_and_successes_with_ttl(self) -> None:
        async def check() -> None:
            now = [100.0]
            remote = RemoteProjectInfo(
                description="Milihu Discord bot",
                repository_url=_URL.validate_python(
                    "https://github.com/Creeper011/milihu"
                ),
            )
            fetcher = _StubFetcher([None, remote])
            service = ProjectService(
                fetcher,
                stack_provider=lambda: ("Python 3.13.12", "discord.py 2.7.1"),
                cache_ttl_seconds=3600.0,
                retry_ttl_seconds=300.0,
                clock=lambda: now[0],
            )

            # Failure is cached until retry_ttl_seconds passes.
            first = await service.get_project()
            second = await service.get_project()
            self.assertEqual(
                first.description,
                "A random utility bot for Discord | Music, Download, Image",
            )
            self.assertEqual(first, second)
            self.assertEqual(fetcher.calls, 1)

            # After retry TTL expires, retries and caches the successful result.
            now[0] += 300.0
            third = await service.get_project()
            fourth = await service.get_project()
            self.assertEqual(
                third,
                ProjectOutput(
                    description="Milihu Discord bot",
                    repository_url=_URL.validate_python(
                        "https://github.com/Creeper011/milihu"
                    ),
                    stack=("Python 3.13.12", "discord.py 2.7.1"),
                ),
            )
            self.assertEqual(fourth, third)
            self.assertEqual(fetcher.calls, 2)

        asyncio.run(check())

    def test_concurrent_calls_coalesce_into_single_fetch(self) -> None:
        async def check() -> None:
            gate = asyncio.Event()
            remote = RemoteProjectInfo(description="Coalesced description")
            fetcher = _StubFetcher([remote], gate=gate)
            service = ProjectService(
                fetcher,
                stack_provider=lambda: ("Python 3.13.12",),
            )

            tasks = [asyncio.create_task(service.get_project()) for _ in range(10)]
            await asyncio.sleep(0)
            gate.set()
            results = await asyncio.gather(*tasks)

            self.assertEqual(fetcher.calls, 1)
            self.assertTrue(
                all(r.description == "Coalesced description" for r in results)
            )

        asyncio.run(check())

    def test_default_stack_reports_python_and_discord_py(self) -> None:
        async def check() -> None:
            output = await ProjectService().get_project()
            self.assertTrue(output.stack[0].startswith("Python "))
            self.assertTrue(
                any(item.startswith("discord.py ") for item in output.stack)
            )

        asyncio.run(check())


class GitHubProjectFetcherTests(unittest.TestCase):
    def test_parse_github_slug_supports_https_and_ssh_remotes(self) -> None:
        self.assertEqual(
            parse_github_slug("https://github.com/Creeper011/milihu.git"),
            ("Creeper011", "milihu"),
        )
        self.assertEqual(
            parse_github_slug("https://github.com/Creeper011/milihu/"),
            ("Creeper011", "milihu"),
        )
        self.assertEqual(
            parse_github_slug("git@github.com:Creeper011/milihu.git"),
            ("Creeper011", "milihu"),
        )
        self.assertEqual(
            parse_github_slug("ssh://git@github.com/Creeper011/milihu.git"),
            ("Creeper011", "milihu"),
        )
        self.assertIsNone(parse_github_slug("https://gitlab.com/user/repo.git"))
        self.assertTrue((DEFAULT_REPO_DIR / "pyproject.toml").is_file())

    def test_fetch_reads_git_remote_and_github_description(self) -> None:
        async def check() -> None:
            fetcher = GitHubProjectFetcher()
            payload: Mapping[str, object] = {
                "description": "  Cool bot description  ",
                "html_url": "https://github.com/Creeper011/milihu",
            }
            with (
                patch.object(
                    fetcher,
                    "_read_remote_url",
                    AsyncMock(return_value="git@github.com:Creeper011/milihu.git"),
                ),
                patch.object(
                    fetcher,
                    "_fetch_github_json",
                    AsyncMock(return_value=payload),
                ) as fetch_json,
            ):
                info = await fetcher.fetch()

            fetch_json.assert_awaited_once_with("Creeper011", "milihu")
            self.assertIsNotNone(info)
            assert info is not None
            self.assertEqual(info.description, "Cool bot description")
            self.assertEqual(
                str(info.repository_url),
                "https://github.com/Creeper011/milihu",
            )

        asyncio.run(check())
