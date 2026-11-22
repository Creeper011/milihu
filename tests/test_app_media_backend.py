import unittest
from typing import Self, Unpack
from unittest.mock import patch

from milihu.app.app import MilihuApp
from milihu.services.faces.media import MediaBackend
from milihu.types.media import MediaResolveOptions, MediaResult


class FakeMediaBackend:
    def __init__(self) -> None:
        self.active = False

    async def __aenter__(self) -> Self:
        self.active = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.active = False

    async def resolve(
        self, url: str, **options: Unpack[MediaResolveOptions]
    ) -> MediaResult:
        raise NotImplementedError


class FakeBot:
    def __init__(self, media_backend: FakeMediaBackend) -> None:
        self.media_backend = media_backend
        self.started = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def start(self, token: str) -> None:
        assert self.media_backend.active
        self.started = True


class AppMediaBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_failure_is_logged_and_bot_does_not_start(self) -> None:
        class FailingBackend:
            async def __aenter__(self) -> MediaBackend:
                raise RuntimeError("Cobalt did not become ready")

            async def __aexit__(self, *_: object) -> None:
                pass

        bot = FakeBot(FakeMediaBackend())
        with patch.object(MilihuApp, "_build_bot", return_value=bot):
            app = MilihuApp(token="test", media_backend=FailingBackend())

        with self.assertLogs("MilihuApp", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "Cobalt did not become ready"):
                await app.start()

        self.assertIn("Cobalt did not become ready", logs.output[0])
        self.assertFalse(bot.started)

    async def test_media_backend_lifecycle_follows_bot(self) -> None:
        media_backend = FakeMediaBackend()
        bot = FakeBot(media_backend)
        with patch.object(MilihuApp, "_build_bot", return_value=bot):
            app = MilihuApp(token="test", media_backend=media_backend)

        async def load_cogs(backend: MediaBackend | None) -> None:
            self.assertTrue(media_backend.active)
            self.assertIs(backend, media_backend)

        with patch.object(app, "_load_cogs", side_effect=load_cogs):
            await app.start()

        self.assertTrue(bot.started)
        self.assertFalse(media_backend.active)
