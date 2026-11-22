"""Checks the Components v2 /about (m$ab) command."""

import asyncio
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from discord import AllowedMentions, Intents
from discord.ext import commands
from discord.ui import LayoutView

from milihu.app.registry import HELP_CATEGORIES, build_cog_registry
from milihu.discord.basebot import MilihuBaseBot
from milihu.discord.cogs.about import (
    AboutCog,
    _build_about_view,  # pyright: ignore[reportPrivateUsage]
)
from milihu.discord.types.bot import BotClient
from milihu.types.default.project import CREATOR_USER_ID
from milihu.types.help import HelpCategoryKey
from milihu.types.project import ProjectOutput


class _FakeProjectProvider:
    def __init__(self, output: ProjectOutput) -> None:
        self._output = output

    async def get_project(self) -> ProjectOutput:
        return self._output


def _collect_text(components: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in components:
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
        nested = item.get("components")
        if isinstance(nested, list):
            parts.append(_collect_text(cast(list[dict[str, Any]], nested)))
    return "\n".join(parts)


class AboutCommandTests(unittest.TestCase):
    def test_registry_wires_about_cog_and_command_registers_alias(self) -> None:
        async def check() -> None:
            bot = MilihuBaseBot(command_prefix="m$", intents=Intents.default())
            registry = build_cog_registry(None)
            self.assertIn(AboutCog, registry)
            cog = registry[AboutCog](bot)
            self.assertIsInstance(cog, AboutCog)
            await bot.add_cog(cog)

            command = bot.get_command("about")
            self.assertIsInstance(command, commands.HybridCommand)
            self.assertIs(bot.get_command("ab"), command)
            self.assertIn(AboutCog, HELP_CATEGORIES[HelpCategoryKey.UTILITY].items)

        asyncio.run(check())

    def test_about_replies_with_components_v2_remote_description_and_mention(
        self,
    ) -> None:
        async def check() -> None:
            bot = MilihuBaseBot(command_prefix="m$", intents=Intents.default())
            provider = _FakeProjectProvider(
                ProjectOutput(
                    description="Discord bot from GitHub remote.",
                    stack=("Python 3.13.12", "discord.py 2.7.1"),
                )
            )
            cog = AboutCog(bot, project=provider)
            await bot.add_cog(cog)
            ctx = Mock()
            ctx.reply = AsyncMock()

            await cog.about(cast(commands.Context[BotClient], ctx))

            ctx.reply.assert_awaited_once()
            kwargs = ctx.reply.await_args.kwargs
            view = kwargs["view"]
            self.assertIsInstance(view, LayoutView)
            allowed = kwargs["allowed_mentions"]
            self.assertEqual(allowed.to_dict(), AllowedMentions.none().to_dict())

            text = _collect_text(view.to_components())
            self.assertIn("### 🌙 About Milihu", text)
            self.assertIn("Discord bot from GitHub remote.", text)
            self.assertIn(f"<@{CREATOR_USER_ID}>", text)
            self.assertIn("**Ping:** N/A", text)
            self.assertIn("**Stack:** Python 3.13.12 · discord.py 2.7.1", text)
            self.assertIn("`m$help`", text)

        asyncio.run(check())

    def test_about_view_uses_section_thumbnail_and_omits_missing_description(
        self,
    ) -> None:
        async def check() -> None:
            bot = Mock()
            bot.user.display_avatar.url = "https://cdn.discordapp.com/avatars/1/a.png"
            bot.guilds = [object(), object()]
            bot.latency = 0.042
            project = ProjectOutput(
                description=None,
                stack=("Python 3.13.12",),
            )

            view = _build_about_view(cast(BotClient, bot), "m$", project)
            container_children = view.to_components()[0]["components"]
            section = container_children[0]
            self.assertEqual(
                section["accessory"]["media"]["url"],
                "https://cdn.discordapp.com/avatars/1/a.png",
            )
            self.assertEqual(len(section["components"]), 1)
            text = _collect_text(view.to_components())
            self.assertIn("**Servers:** 2 · **Ping:** 42 ms", text)
            self.assertIn("**Stack:** Python 3.13.12", text)
            self.assertIn("<@713544201828040786>", text)

        asyncio.run(check())
