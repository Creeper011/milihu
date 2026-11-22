"""Discord bot client type definitions."""

from discord.ext import commands

type BotClient = commands.Bot | commands.AutoShardedBot
