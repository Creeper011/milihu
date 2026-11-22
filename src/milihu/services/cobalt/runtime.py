"""Manage the local Cobalt container for the bot's lifetime."""

import asyncio
import contextlib
from pathlib import Path

from aiohttp import ClientError

from milihu.services.cobalt.client import Cobalt

COBALT_URL = "http://127.0.0.1:9000/"


class CobaltRuntime:
    def __init__(self, image: str) -> None:
        self.image = image
        self.container_id: str | None = None
        self.backend = Cobalt(COBALT_URL)

    @staticmethod
    async def _docker(*args: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Cannot start Cobalt: Docker is unavailable: {exc}"
            ) from exc
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"Cannot start Cobalt: {detail or 'Docker failed'}")
        return (stdout or stderr).decode(errors="replace").strip()

    async def __aenter__(self) -> Cobalt:
        image = await self._docker(
            "build",
            "--quiet",
            "--load",
            "--build-arg",
            f"COBALT_IMAGE={self.image}",
            str(Path(__file__).with_name("cobalt-image")),
        )
        self.container_id = await self._docker(
            "run", "--detach", "--rm", "--init", "--read-only",
            "--pull=never", "--publish", "127.0.0.1:9000:9000",
            "--env", f"API_URL={COBALT_URL}", image,
        )
        try:
            for _ in range(40):
                try:
                    return await self.backend.__aenter__()
                except (ClientError, TimeoutError, RuntimeError):
                    await asyncio.sleep(0.25)
            logs = await self._docker("logs", "--tail", "20", self.container_id)
            raise RuntimeError(f"Cobalt did not become ready: {logs}")
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_: object) -> None:
        try:
            await self.backend.__aexit__()
        finally:
            if self.container_id is not None:
                with contextlib.suppress(RuntimeError):
                    await self._docker("stop", "--time=1", self.container_id)
                self.container_id = None
