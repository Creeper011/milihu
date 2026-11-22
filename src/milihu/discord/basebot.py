"""união flasco"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from discord import Intents
from discord.ext import commands
from discord.utils import MISSING
from pydantic import validate_call

from milihu.discord.cogfinder import CogFinder
from milihu.types.cogpath import CogPath
from milihu.types.default.prefix import Prefix
from milihu.types.token import Token


class MilihuBaseBot(commands.AutoShardedBot):
    """Defines the Milihu Bot Client."""

    @validate_call(config={"arbitrary_types_allowed": True})
    def __init__(
        self,
        command_prefix: Prefix | None = None,
        *,
        intents: Intents | None = None,
        **options: Any,
    ) -> None:
        if command_prefix is None:
            raise ValueError("command prefix is required")

        super().__init__(
            command_prefix=command_prefix,
            intents=intents or Intents.default(),
            **options,
        )

    @override
    async def setup_hook(self) -> None:
        """Synchronize application commands with Discord on login."""
        await self.tree.sync()

    async def load_cogs(
        self,
        cog_finder: CogFinder,
        loader: Callable[[CogPath], Awaitable[None]],
    ) -> None:
        """Load all cog paths discovered by ``cog_finder`` using ``loader``."""
        for cog_path in cog_finder:
            await loader(cog_path)

    @override
    @validate_call(config={"arbitrary_types_allowed": True})
    def run(
        self,
        token: Token,
        *,
        reconnect: bool = True,
        log_handler: logging.Handler | None = MISSING,
        log_formatter: logging.Formatter = MISSING,
        log_level: int = MISSING,
        root_logger: bool = False,
    ) -> None:
        """Start the bot using the provided Discord token."""
        super().run(
            token,
            reconnect=reconnect,
            log_handler=log_handler,
            log_formatter=log_formatter,
            log_level=log_level,
            root_logger=root_logger,
        )
