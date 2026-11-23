import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from milihu.services.nodelink.runtime import NodeLinkRuntime


class NodeLinkRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_hardened_nodelink_image_with_ephemeral_password(self) -> None:
        runtime = NodeLinkRuntime(
            "ghcr.io/performanc/nodelink@sha256:test",
            spotify_client_id="sp-id",
            spotify_client_secret="sp-secret",
        )
        self.assertTrue(len(runtime.password) >= 24)

        run_args: tuple[str, ...] = ()

        async def docker(command: str, *args: str) -> str:
            nonlocal run_args
            if command == "build":
                self.assertIn(f"NODELINK_IMAGE={runtime.image}", args)
                context = Path(args[-1])
                self.assertTrue((context / "Dockerfile").is_file())
                return "sha256:seeded"
            if command == "run":
                run_args = args
                self.assertEqual(args[-1], "sha256:seeded")
                return "container-nl"
            return ""

        with (
            patch.object(runtime, "_docker", new=docker),
            patch.object(runtime.backend, "__aenter__", return_value=runtime.backend),
        ):
            async with runtime as backend:
                self.assertIs(backend, runtime.backend)

        self.assertIn("--read-only", run_args)
        self.assertIn("127.0.0.1:2333:3000", run_args)
        self.assertIn(f"NODELINK_SERVER_PASSWORD={runtime.password}", run_args)
        self.assertIn("NODELINK_API_ENABLETRACKSTREAMENDPOINT=true", run_args)
        self.assertIn("NODELINK_API_ENABLELOADSTREAMENDPOINT=true", run_args)
        self.assertIn("NODELINK_SOURCES_LOCAL_ENABLED=false", run_args)
        self.assertIn("NODELINK_SOURCES_HTTP_ENABLED=false", run_args)
        self.assertIn("NODELINK_SOURCES_SPOTIFY_EXTERNALAUTHURL=", run_args)
        self.assertIn("NODELINK_SOURCES_SPOTIFY_CLIENTID=sp-id", run_args)
        self.assertIn("NODELINK_SOURCES_SPOTIFY_CLIENTSECRET=sp-secret", run_args)

    async def test_start_failure_explains_why_nodelink_is_unavailable(self) -> None:
        runtime = NodeLinkRuntime("ghcr.io/performanc/nodelink@sha256:test")
        with patch.object(
            runtime,
            "_docker",
            new=AsyncMock(side_effect=RuntimeError("Docker daemon unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Docker daemon unavailable"):
                await runtime.__aenter__()

    async def test_docker_kills_subprocess_on_cancellation(self) -> None:
        runtime = NodeLinkRuntime("ghcr.io/performanc/nodelink@sha256:test")
        started = asyncio.Event()
        process = MagicMock()

        async def hang() -> tuple[bytes, bytes]:
            started.set()
            await asyncio.Future[None]()
            return b"", b""

        process.communicate = AsyncMock(side_effect=hang)
        process.wait = AsyncMock(return_value=-9)

        with patch(
            "milihu.services.nodelink.runtime.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            task = asyncio.create_task(runtime.__aenter__())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        process.kill.assert_called_once_with()
        process.wait.assert_awaited_once_with()

    async def test_aexit_stops_container_even_when_backend_close_fails(self) -> None:
        runtime = NodeLinkRuntime("ghcr.io/performanc/nodelink@sha256:test")
        runtime.container_id = "container-nl-123"
        docker_mock = AsyncMock(return_value="")

        with (
            patch.object(
                runtime.backend,
                "__aexit__",
                new=AsyncMock(side_effect=RuntimeError("session close failed")),
            ),
            patch.object(runtime, "_docker", new=docker_mock),
        ):
            with self.assertRaisesRegex(RuntimeError, "session close failed"):
                await runtime.__aexit__()

        docker_mock.assert_awaited_once_with("stop", "--time=1", "container-nl-123")
        self.assertIsNone(runtime.container_id)
