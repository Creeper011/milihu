"""Discord cog registry type definitions."""

from collections.abc import Callable

from discord.ext import commands

from milihu.discord.types.bot import BotClient

type CogFactory = Callable[[BotClient], commands.Cog]
type CogRegistry = dict[type[commands.Cog], CogFactory]
