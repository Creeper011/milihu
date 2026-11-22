import unittest
from unittest.mock import AsyncMock, patch

from milihu.services.core.cobalt_runtime import CobaltRuntime


class CobaltRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_the_patched_cobalt_image(self) -> None:
        runtime = CobaltRuntime("ghcr.io/imputnet/cobalt@sha256:test")

        async def docker(command: str, *args: str) -> str:
            if command == "build":
                self.assertIn(f"COBALT_IMAGE={runtime.image}", args)
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
