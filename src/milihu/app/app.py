import asyncio
import logging
from contextlib import AbstractAsyncContextManager, AsyncExitStack

from discord import Intents

from milihu.discord.basebot import MilihuBaseBot
from milihu.discord.cogfinder import CogFinder
from milihu.discord.loader import CogLoader
from milihu.discord.registry import build_cog_registry
from milihu.services.faces.media import MediaBackend
from milihu.types.default.cogs import DEFAULT_COGS_DIR, DEFAULT_COGS_PACKAGE
from milihu.types.default.prefix import DEFAULT_PREFIX, Prefix
from milihu.types.token import Token


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
        return MilihuBaseBot(command_prefix=self.prefix, intents=intents)

    async def _load_cogs(self, media_backend: MediaBackend | None) -> None:
        finder = CogFinder(DEFAULT_COGS_DIR, package=DEFAULT_COGS_PACKAGE)
        loader = CogLoader(self.bot, finder, build_cog_registry(media_backend))
        await self.bot.load_cogs(finder, loader.load)

    async def start(self) -> None:
        async with AsyncExitStack() as stack:
            media_backend = None
            if self.media_backend is not None:
                try:
                    media_backend = await stack.enter_async_context(self.media_backend)
                except Exception as exc:
                    self.logger.error("Cobalt unavailable: %s", exc)
                    raise
            await stack.enter_async_context(self.bot)
            await self._load_cogs(media_backend)
            self.logger.info("Starting Discord bot")
            await self.bot.start(self.token)

    def run(self) -> None:
        asyncio.run(self.start())
