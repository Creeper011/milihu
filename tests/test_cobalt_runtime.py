import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from milihu.services.cobalt.runtime import CobaltRuntime


class CobaltRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_the_patched_cobalt_image(self) -> None:
        runtime = CobaltRuntime("ghcr.io/imputnet/cobalt@sha256:test")

        async def docker(command: str, *args: str) -> str:
            if command == "build":
                self.assertIn(f"COBALT_IMAGE={runtime.image}", args)
                context = Path(args[-1])
                self.assertTrue((context / "Dockerfile").is_file())
                self.assertTrue((context / "youtube-client" / "package.json").is_file())
                return "sha256:patched"
            if command == "run":
                self.assertEqual(args[-1], "sha256:patched")
                return "container"
            return ""

        with (
            patch.object(runtime, "_docker", new=docker),
            patch.object(runtime.backend, "__aenter__", return_value=runtime.backend),
        ):
            async with runtime as backend:
                self.assertIs(backend, runtime.backend)

    async def test_start_failure_explains_why_cobalt_is_unavailable(self) -> None:
        runtime = CobaltRuntime("ghcr.io/imputnet/cobalt@sha256:test")
        with patch.object(
            runtime,
            "_docker",
            new=AsyncMock(side_effect=RuntimeError("Docker daemon unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Docker daemon unavailable"):
                await runtime.__aenter__()

    async def test_docker_kills_subprocess_on_cancellation(self) -> None:
        runtime = CobaltRuntime("ghcr.io/imputnet/cobalt@sha256:test")
        started = asyncio.Event()
        process = MagicMock()

        async def hang() -> tuple[bytes, bytes]:
            started.set()
            await asyncio.Future[None]()
            return b"", b""

        process.communicate = AsyncMock(side_effect=hang)
        process.wait = AsyncMock(return_value=-9)

        with patch(
            "milihu.services.cobalt.runtime.asyncio.create_subprocess_exec",
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
        runtime = CobaltRuntime("ghcr.io/imputnet/cobalt@sha256:test")
        runtime.container_id = "container-123"
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

        docker_mock.assert_awaited_once_with("stop", "--time=1", "container-123")
        self.assertIsNone(runtime.container_id)
