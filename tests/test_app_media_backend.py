import asyncio
import os
import signal
import unittest
from typing import Self, Unpack
from unittest.mock import AsyncMock, patch

from milihu.app.app import MilihuApp
from milihu.types.media import MediaResolveOptions, MediaResult
from milihu.usecases.faces.media import MediaBackend


class FakeMediaBackend:
    def __init__(self, events: list[str] | None = None) -> None:
        self.active = False
        self.events = events

    async def __aenter__(self) -> Self:
        self.active = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.active = False
        if self.events is not None:
            self.events.append("backend_closed")

    async def resolve(
        self, url: str, **options: Unpack[MediaResolveOptions]
    ) -> MediaResult:
        raise NotImplementedError


class FakeBot:
    def __init__(
        self,
        media_backend: FakeMediaBackend,
        *,
        block_on_start: bool = False,
        events: list[str] | None = None,
    ) -> None:
        self.media_backend = media_backend
        self.block_on_start = block_on_start
        self.events = events
        self.started = False
        self.closed = False
        self.ready = asyncio.Event()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True
        if self.events is not None:
            self.events.append("bot_closed")

    async def start(self, token: str) -> None:
        assert self.media_backend.active
        self.started = True
        self.ready.set()
        if self.block_on_start:
            await asyncio.Future[None]()


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

        async def load_cogs(
            backend: MediaBackend | None,
            audio_backend: object = None,
        ) -> None:
            self.assertTrue(media_backend.active)
            self.assertIs(backend, media_backend)
            self.assertIsNone(audio_backend)

        with patch.object(app, "_load_cogs", side_effect=load_cogs):
            await app.start()

        self.assertTrue(bot.started)
        self.assertFalse(media_backend.active)

    async def test_signals_shut_down_bot_and_backend_cleanly(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig.name):
                events: list[str] = []
                media_backend = FakeMediaBackend(events)
                bot = FakeBot(media_backend, block_on_start=True, events=events)
                with patch.object(MilihuApp, "_build_bot", return_value=bot):
                    app = MilihuApp(token="test", media_backend=media_backend)

                with (
                    patch.object(app, "_load_cogs", new=AsyncMock()),
                    self.assertLogs("MilihuApp", level="INFO") as logs,
                ):
                    task = asyncio.create_task(app.start())
                    await bot.ready.wait()
                    os.kill(os.getpid(), sig)
                    await task

                self.assertTrue(bot.closed)
                self.assertFalse(media_backend.active)
                self.assertEqual(events, ["bot_closed", "backend_closed"])
                joined = "\n".join(logs.output)
                self.assertIn(f"Received {sig.name}, shutting down", joined)
                self.assertIn("Discord bot shut down cleanly", joined)

    async def test_external_cancellation_closes_resources_and_reraises(self) -> None:
        media_backend = FakeMediaBackend()
        bot = FakeBot(media_backend, block_on_start=True)
        with patch.object(MilihuApp, "_build_bot", return_value=bot):
            app = MilihuApp(token="test", media_backend=media_backend)

        with patch.object(app, "_load_cogs", new=AsyncMock()):
            task = asyncio.create_task(app.start())
            await bot.ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(bot.closed)
        self.assertFalse(media_backend.active)

    def test_run_handles_keyboard_interrupt_without_traceback(self) -> None:
        bot = FakeBot(FakeMediaBackend())
        with patch.object(MilihuApp, "_build_bot", return_value=bot):
            app = MilihuApp(token="test")

        def raise_keyboard_interrupt(coro: object) -> None:
            if asyncio.iscoroutine(coro):
                coro.close()
            raise KeyboardInterrupt

        with (
            patch(
                "milihu.app.app.asyncio.run",
                side_effect=raise_keyboard_interrupt,
            ),
            self.assertLogs("MilihuApp", level="WARNING") as logs,
        ):
            app.run()

        self.assertIn("Shutdown interrupted", logs.output[0])
