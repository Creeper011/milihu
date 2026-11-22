"""Cog loader."""

import importlib
import inspect
from collections.abc import Mapping

from discord.ext import commands

from milihu.discord.cogfinder import CogFinder
from milihu.types.cogpath import CogPath
from milihu.types.registry import BotClient, CogFactory


class CogLoader:
    """Loads cogs discovered by ``CogFinder`` using the ``registry`` factories."""

    def __init__(
        self,
        bot: BotClient,
        finder: CogFinder,
        registry: Mapping[type[commands.Cog], CogFactory],
    ) -> None:
        self.bot = bot
        self.finder = finder
        self.registry = registry

    async def load_all(self) -> None:
        """Discover and load all cogs."""
        for cog_path in self.finder:
            await self.load(cog_path)

    async def load(self, cog_path: CogPath) -> None:
        """Import ``cog_path`` and register its ``commands.Cog`` subclasses."""
        module = importlib.import_module(cog_path)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, commands.Cog)
                and obj is not commands.Cog
                and obj.__module__ == module.__name__
            ):
                factory = self.registry.get(obj, obj)
                await self.bot.add_cog(factory(self.bot))
