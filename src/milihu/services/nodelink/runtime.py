"""Manage the local NodeLink container for the bot's lifetime."""

import asyncio
import contextlib
import secrets
from pathlib import Path

from aiohttp import ClientError

from milihu.services.nodelink.client import NodeLinkClient

NODELINK_URL = "http://127.0.0.1:2333/"
NODELINK_CONTAINER_PORT = 3000


class NodeLinkRuntime:
    """Build and supervise a hardened local NodeLink Docker container."""

    def __init__(
        self,
        image: str,
        *,
        password: str | None = None,
        spotify_client_id: str | None = None,
        spotify_client_secret: str | None = None,
    ) -> None:
        self.image = image
        self.password = password or secrets.token_urlsafe(24)
        self.spotify_client_id = (spotify_client_id or "").strip()
        self.spotify_client_secret = (spotify_client_secret or "").strip()
        self.container_id: str | None = None
        self.backend = NodeLinkClient(NODELINK_URL, self.password)

    @staticmethod
    async def _docker(*args: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Cannot start NodeLink: Docker is unavailable: {exc}"
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
            raise RuntimeError(f"Cannot start NodeLink: {detail or 'Docker failed'}")
        return (stdout or stderr).decode(errors="replace").strip()

    def _build_env_args(self) -> list[str]:
        env_map: dict[str, str] = {
            "NODELINK_SERVER_HOST": "0.0.0.0",
            "NODELINK_SERVER_PORT": str(NODELINK_CONTAINER_PORT),
            "NODELINK_SERVER_PASSWORD": self.password,
            "NODELINK_CLUSTER_ENABLED": "false",
            "NODELINK_CACHE_DISKENABLED": "false",
            "NODELINK_LOGGING_FILE_ENABLED": "false",
            "NODELINK_LOGGING_LEVEL": "warn",
            "NODELINK_SOURCEHEALTH_ACTIVEPROBES_ENABLED": "false",
            "NODELINK_API_ENABLETRACKSTREAMENDPOINT": "true",
            "NODELINK_API_ENABLELOADSTREAMENDPOINT": "true",
            "NODELINK_SOURCES_LOCAL_ENABLED": "false",
            "NODELINK_SOURCES_HTTP_ENABLED": "false",
            "NODELINK_SOURCES_FLOWERY_ENABLED": "false",
            "NODELINK_SOURCES_LAZYPYTTS_ENABLED": "false",
            "NODELINK_SOURCES_SPOTIFY_EXTERNALAUTHURL": "",
        }
        if self.spotify_client_id and self.spotify_client_secret:
            env_map["NODELINK_SOURCES_SPOTIFY_CLIENTID"] = self.spotify_client_id
            env_map["NODELINK_SOURCES_SPOTIFY_CLIENTSECRET"] = (
                self.spotify_client_secret
            )
        args: list[str] = []
        for key, value in env_map.items():
            args.extend(("--env", f"{key}={value}"))
        return args

    async def __aenter__(self) -> NodeLinkClient:
        image = await self._docker(
            "build",
            "--quiet",
            "--load",
            "--build-arg",
            f"NODELINK_IMAGE={self.image}",
            str(Path(__file__).with_name("nodelink-image")),
        )
        self.container_id = await self._docker(
            "run",
            "--detach",
            "--rm",
            "--init",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/app/.nodelink",
            "--tmpfs",
            "/app/.cache",
            "--pull=never",
            "--publish",
            f"127.0.0.1:2333:{NODELINK_CONTAINER_PORT}",
            *self._build_env_args(),
            image,
        )
        try:
            for _ in range(60):
                try:
                    return await self.backend.__aenter__()
                except (ClientError, TimeoutError, RuntimeError):
                    await asyncio.sleep(0.25)
            logs = await self._docker("logs", "--tail", "20", self.container_id)
            raise RuntimeError(f"NodeLink did not become ready: {logs}")
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
