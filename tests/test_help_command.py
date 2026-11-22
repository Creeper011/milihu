"""Checks the public Components v2 command guide."""

import asyncio
import unittest
from typing import Any
from unittest.mock import Mock

from discord import Intents
from discord.ui import Select
from pydantic import ValidationError

from milihu.app.registry import HELP_CATEGORIES, build_cog_registry
from milihu.discord.basebot import MilihuBaseBot
from milihu.discord.cogs.download import DownloadCog
from milihu.discord.cogs.eval import EvalCog
from milihu.discord.cogs.help import (
    HelpCog,
    _HelpView,  # pyright: ignore[reportPrivateUsage]
    _parse_help_page,  # pyright: ignore[reportPrivateUsage]
)
from milihu.discord.cogs.music import MusicCog
from milihu.discord.cogs.openpoll import OpenpollCog
from milihu.discord.types.help import CogHelpCategory
from milihu.types.help import HelpCategoryKey


class HelpCommandTests(unittest.TestCase):
    def test_registry_wires_help_cog_with_categories(self) -> None:
        bot = MilihuBaseBot(command_prefix="m$", intents=Intents.default())
        registry = build_cog_registry(None)
        self.assertIn(HelpCog, registry)
        cog = registry[HelpCog](bot)
        self.assertIsInstance(cog, HelpCog)
        assert isinstance(cog, HelpCog)
        self.assertIs(cog.categories, HELP_CATEGORIES)

    def test_home_and_categories_render_in_english_and_exclude_eval(self) -> None:
        async def check() -> None:
            bot = MilihuBaseBot(command_prefix="m$", intents=Intents.default())
            await bot.add_cog(HelpCog(bot, categories=HELP_CATEGORIES))
            await bot.add_cog(OpenpollCog(bot))
            await bot.add_cog(DownloadCog(bot, Mock()))
            await bot.add_cog(MusicCog(bot, Mock()))
            await bot.add_cog(EvalCog(bot))

            home_view = _HelpView(bot, "m$", HELP_CATEGORIES)
            home_components = home_view.to_components()[0]["components"]
            self.assertEqual(home_components[0]["content"], "### 🌙 Milihu Help")
            home_body = home_components[1]["content"]
            self.assertIn("🖼️ Media — browse commands in the menu below.", home_body)
            self.assertIn("🎵 Music — browse commands in the menu below.", home_body)
            self.assertIn("🧩 Utility — browse commands in the menu below.", home_body)
            self.assertEqual(
                home_components[3]["content"],
                "-# Use `/help` or `m$help` to open this help menu.",
            )

            select = next(
                item for item in home_view.walk_children() if isinstance(item, Select)
            )
            self.assertEqual(select.placeholder, "Choose a category")
            self.assertEqual(
                [option.label for option in select.options],
                ["Home", "Media", "Music", "Utility"],
            )

            for category, expected_heading, expected_command in (
                (HelpCategoryKey.MEDIA, "### 🖼️ Media", "`/download` · `m$download`"),
                (
                    HelpCategoryKey.MUSIC,
                    "### 🎵 Music",
                    "`/music play` · `m$music play`",
                ),
                (HelpCategoryKey.UTILITY, "### 🧩 Utility", "`/help` · `m$help`"),
            ):
                view = _HelpView(bot, "m$", HELP_CATEGORIES, category)
                components = view.to_components()[0]["components"]
                self.assertEqual(components[0]["content"], expected_heading)
                self.assertIn(expected_command, components[1]["content"])
                self.assertNotIn("m$ev", components[1]["content"])

            self.assertIsNone(bot.help_command)

        asyncio.run(check())

    def test_cog_help_category_validates_fields(self) -> None:
        with self.assertRaises(ValidationError):
            CogHelpCategory(label="Media", emoji="🖼️", items=())

        bad_items: Any = (int,)
        with self.assertRaises(ValidationError):
            CogHelpCategory(label="Media", emoji="🖼️", items=bad_items)

        with self.assertRaises(ValidationError):
            CogHelpCategory(label="   ", emoji="🖼️", items=(HelpCog,))

    def test_parse_help_page_falls_back_to_home(self) -> None:
        self.assertEqual(_parse_help_page("home", HELP_CATEGORIES), "home")
        self.assertEqual(
            _parse_help_page("media", HELP_CATEGORIES), HelpCategoryKey.MEDIA
        )
        self.assertEqual(_parse_help_page("unknown", HELP_CATEGORIES), "home")
