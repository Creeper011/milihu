"""Cog registry type definitions."""

from collections.abc import Callable

from discord.ext import commands

type BotClient = commands.Bot | commands.AutoShardedBot
type CogFactory = Callable[[BotClient], commands.Cog]
type CogRegistry = dict[type[commands.Cog], CogFactory]
