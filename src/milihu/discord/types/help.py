"""Discord help category type definitions."""

from discord.ext import commands

from milihu.types.help import HelpCategories, HelpCategory


class CogHelpCategory(HelpCategory[type[commands.Cog]]):
    """Help category bound to Discord cog classes."""


type HelpCategoryRegistry = HelpCategories[type[commands.Cog]]
