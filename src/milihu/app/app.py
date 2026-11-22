import asyncio
import logging
import signal
from contextlib import AbstractAsyncContextManager, AsyncExitStack

from discord import CustomActivity, Intents, Status

from milihu.app.registry import build_cog_registry
from milihu.discord.basebot import MilihuBaseBot
from milihu.discord.cogfinder import CogFinder
from milihu.discord.loader import CogLoader
from milihu.types.default.cogs import DEFAULT_COGS_DIR, DEFAULT_COGS_PACKAGE
from milihu.types.default.prefix import DEFAULT_PREFIX, Prefix
from milihu.types.token import Token
from milihu.usecases.faces.media import MediaBackend


class MilihuApp:
    """Application lifecycle for the Milihu bot."""

    def __init__(
        self,
        token: Token,
        prefix: Prefix = DEFAULT_PREFIX,
        *,
        media_backend: AbstractAsyncContextManager[MediaBackend] | None = None,
    ) -> None:
        self.token = token
        self.prefix = prefix
        self.media_backend = media_backend
        self.logger = self._configure_logging()
        self.bot = self._build_bot()

    def _configure_logging(self) -> logging.Logger:
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger(self.__class__.__name__)

    def _build_bot(self) -> MilihuBaseBot:
        self.logger.info("Building Discord bot")
        intents = Intents.default()
        intents.message_content = True
        return MilihuBaseBot(
            command_prefix=self.prefix,
            intents=intents,
            status=Status.online,
            activity=CustomActivity(name="🌙 yeaa, idk what i'm doing"),
        )

    async def _load_cogs(self, media_backend: MediaBackend | None) -> None:
        finder = CogFinder(DEFAULT_COGS_DIR, package=DEFAULT_COGS_PACKAGE)
        loader = CogLoader(self.bot, finder, build_cog_registry(media_backend))
        await self.bot.load_cogs(finder, loader.load)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        shutdown_requested = asyncio.Event()
        registered_signals: list[signal.Signals] = []

        def _handle_shutdown(sig: signal.Signals) -> None:
            if shutdown_requested.is_set():
                return
            shutdown_requested.set()
            self.logger.info("Received %s, shutting down", sig.name)
            for registered in registered_signals:
                loop.remove_signal_handler(registered)
            registered_signals.clear()
            if task is not None and not task.done():
                task.cancel()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_shutdown, sig)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
            registered_signals.append(sig)

        try:
            async with AsyncExitStack() as stack:
                media_backend = None
                if self.media_backend is not None:
                    try:
                        media_backend = await stack.enter_async_context(
                            self.media_backend
                        )
                    except Exception as exc:
                        self.logger.error("Media backend unavailable: %s", exc)
                        raise
                await stack.enter_async_context(self.bot)
                await self._load_cogs(media_backend)
                self.logger.info("Starting Discord bot")
                await self.bot.start(self.token)
        except asyncio.CancelledError:
            if not shutdown_requested.is_set():
                raise
            if task is not None and task.uncancel() > 0:
                raise
            self.logger.info("Discord bot shut down cleanly")
        finally:
            for registered in registered_signals:
                loop.remove_signal_handler(registered)

    def run(self) -> None:
        try:
            asyncio.run(self.start())
        except KeyboardInterrupt:
            self.logger.warning("Shutdown interrupted")
